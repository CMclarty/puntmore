---
name: puntmore-scraper-ops
description: Operate and repair the Scraper (scraper/) on the Puntmore Hub, including the Bet Finder's sources. Use when a target is blocked, unhealthy, parsing too few rows, or an escalation names a source.
---

# Puntmore Scraper operations

Read `docs/decisions.md` first; it wins over this runbook. One Chrome on the Hub does every page load: the Scraper in `~/Projects/puntmore/scraper/`, running as the user service `puntmore-scraper` on the desktop session (real Chrome on the real display) with its data in `/opt/puntmore-scraper/data`; the Docker container is the fallback for a box with no screen. Paddy Power pages are kept open; the one Bookie Bashing target (`bb-saves`) is an OPEN item for Callum in `docs/decisions.md` (keep as the named exception, or disable), so leave it as it is; Bet Finder sources (`site = "generic"`) are loaded, parsed and closed. Targets are `data/targets.toml` on the Hub. Parsers are `scraper/parsers/<name>.py`. The Hub's `puntmore` CLI (`~/.venvs/puntmore/bin/puntmore`) reads what the Scraper reports.

## Hard rules (never break these)
- Real headful Chrome, one persistent profile, no stealth or fingerprint tooling, no fake mouse movement. The Scraper's `browser.py` is the only place Chrome is configured.
- Budgets are code (`sites.py`): Paddy 15/h, Bookie Bashing 8/h, generic 60/h and 400/day. Never loop retries. Never add a bookmaker host as a generic target.
- A block starts a cool-off the Scraper enforces. Do not clear it to "try again"; it needs `python -m scraper resume <site>` after the hours have passed, and a human look at why.
- Bookie Bashing is not a Bet Finder source. Any BB interaction is user-triggered.
- Parsers are written only from what real pages were seen to do: a saved dump or sample, never a guessed layout.
- Never guess a tip. A parser that cannot read the page returns nothing; `min_rows` then raises the alarm.

## Triage a broken source
1. `puntmore health` and `puntmore status` for the Scraper's view: which target, which state, the detail string.
2. `journalctl --user -u puntmore-scraper -n 200` for the parser's own error (in the Docker fallback, `docker logs --tail 200 puntmore-scraper`), and `data/latest/<target>.json` for the last rows it produced.
3. Classify:
   - **blocked** (state `blocked`): cooling off or backing off. Note it in the target's comment in `targets.toml`, resolve the escalation with "blocked, cooling off until <time>", stop. If it repeats after the cool-off, set `enabled = false` and say so.
   - **layout change** (detail says "layout change?" or "parser failed"): go to step 4.
   - **parser not written**: the target is capturing only; samples are collecting under `data/samples/`.
4. Get a dump of the page as the Scraper sees it: `python -m scraper dump <target>` writes `data/dumps/<target>-<time>/0.html` and a screenshot. Fix `scraper/parsers/<parser>.py` against that file, and add it to `scraper/tests/fixtures/<parser>/` with a test so the old pages still parse.
5. `cd scraper && python -m pytest tests -q` must pass. Commit the parser and fixture on a branch with a one-line message naming the target. Push to `main` only through a pull request; once merged, `puntmore-update.timer` brings it onto the Hub and restarts the Scraper within 10 minutes (in the Docker fallback, CI rebuilds the image).
6. Resolve: `puntmore escalation resolve <id> --outcome "<what broke, what you changed, data flowing again: yes/no>"`.
7. Report one line to the chief of staff.

## Adding a Bet Finder source
Add a `[[target]]` with `site = "generic"`, `parser`, `post = "hub:tips"`, `min_rows` and `enabled = false` to `data/targets.toml` (see `scraper/targets.example.toml`). Let it capture a sample, write the parser from the sample with a fixture test, then set `enabled = true`. Rows reach Render as candidate tips through the Hub's inbox. Trust is set on Render, not here: a new source arrives inactive at trust 0.5, and Callum sets its trust weight with `PATCH /api/2up/betfinder/sources/{name}` (a `trust_weight` in `targets.toml` is ignored by Render).
