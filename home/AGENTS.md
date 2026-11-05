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

## From the repo's CLAUDE.md (moved here 5 Nov 2026)
`home/` is the home-box side of the 2-Up Engine (spec: the "2-Up Engine —
Module Spec" doc): the persistent outbound link to Render, the client engine
that places bets on sub-client phones, escalation to the local AI, and the Bet
Finder's scrape sources. It runs on Callum's box, never on Render, and is NOT
part of the Render deploy (`.github/workflows/home-tests.yml` only tests it).
Rules, all from the spec — do not relax any of them:
- **Dry-run is the default** (`PUNTMORE_DRY_RUN`); Callum switches it off, nobody
  else. **The kill switch is a file** (`home/data/KILL`), honoured by scrapes
  and placements, no code change to flip.
- **No auto-retry, no re-routing of a failed bet.** `failed` is reported and
  escalated. A bet is idempotent on `bet_id`, a message on `msg_id`.
- **The wire contract is `home/PROTOCOL.md` + `home/schemas/`.** A change to
  it touches the schema, an example, `home/puntmore_home/schemas.py` and the
  Render side in `api/` in ONE pull request; `home/tests/test_protocol.py` is
  the referee. Bet Finder tips are `source_scope: owner` — never offered to
  another client.
- **Chrome setup is `scraper/browser.py`**, borrowed, not copied: the realism
  rules (no spoofing, no solvers, a person's Chrome) live in one file.
  Bookmaker pages load only on the home box. BookieBashing is not a scrape
  source without written permission.
- Directory ownership when several agents work at once: `api/` + `frontend/`
  (Render side) and `home/` + `scraper/` (home side) are edited by different
  branches; only the protocol files are shared.

