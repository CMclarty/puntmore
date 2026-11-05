---
name: puntmore-scraper-ops
description: Operate and repair the Puntmore home-box scrapers (sources in YAML, parsers in Python, SQLite state). Use when a scrape source is unhealthy, blocked, empty, or an escalation names a source.
---

# Puntmore scraper operations

Repo: `~/Projects/puntmore`, package `hub/` (run the commands below from `hub/`). CLI: `~/.venvs/puntmore/bin/puntmore` (also on PATH as `puntmore` inside the venv).
State: `data/home.sqlite`. Raw pages: `data/raw/<source>/<timestamp>.html`. Sources: `sources/*.yaml`. Parsers: `parsers/<name>.py`. Fixtures: `fixtures/<parser>/`.

## Hard rules (never break these)
- Real headful Chrome, one persistent profile, no stealth or fingerprint tooling, no fake mouse movement.
- Request budget ~60 pages/hour, ~400/day. Never loop retries against a bookmaker.
- On any challenge, CAPTCHA or block: the runner sets a 24 h cool-off. Do not clear it to "try again". Report it.
- BookieBashing is not a scrape source without written permission. Do not add it.
- Never change the database schema or the runner. That is the software supervisor's job; ask via the chief of staff.
- Never guess a tip. A parser that cannot read the page returns nothing.

## Triage a broken source
1. `puntmore health` and `puntmore runs --limit 20` to see the failing run and its status: `empty`, `parse_error`, `blocked`, `fetch_error`.
2. `puntmore raw <source>` to find the saved page from the failed run and the last good one before it.
3. Classify:
   - **blocked**: cool-off is set. Note it in the source's `notes`, resolve the escalation with "blocked, cooling off until <time>", and stop. If it repeats on the next run after cool-off, disable the source (`enabled: false`) and say so.
   - **empty / parse_error** with the page fetched fine: layout change. Go to step 4.
   - **fetch_error** repeated 3+ times: site or network down. Report; do not change the parser.
4. Fix the parser against the saved page: edit `parsers/<parser>.py`, then
   `puntmore parser-test <parser> data/raw/<source>/<failed>.html --source <source>` until it passes the source's `min_items`.
   Also run it against the fixture in `fixtures/<parser>/` so old pages still parse. Save the new page as a second fixture if the layout really changed.
5. `puntmore scrape run <source> --force` once. If `ok`, commit the parser and fixture with a one-line message naming the source and what changed.
6. Resolve: `puntmore escalation resolve <id> --outcome "<what broke, what you changed, data flowing again: yes/no>"`.
7. Report one line to the chief of staff.

## Adding a source
Copy `sources/_template.yaml`, write `parsers/<parser>.py`, save a real page under `fixtures/<parser>/page.html`, pass `puntmore parser-test`, then set `enabled: true`. Start with `trust_weight` at 0.5 for an unproven source.
