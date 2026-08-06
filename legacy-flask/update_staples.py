#!/usr/bin/env python3
"""Force-refresh the staple lists from MTGTop8 / EDHREC.

Usage:
    python update_staples.py               # refresh every format
    python update_staples.py modern legacy # refresh specific formats

The server also refreshes automatically (lists older than 7 days trigger a
background refresh on use), so this is only needed to force an update now.
"""

import sys

from app import FORMAT_KEYS, refresh_all


def main():
    fmts = sys.argv[1:] or None
    if fmts:
        unknown = [f for f in fmts if f not in FORMAT_KEYS]
        if unknown:
            sys.exit(f"unknown format(s): {', '.join(unknown)} "
                     f"(choose from {', '.join(FORMAT_KEYS)})")
    refresh_all(fmts, verbose=True)


if __name__ == "__main__":
    main()
