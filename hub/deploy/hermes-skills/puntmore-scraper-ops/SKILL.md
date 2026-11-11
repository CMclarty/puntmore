---
name: puntmore-scraper-ops
description: Operate and repair the Scraper (scraper/) on the Puntmore Hub, including Finder's sources. Use when a target is blocked, unhealthy, parsing too few rows, or an escalation names a source.
---

# Puntmore Scraper operations

Read `docs/decisions.md` first; it wins over this runbook. One Chrome on the Hub does every page load: the Scraper in `~/Projects/puntmore/scraper/`, running as the user service `puntmore-scraper` on its own virtual screen (`puntmore-screen`: real Chrome on the box's GPU, viewed with `remmina -c vnc://127.0.0.1:5910`) with its data in `/opt/puntmore-scraper/data`; the Docker container is the fallback for a box with no screen. Paddy Power pages are kept open; the one Bookie Bashing target (`bb-saves`) is the single named exception in `docs/decisions.md`: it stays and runs, and no other Bookie Bashing page is added; Finder sources (`site = "generic"`) are loaded, parsed and closed. Targets are `data/targets.toml` on the Hub. Parsers are `scraper/parsers/<name>.py`. The Hub's `puntmore` CLI (`~/.venvs/puntmore/bin/puntmore`) reads what the Scraper reports.

## Hard rules (never break these)
- Real headful Chrome, one persistent profile, no stealth or fingerprint tooling, no fake mouse movement. The Scraper's `browser.py` is the only place Chrome is configured.
- Budgets are code (`sites.py`): Paddy 15/h, Bookie Bashing 8/h, generic 60/h and 400/day. Never loop retries. Never add a bookmaker host as a generic target.
- A block starts a cool-off the Scraper enforces. Do not clear it to "try again"; it needs `python -m scraper resume <site>` after the hours have passed, and a human look at why.
- Bookie Bashing is not a Finder source. Any BB interaction is user-triggered.
- Parsers are written only from what real pages were seen to do: a saved dump or sample, never a guessed layout.
- Never guess a tip. A parser that cannot read the page returns nothing; `min_rows` then raises the alarm.

## Triage a broken source
1. `puntmore health` and `puntmore status` for the Scraper's view: which target, which state, the detail string.
2. `targets log <target> -n 50` (see "Adding a Finder source" for how to run it) for what the Scraper said about that job alone: loads and parser failures, window and days, rows handed to the Hub. Then `journalctl --user -u puntmore-scraper -n 200` for the rest, site-wide lines included, and the parser's own error (in the Docker fallback, `docker logs --tail 200 puntmore-scraper`), and `data/latest/<target>.json` for the last rows it produced.
3. Classify:
   - **blocked** (state `blocked`): cooling off or backing off. Resolve the escalation with "blocked, cooling off until <time>", stop (`targets.toml` keeps no comments; the escalation is the record). If it repeats after the cool-off, `targets disable <target>` (see "Adding a Finder source" for how to run it) and say so.
   - **layout change** (detail says "layout change?" or "parser failed"): go to step 4.
   - **parser not written**: the target is capturing only; samples are collecting under `data/samples/`.
4. Get a dump of the page as the Scraper sees it: `python -m scraper dump <target>` writes `data/dumps/<target>-<time>/0.html` and a screenshot. Fix `scraper/parsers/<parser>.py` against that file, and add it to `scraper/tests/fixtures/<parser>/` with a test so the old pages still parse.
5. `cd scraper && python -m pytest tests -q` must pass. Commit the parser and fixture on a branch with a one-line message naming the target. Push to `main` only through a pull request; once merged, `puntmore-update.timer` brings it onto the Hub and restarts the Scraper within 10 minutes (in the Docker fallback, CI rebuilds the image).
6. Resolve: `puntmore escalation resolve <id> --outcome "<what broke, what you changed, data flowing again: yes/no>"`.
7. Report one line to the chief of staff.

## Adding a Finder source
Never edit `data/targets.toml` by hand: every change goes through `scraper/targets.py`, which validates it and saves atomically under a lock. A person uses the Hub Console's Scraper tab (http://127.0.0.1:8090, the same library); you use its CLI. Run it from `~/Projects/puntmore` with `SCRAPER_DATA_DIR=/opt/puntmore-scraper/data PYTHONPATH=. ~/.venvs/puntmore/bin/python -m scraper targets ...` (shown below as `targets ...`). Add `--json` to read the result; a refused change exits 2 with the reason, which you report rather than work around. "being changed by something else" means the console or another run holds the lock: wait a few seconds and run it once more, never remove the lock file.
1. Add it disabled: `targets add --name <source> --site generic --url <url> --parser <parser> --post hub:tips --min-rows <n> --trust-weight 0.5 --every-min <minutes> --disabled`. A bookmaker host, or a frequency below the floor, is refused.
2. Let it capture a sample, then write `scraper/parsers/<parser>.py` from the sample with a fixture test.
3. `targets enable <source>`. The running Scraper picks it up within 60 s; `targets show <source> --json` shows its `status` once it has reported.
4. To change it later: `targets set <source> --every-min 120` (any field; `none` clears an optional one). To run it only on some days and stop on a date: `targets set <source> --days fri,sat --until 2026-11-30` (`--days none` = every day again, `--until none` = forever); after `until` its status is `finished`. To stop it: `targets disable <source>`; to delete it: `targets remove <source>`.
Rows reach the Hub's inbox as drop files and are stored as tips for HubAI to judge for Finder.

## Tipster email sources
Tipsters who send their tips by email are not Scraper targets: the Hub's `mail.py` polls one dedicated mailbox over IMAP (`PUNTMORE_IMAP_*` in `hub/.env`; the Hub daemon runs it every `PUNTMORE_IMAP_EVERY_MIN`), and nothing on Render or in the Scraper reads mail. Which senders are tipsters is `hub/data/tipsters.toml` (the format is `hub/tipsters.example.toml`): per entry a `name` (the source id on every tip), a `from` rule (an address or a domain), a `parser`, a `trust_weight` and `enabled`. The parsers are `hub/puntmore_hub/tipsters/<name>.py`, one per tipster, `parse(subject, text, html) -> rows`, registered in that package's `PARSERS`, with the same row contract as the Scraper's parsers. The file is re-read on every poll; an entry that will not validate (unknown parser, weight outside 0..1, duplicate name) stops that poll with one alert until it is fixed.

`puntmore mail --dry-run` reads the mailbox without changing anything (no tip stored, no message flagged) and prints the rows each parser would store, with their `tip_id`, and what it would reject or escalate; `puntmore mail` is one real poll. An escalation `unhealthy_source` whose ref is a tipster's name means an email from that tipster parsed to no rows or its parser raised; the detail carries the subject and the error. Get the email itself from the mailbox, fix the parser from that real message with a test in `hub/tests/`, and resolve as for a Scraper source. The same rule as every parser: never guess a tip. A parser that cannot read an email returns nothing, and the escalation is the alarm; do not write a looser parser to make it go quiet.
