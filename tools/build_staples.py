#!/usr/bin/env python3
"""Build docs/staples.json from live sources (MTGTop8 + EDHREC).

Run weekly by .github/workflows/update-staples.yml, or manually:

    python tools/build_staples.py

Per format, in order of preference: fresh scrape -> previous staples.json
entry -> built-in seed list -> Scryfall popularity search. A scrape returning
fewer than MIN_ACCEPT names is treated as a failure (likely a markup change)
so a good previous list is never overwritten by a broken one.
"""

import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import staple_sources
from staples_seed import FORMATS, STAPLES

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "docs", "staples.json")


def scryfall_popular(session, fmt, limit=75):
    r = session.get("https://api.scryfall.com/cards/search", params={
        "q": f"format:{fmt} -is:digital", "order": "edhrec", "unique": "cards",
    }, timeout=20)
    r.raise_for_status()
    return [c["name"].split(" //")[0] for c in r.json().get("data", [])][:limit]


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "CouncilsJudgment/1.0 (MTG art voting; GitHub Pages hobby app)",
        "Accept": "application/json",
    })

    old = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            old = {e["key"]: e for e in json.load(f)["formats"]}

    formats, fresh = [], 0
    for key, label in FORMATS:
        entry = None
        try:
            names, source = staple_sources.fetch_for(session, key)
            if len(names) >= staple_sources.MIN_ACCEPT:
                entry = {"key": key, "label": label, "source": source,
                         "updated_at": int(time.time()), "names": names}
                fresh += 1
                print(f"{label}: {len(names)} cards from {source}")
            else:
                print(f"{label}: only {len(names)} names, rejecting scrape",
                      file=sys.stderr)
        except Exception as exc:
            print(f"{label}: fetch failed: {exc}", file=sys.stderr)

        if entry is None and key in old and old[key].get("names"):
            entry = old[key]
            print(f"{label}: keeping previous list ({len(entry['names'])} cards)")
        if entry is None and STAPLES.get(key):
            entry = {"key": key, "label": label, "source": "built-in seed list",
                     "updated_at": None, "names": STAPLES[key]}
            print(f"{label}: using seed list")
        if entry is None:
            try:
                names = scryfall_popular(session, key)
                entry = {"key": key, "label": label,
                         "source": "scryfall.com popularity (fallback)",
                         "updated_at": int(time.time()), "names": names}
                print(f"{label}: Scryfall popularity fallback ({len(names)} cards)")
            except Exception as exc:
                print(f"{label}: all sources failed: {exc}", file=sys.stderr)
                entry = {"key": key, "label": label, "source": "unavailable",
                         "updated_at": None, "names": []}
        formats.append(entry)

    with open(OUT, "w") as f:
        json.dump({"generated_at": int(time.time()), "formats": formats}, f, indent=1)
    print(f"wrote {OUT} ({fresh}/{len(FORMATS)} formats freshly scraped)")
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())
