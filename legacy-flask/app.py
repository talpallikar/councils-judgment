"""MTG Art Duel — pick your favorite art, one card at a time.

Flask backend: serves the single-page frontend, proxies/caches Scryfall,
records votes in SQLite, and keeps per-format staple lists fresh from
MTGTop8 / EDHREC (see staple_sources.py).
"""

import json
import math
import os
import random
import sqlite3
import threading
import time

import requests
from flask import Flask, abort, g, jsonify, request

import staple_sources
from staples import FORMATS, STAPLES

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("MTGART_DB", os.path.join(BASE, "mtgart.db"))

SCRYFALL = "https://api.scryfall.com"
USER_AGENT = "MTGArtDuel/1.0 (local hobby app)"

CARD_TTL = 7 * 86400      # cached unique-art printings per card
LIST_TTL = 7 * 86400      # staple lists per format
ELO_K = 32
MATCHUP_TRIES = 10

FORMAT_KEYS = [k for k, _ in FORMATS]
FORMAT_LABELS = dict(FORMATS)

app = Flask(__name__, static_folder="static", static_url_path="")

http = requests.Session()
http.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

_scry_lock = threading.Lock()
_scry_last = [0.0]

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_cache(
  card_name TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS staple_lists(
  format TEXT PRIMARY KEY,
  names TEXT NOT NULL,
  source TEXT NOT NULL,
  fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS arts(
  illustration_id TEXT PRIMARY KEY,
  card_name TEXT NOT NULL,
  artist TEXT,
  set_name TEXT,
  set_code TEXT,
  released_at TEXT,
  art_url TEXT,
  full_url TEXT,
  wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0,
  elo REAL NOT NULL DEFAULT 1500
);
CREATE TABLE IF NOT EXISTS votes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  format TEXT NOT NULL,
  card_name TEXT NOT NULL,
  winner_id TEXT NOT NULL,
  loser_id TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_votes_card ON votes(card_name);
CREATE INDEX IF NOT EXISTS idx_votes_format ON votes(format);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------- Scryfall

def scry_get(path_or_url, params=None):
    """Rate-limited GET against Scryfall (they ask for ~100ms spacing)."""
    with _scry_lock:
        wait = 0.12 - (time.monotonic() - _scry_last[0])
        if wait > 0:
            time.sleep(wait)
        _scry_last[0] = time.monotonic()
    url = path_or_url if path_or_url.startswith("http") else SCRYFALL + path_or_url
    r = http.get(url, params=params, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _extract_art(card, wanted_name):
    """One art entry from a Scryfall card object, or None."""
    face = None
    if card.get("card_faces"):
        lowered = wanted_name.lower()
        face = next(
            (f for f in card["card_faces"] if f.get("name", "").lower() == lowered),
            card["card_faces"][0])
    src = face if face and face.get("image_uris") else card
    imgs = src.get("image_uris") or {}
    ill = (face or {}).get("illustration_id") or card.get("illustration_id")
    if not ill or "art_crop" not in imgs or "normal" not in imgs:
        return None
    return {
        "id": ill,
        "artist": (face or {}).get("artist") or card.get("artist"),
        "set_name": card.get("set_name"),
        "set_code": card.get("set"),
        "released_at": card.get("released_at"),
        "art": imgs["art_crop"],
        "full": imgs["normal"],
    }


def fetch_prints(name):
    """All unique-art printings of a card, oldest first."""
    params = {
        "q": f'!"{name}" -is:digital',
        "unique": "art",
        "order": "released",
        "dir": "asc",
        "include_extras": "true",
    }
    data = scry_get("/cards/search", params)
    prints, seen, pages = [], set(), 0
    while data:
        for card in data.get("data", []):
            entry = _extract_art(card, name)
            if entry and entry["id"] not in seen:
                seen.add(entry["id"])
                prints.append(entry)
        pages += 1
        if data.get("has_more") and pages < 3:
            data = scry_get(data["next_page"])
        else:
            break
    return prints


def get_prints(db, name):
    """Cached unique-art printings; refreshes after CARD_TTL."""
    row = db.execute(
        "SELECT payload, fetched_at FROM card_cache WHERE card_name=?", (name,)
    ).fetchone()
    if row and time.time() - row["fetched_at"] < CARD_TTL:
        return json.loads(row["payload"])
    try:
        prints = fetch_prints(name)
    except requests.RequestException as exc:
        app.logger.warning("Scryfall fetch failed for %r: %s", name, exc)
        return json.loads(row["payload"]) if row else []
    db.execute(
        "INSERT INTO card_cache(card_name, payload, fetched_at) VALUES(?,?,?) "
        "ON CONFLICT(card_name) DO UPDATE SET payload=excluded.payload, "
        "fetched_at=excluded.fetched_at",
        (name, json.dumps(prints), time.time()))
    db.commit()
    return prints


# ------------------------------------------------------- staple list refresh

_refreshing = set()
_refresh_lock = threading.Lock()


def _store_list(conn, fmt, names, source):
    conn.execute(
        "INSERT INTO staple_lists(format, names, source, fetched_at) VALUES(?,?,?,?) "
        "ON CONFLICT(format) DO UPDATE SET names=excluded.names, "
        "source=excluded.source, fetched_at=excluded.fetched_at",
        (fmt, json.dumps(names), source, time.time()))
    conn.commit()


def refresh_format(fmt, conn=None):
    """Pull a fresh staple list from its live source. Returns names or None."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        names, source = staple_sources.fetch_for(http, fmt)
        if len(names) < staple_sources.MIN_ACCEPT:
            app.logger.warning(
                "staple refresh for %s yielded only %d names; keeping old list",
                fmt, len(names))
            return None
        _store_list(conn, fmt, names, source)
        app.logger.info("staples refreshed: %s (%d cards from %s)", fmt, len(names), source)
        return names
    except Exception as exc:
        app.logger.warning("staple refresh failed for %s: %s", fmt, exc)
        return None
    finally:
        if own:
            conn.close()


def kick_refresh(fmt):
    """Refresh a format's staples in the background, at most once at a time."""
    with _refresh_lock:
        if fmt in _refreshing:
            return
        _refreshing.add(fmt)

    def run():
        try:
            refresh_format(fmt)
        finally:
            with _refresh_lock:
                _refreshing.discard(fmt)

    threading.Thread(target=run, daemon=True).start()


def scryfall_popular(fmt):
    """Popularity-ordered fallback when no live source and no seed exists."""
    data = scry_get("/cards/search", {
        "q": f"format:{fmt} -is:digital",
        "order": "edhrec",
        "unique": "cards",
    })
    if not data:
        return []
    return [c["name"].split(" //")[0] for c in data.get("data", [])][:75]


def get_format_list(db, fmt):
    row = db.execute(
        "SELECT names, fetched_at FROM staple_lists WHERE format=?", (fmt,)
    ).fetchone()
    if row:
        names = json.loads(row["names"])
        if time.time() - row["fetched_at"] >= LIST_TTL:
            kick_refresh(fmt)
        if names:
            return names
    seed = STAPLES.get(fmt) or []
    if seed:
        kick_refresh(fmt)
        return seed
    # No stored list and no seed (fresh Standard install): block on a live
    # fetch once, falling back to Scryfall popularity order.
    names = refresh_format(fmt, conn=db)
    if names:
        return names
    names = scryfall_popular(fmt)
    if names:
        _store_list(db, fmt, names, "scryfall.com popularity (fallback)")
        return names
    abort(503, description="No staple list available for this format")


def refresh_all(formats=None, verbose=False):
    """Force-refresh staple lists; used by update_staples.py."""
    init_db()
    results = {}
    for fmt in formats or FORMAT_KEYS:
        names = refresh_format(fmt)
        results[fmt] = len(names) if names else None
        if verbose:
            status = f"{len(names)} cards" if names else "FAILED (kept previous list)"
            print(f"{FORMAT_LABELS.get(fmt, fmt):>10}: {status}")
    return results


# ------------------------------------------------------------------ routes

@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/formats")
def api_formats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) c FROM votes").fetchone()["c"]
    lists = {
        r["format"]: r
        for r in db.execute("SELECT format, names, source, fetched_at FROM staple_lists")
    }
    out = []
    for key, label in FORMATS:
        row = lists.get(key)
        out.append({
            "key": key,
            "label": label,
            "cards": len(json.loads(row["names"])) if row else len(STAPLES.get(key) or []),
            "source": row["source"] if row else "built-in seed list",
            "updated_at": row["fetched_at"] if row else None,
        })
    return jsonify({"formats": out, "total_votes": total})


@app.get("/api/matchup")
def api_matchup():
    fmt = request.args.get("format", "")
    if fmt not in FORMAT_KEYS:
        abort(400, description="unknown format")
    db = get_db()
    names = get_format_list(db, fmt)
    pool = random.sample(names, min(MATCHUP_TRIES, len(names)))
    for name in pool:
        prints = get_prints(db, name)
        if len(prints) >= 2:
            pair = random.sample(prints, 2)
            return jsonify({
                "format": fmt,
                "card": name,
                "total_arts": len(prints),
                "arts": pair,
            })
    abort(503, description="Could not find a card with two arts; try again")


@app.post("/api/vote")
def api_vote():
    data = request.get_json(silent=True) or {}
    fmt = data.get("format")
    card_name = data.get("card")
    winner_id = data.get("winner")
    loser_id = data.get("loser")
    if fmt not in FORMAT_KEYS or not card_name or not winner_id or not loser_id \
            or winner_id == loser_id:
        abort(400, description="bad vote payload")

    db = get_db()
    row = db.execute(
        "SELECT payload FROM card_cache WHERE card_name=?", (card_name,)
    ).fetchone()
    if not row:
        abort(400, description="unknown card")
    by_id = {p["id"]: p for p in json.loads(row["payload"])}
    if winner_id not in by_id or loser_id not in by_id:
        abort(400, description="art does not belong to this card")

    # Ensure both arts exist in the arts table (metadata from our own cache,
    # never from the client).
    for art_id in (winner_id, loser_id):
        p = by_id[art_id]
        db.execute(
            "INSERT INTO arts(illustration_id, card_name, artist, set_name, "
            "set_code, released_at, art_url, full_url) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(illustration_id) DO NOTHING",
            (art_id, card_name, p.get("artist"), p.get("set_name"),
             p.get("set_code"), p.get("released_at"), p.get("art"), p.get("full")))

    w = db.execute("SELECT * FROM arts WHERE illustration_id=?", (winner_id,)).fetchone()
    l = db.execute("SELECT * FROM arts WHERE illustration_id=?", (loser_id,)).fetchone()
    expected_w = 1.0 / (1.0 + 10 ** ((l["elo"] - w["elo"]) / 400.0))
    delta = ELO_K * (1.0 - expected_w)
    db.execute(
        "UPDATE arts SET wins=wins+1, elo=elo+? WHERE illustration_id=?",
        (delta, winner_id))
    db.execute(
        "UPDATE arts SET losses=losses+1, elo=elo-? WHERE illustration_id=?",
        (delta, loser_id))
    db.execute(
        "INSERT INTO votes(format, card_name, winner_id, loser_id, created_at) "
        "VALUES(?,?,?,?,?)",
        (fmt, card_name, winner_id, loser_id, time.time()))
    db.commit()

    agree = db.execute(
        "SELECT COUNT(*) c FROM votes WHERE card_name=? AND winner_id=? AND loser_id=?",
        (card_name, winner_id, loser_id)).fetchone()["c"]
    disagree = db.execute(
        "SELECT COUNT(*) c FROM votes WHERE card_name=? AND winner_id=? AND loser_id=?",
        (card_name, loser_id, winner_id)).fetchone()["c"]
    card_votes = db.execute(
        "SELECT COUNT(*) c FROM votes WHERE card_name=?", (card_name,)).fetchone()["c"]
    return jsonify({
        "ok": True,
        "pair": {
            "with_you": agree,
            "against_you": disagree,
            "agree_pct": round(100.0 * agree / (agree + disagree)),
        },
        "card_votes": card_votes,
    })


def _wilson(wins, games, z=1.96):
    """Lower bound of the Wilson score interval — ranks by win rate while
    penalizing small samples."""
    if games == 0:
        return 0.0
    p = wins / games
    denom = 1 + z * z / games
    center = p + z * z / (2 * games)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * games)) / games)
    return (center - spread) / denom


def _art_meta(db, ids):
    metas = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in db.execute(
                f"SELECT * FROM arts WHERE illustration_id IN ({marks})", chunk):
            metas[r["illustration_id"]] = dict(r)
    return metas


@app.get("/api/stats")
def api_stats():
    fmt = request.args.get("format", "all")
    if fmt != "all" and fmt not in FORMAT_KEYS:
        abort(400, description="unknown format")
    db = get_db()
    cond, params = ("", ()) if fmt == "all" else (" WHERE format=?", (fmt,))
    rows = db.execute(
        "SELECT format, card_name, winner_id, loser_id FROM votes" + cond, params
    ).fetchall()

    total = len(rows)
    per_art, per_card, pairs, per_format = {}, {}, {}, {}
    for r in rows:
        wid, lid = r["winner_id"], r["loser_id"]
        per_art.setdefault(wid, [0, 0])[0] += 1
        per_art.setdefault(lid, [0, 0])[1] += 1
        per_card[r["card_name"]] = per_card.get(r["card_name"], 0) + 1
        per_format[r["format"]] = per_format.get(r["format"], 0) + 1
        a, b = sorted((wid, lid))
        tally = pairs.setdefault((r["card_name"], a, b), [0, 0])
        tally[0 if wid == a else 1] += 1

    metas = _art_meta(db, per_art.keys())
    min_games = 5 if total >= 200 else 3 if total >= 60 else 1

    def art_entry(art_id, wins, losses):
        m = metas.get(art_id, {})
        games = wins + losses
        return {
            "id": art_id,
            "card_name": m.get("card_name"),
            "artist": m.get("artist"),
            "set_name": m.get("set_name"),
            "released_at": m.get("released_at"),
            "art": m.get("art_url"),
            "full": m.get("full_url"),
            "wins": wins, "losses": losses, "games": games,
            "win_rate": round(100.0 * wins / games) if games else 0,
            "elo": round(m.get("elo", 1500)),
            "score": _wilson(wins, games),
        }

    ranked = [art_entry(a, wl[0], wl[1]) for a, wl in per_art.items()]
    eligible = [e for e in ranked if e["games"] >= min_games]
    top_arts = sorted(eligible, key=lambda e: (-e["score"], -e["games"]))[:12]

    pair_entries = []
    for (card, a, b), (na, nb) in pairs.items():
        n = na + nb
        if n < max(3, min_games):
            continue
        ma, mb = metas.get(a, {}), metas.get(b, {})
        pair_entries.append({
            "card_name": card,
            "n": n,
            "a": {"id": a, "art": ma.get("art_url"), "artist": ma.get("artist"),
                  "set_name": ma.get("set_name"), "votes": na,
                  "pct": round(100.0 * na / n)},
            "b": {"id": b, "art": mb.get("art_url"), "artist": mb.get("artist"),
                  "set_name": mb.get("set_name"), "votes": nb,
                  "pct": round(100.0 * nb / n)},
        })
    closest = sorted(pair_entries,
                     key=lambda e: (abs(e["a"]["votes"] / e["n"] - 0.5), -e["n"]))[:8]
    blowouts = sorted(pair_entries,
                      key=lambda e: (-max(e["a"]["pct"], e["b"]["pct"]), -e["n"]))
    blowouts = [e for e in blowouts if max(e["a"]["pct"], e["b"]["pct"]) >= 65][:8]

    artists = {}
    for e in ranked:
        if not e["artist"]:
            continue
        rec = artists.setdefault(e["artist"], {"artist": e["artist"], "wins": 0,
                                               "losses": 0, "arts": 0})
        rec["wins"] += e["wins"]
        rec["losses"] += e["losses"]
        rec["arts"] += 1
    artist_list = []
    for rec in artists.values():
        games = rec["wins"] + rec["losses"]
        if games < max(5, min_games):
            continue
        rec["games"] = games
        rec["win_rate"] = round(100.0 * rec["wins"] / games)
        rec["score"] = _wilson(rec["wins"], games)
        artist_list.append(rec)
    top_artists = sorted(artist_list, key=lambda r: (-r["score"], -r["games"]))[:10]

    most_voted = sorted(per_card.items(), key=lambda kv: -kv[1])[:10]

    return jsonify({
        "format": fmt,
        "totals": {
            "votes": total,
            "cards": len(per_card),
            "arts": len(per_art),
            "by_format": per_format,
        },
        "min_games": min_games,
        "top_arts": top_arts,
        "closest": closest,
        "blowouts": blowouts,
        "top_artists": top_artists,
        "most_voted": [{"card_name": c, "votes": v} for c, v in most_voted],
    })


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
