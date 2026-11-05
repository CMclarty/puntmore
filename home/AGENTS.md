# Working in home/

This is the home-box side of the Puntmore 2-Up Engine. Render (cloud, `api/`) prices and decides; this box scrapes, places, and reports. The repo's `CLAUDE.md` applies here too and wins on any conflict. Read this directory's `README.md` and `PROTOCOL.md` before changing anything.

## Rules
- Dry-run is on by default (`PUNTMORE_DRY_RUN`). Do not switch it off. Cal does that after two weeks of clean dry-run reports.
- The kill switch is the file `data/KILL`. `puntmore kill on` stops scrapes and placements immediately. Anyone may turn it on; only Cal turns it off.
- No auto-retry and no re-routing of failed bets, anywhere. A failure is reported and escalated, full stop.
- Every bet is idempotent on `bet_id`; every message on `msg_id`. Never remove those checks.
- Bookmaker pages load only on this box. Never add bookmaker pricing calls here that belong to the odds API on Render.
- Scraper hard rules live in `deploy/hermes-skills/puntmore-scraper-ops/SKILL.md`. Follow them.
- Secrets go in `.env` (git-ignored). Never commit tokens, account details or `sub_clients.yaml`.

## Where to change what
| Want to... | Change |
|---|---|
| Add or pause a scrape source | `sources/<name>.yaml` |
| Fix a broken parser | `parsers/<name>.py` + a fixture under `fixtures/<name>/` |
| Change budgets, cool-off, paths | `.env` |
| Add a bookmaker account (sub-client) | `sub_clients.yaml` (see `sub_clients.example.yaml`) |
| Implement real placement | `puntmore_home/placement/adapters/phone.py` |
| Change what Render and the home box say to each other | `PROTOCOL.md`, `schemas/protocol.schema.json`, an example in `schemas/examples/`, `puntmore_home/schemas.py`, and the Render side in `api/`, all in ONE pull request |

## Checks
```
cd home && ~/.venvs/puntmore/bin/pytest -q  # must pass before any commit
puntmore health                             # exit 1 = something needs attention
puntmore parser-test <parser> <saved page>  # before enabling a source
```
