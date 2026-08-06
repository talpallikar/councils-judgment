# Council's Judgment

*Will of the council* — a one-page web app: two arts of the same Magic card,
side by side; pick the one you prefer. Votes are aggregated into community
rankings (Wilson-scored art leaderboard, closest rivalries, biggest blowouts,
top artists). Named after the card whose whole mechanic is everyone voting
and the winner resolving.

Hosted entirely on **GitHub Pages** (static frontend, Scryfall called directly
from the browser) with **Supabase** (free tier) as the shared vote store and a
**GitHub Actions cron** keeping the staple lists fresh.

## Deploy checklist

1. **Push this repo to GitHub** (public repo for free Pages).
2. **Enable Pages**: repo Settings → Pages → Source: *Deploy from a branch* →
   Branch `main`, folder `/docs`.
3. **Create a free Supabase project** at [supabase.com](https://supabase.com),
   then open its *SQL Editor*, paste all of `supabase/schema.sql`, and run it.
4. **Wire the frontend**: put your project's URL and anon key (Project
   Settings → API) into `docs/config.js`, commit, push.
5. **Enable the workflow**: the *Update staple lists* action runs Mondays and
   on demand (Actions tab → Run workflow). It commits a fresh
   `docs/staples.json`, which redeploys Pages automatically.

Until step 3–4 are done the site still works in **solo mode**: votes are kept
in the visitor's browser only and a banner says so.

## How it works

| Piece | Role |
|---|---|
| `docs/index.html` | The entire frontend — no build step, no framework |
| `docs/staples.json` | Per-format staple lists, regenerated weekly |
| `docs/config.js` | Supabase URL + anon key (safe to publish; RLS enforced) |
| `tools/build_staples.py` | Scrapes MTGTop8 (competitive formats) + EDHREC (Commander) |
| `tools/staple_sources.py` | The fetch/parse logic for those two sites |
| `tools/staples_seed.py` | Hand-curated fallback lists + format registry |
| `.github/workflows/update-staples.yml` | Weekly cron running the scraper |
| `supabase/schema.sql` | Votes table + `record_vote` / `get_stats` functions |
| `legacy-flask/` | The original self-hosted Flask version (superseded) |

Details worth knowing:

- **Staple freshness**: fresh scrape → previous list → seed list → Scryfall
  popularity fallback, in that order. A scrape returning under 20 names is
  rejected (markup-change guard) so a broken scrape never clobbers a good list.
- **Card arts** come from Scryfall's CORS-enabled API (`unique=art`, paper
  printings only), cached 7 days in the visitor's localStorage.
- **Vote integrity**: the anon key cannot read or write the `votes` table
  directly — row-level security is on with no policies. Everything goes
  through two `security definer` functions that validate format, card-name
  length, UUID illustration ids, and that art URLs point at
  `cards.scryfall.io`. The anon key in `config.js` is public **by design** —
  it can only call these two functions.
- **Rate limiting** lives inside `record_vote`: max 15 votes/minute and
  250/hour per caller, and the same pairing can only be judged 3 times a day
  by the same caller. Callers are identified by a salted SHA-256 hash of
  their IP (from the request headers Supabase passes to Postgres) — raw IPs
  are never stored. Determined abuse (rotating IPs) would need a bot check
  like Turnstile in front; not worth it until real traffic shows up.
- **Rankings** use the lower bound of the Wilson score interval, so a 3–0 art
  doesn't outrank a 40–10 one.

## Local development

```bash
python3 -m http.server -d docs 8000     # then open http://localhost:8000
```

Solo mode works fully offline-from-Supabase; to test the live backend just
fill in `docs/config.js`. Refresh staples locally with:

```bash
python3 -m venv .venv && .venv/bin/pip install requests
.venv/bin/python tools/build_staples.py
```

Card data and images courtesy of Scryfall. Unofficial Fan Content permitted
under the Fan Content Policy. Card images © Wizards of the Coast.
