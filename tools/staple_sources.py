"""Live staple-list sources.

Competitive formats come from mtgtop8.com's "Top Cards" pages (POST form,
scraped). Commander comes from EDHREC's public JSON (top cards of the past
month). Pure fetch/parse logic — persistence and scheduling live in app.py.
"""

import html as htmllib
import re
import time

MTGTOP8_URL = "https://mtgtop8.com/topcards"
EDHREC_URL = "https://json.edhrec.com/pages/top/month.json"

# mtgtop8 format codes for the formats we expose
MTGTOP8_CODES = {
    "standard": "ST",
    "pioneer": "PI",
    "modern": "MO",
    "legacy": "LE",
    "vintage": "VI",
}

# Preferred metagame windows, most recent first. The ids behind these labels
# shift over time, so they are parsed from the live form rather than pinned.
PREFERRED_META = ("last 2 months", "last 4 months", "last 6 months")

PAGES = 25         # 20 cards per page -> up to 500 staples per format
MIN_ACCEPT = 20    # reject a scrape that yields fewer names (likely a markup change)
POLITE_DELAY = 0.6
SCRYFALL_TOP = 500  # commander pool size via Scryfall EDHREC-rank ordering

_meta_cache = {"ids": None, "at": 0.0}


def _metagame_ids(session):
    """format code -> metagame id, parsed from the live topcards form."""
    now = time.monotonic()
    if _meta_cache["ids"] and now - _meta_cache["at"] < 3600:
        return _meta_cache["ids"]
    r = session.get(MTGTOP8_URL, timeout=20)
    r.raise_for_status()
    ids = {}
    for code in MTGTOP8_CODES.values():
        m = re.search(
            r"id=meta_%s name=metagame_sel\[%s\]>(.*?)</select>" % (code, code),
            r.text, re.S)
        if not m:
            continue
        opts = re.findall(r"<option value=(\d+)\s*>([^<]+)</option>", m.group(1))
        if not opts:
            continue
        chosen = None
        for pref in PREFERRED_META:
            chosen = next((v for v, label in opts if label.strip().lower() == pref), None)
            if chosen:
                break
        ids[code] = chosen or opts[0][0]
    if ids:
        _meta_cache.update(ids=ids, at=now)
    return ids


def fetch_mtgtop8(session, fmt):
    """Staple names for a competitive format, most-played first."""
    code = MTGTOP8_CODES[fmt]
    meta_id = _metagame_ids(session).get(code)
    if not meta_id:
        raise RuntimeError(f"no metagame id found for {fmt} ({code})")
    names, seen = [], set()
    for page in range(1, PAGES + 1):
        data = {
            "format": code,
            f"metagame_sel[{code}]": meta_id,
            "card_col": "", "card_type": "", "card_rarity": "",
            "current_page": str(page),
        }
        r = session.post(MTGTOP8_URL, data=data, timeout=20)
        r.raise_for_status()
        found = re.findall(r"<td id=\w+_1 class=L14>([^<]+)</td>", r.text)
        if not found:
            break
        added = 0
        for raw in found:
            name = htmllib.unescape(raw).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
                added += 1
        if not added:
            break   # site repeats/clamps pages past the last one
        time.sleep(POLITE_DELAY)
    return names


def fetch_edhrec(session, limit=100):
    """Most-played Commander cards of the past month (fallback source)."""
    r = session.get(EDHREC_URL, timeout=20)
    r.raise_for_status()
    cardlists = r.json()["container"]["json_dict"]["cardlists"]
    names, seen = [], set()
    for cl in cardlists:
        for cv in cl.get("cardviews", []):
            name = cv.get("name", "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names[:limit]


def fetch_scryfall_top(session, fmt, limit=SCRYFALL_TOP):
    """Top cards of a format by EDHREC rank via Scryfall (deep, paginated)."""
    url = "https://api.scryfall.com/cards/search"
    params = {"q": f"format:{fmt} -is:digital", "order": "edhrec", "unique": "cards"}
    names, seen = [], set()
    while url and len(names) < limit:
        r = session.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for c in data.get("data", []):
            name = c.get("name", "").split(" //")[0].strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        url = data.get("next_page") if data.get("has_more") else None
        params = None   # next_page is a complete URL
        time.sleep(0.12)
    return names[:limit]


def fetch_for(session, fmt):
    """Return (names, source_label) for a format from its live source."""
    if fmt == "commander":
        try:
            names = fetch_scryfall_top(session, "commander")
            if len(names) >= MIN_ACCEPT:
                return names, "scryfall.com (EDHREC rank)"
        except Exception:
            pass
        return fetch_edhrec(session), "edhrec.com"
    if fmt in MTGTOP8_CODES:
        return fetch_mtgtop8(session, fmt), "mtgtop8.com"
    raise KeyError(fmt)
