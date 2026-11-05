# Working in hub/

This is the Hub side of the Puntmore 2-Up Engine. Render (cloud, `render/api/`) prices and decides; this box places and reports. Every page load on the Hub is the Worker's (`scraper/`), including the Bet Finder's sources; `hub/` reads what the Worker drops. The repo's `CLAUDE.md` applies here too and wins on any conflict. Read this directory's `README.md` and `PROTOCOL.md` before changing anything.

## Rules
- Dry-run is on by default (`PUNTMORE_DRY_RUN`). Do not switch it off. Cal does that after two weeks of clean dry-run reports.
- The kill switch is the file `data/KILL`. `puntmore kill on` stops placements immediately. Anyone may turn it on; only Cal turns it off.
- No auto-retry and no re-routing of failed bets, anywhere. A failure is reported and escalated, full stop.
- Every bet is idempotent on `bet_id`; every message on `msg_id`. Never remove those checks.
- `hub/` never loads a page. Scraping, budgets and parsers are the Worker's (`scraper/`); its rules are in `scraper/AGENTS.md` and the runbook `deploy/hermes-skills/puntmore-scraper-ops/SKILL.md`.
- Secrets go in `.env` (git-ignored). Never commit tokens, account details or `sub_clients.yaml`.

## Where to change what
| Want to... | Change |
|---|---|
| Add or pause a Bet Finder source | `targets.toml` on the Hub (`site = "generic"`, see `scraper/targets.example.toml`) |
| Fix a broken parser | `scraper/parsers/<name>.py` + a fixture under `scraper/tests/fixtures/` |
| Change where the Worker's drops are read from | `.env` (`PUNTMORE_WORKER_DATA`) |
| Add a bookmaker account (sub-client) | `sub_clients.yaml` (see `sub_clients.example.yaml`) |
| Implement real placement | `puntmore_hub/placement/adapters/phone.py` |
| Get a merged change onto the box | Nothing: it arrives within 10 minutes via `puntmore-update.timer`; `hub/deploy/update.sh` by hand (README, "Updating the Hub") |
| Change what Render and the Hub say to each other | `PROTOCOL.md`, `schemas/protocol.schema.json`, an example in `schemas/examples/`, `puntmore_hub/schemas.py`, and the Render side in `render/api/`, all in ONE pull request |

## Checks
```
cd hub && ~/.venvs/puntmore/bin/pytest -q   # must pass before any commit
puntmore health                             # exit 1 = Worker down, a source blocked/unhealthy, or an open escalation
puntmore inbox                              # one pass over the Worker's drops (the link daemon does this every 30 s)
```

## From the repo's CLAUDE.md (moved here 5 Nov 2026)
`hub/` is the Hub side of the 2-Up Engine (spec: the "2-Up Engine —
Module Spec" doc): the persistent outbound link to Render, the client engine
that places bets on sub-client phones, escalation to the local AI, and the inbox
that turns the Worker's drops into tips for Render and escalations. It runs on Callum's box, never on Render, and is NOT
part of the Render deploy (`.github/workflows/hub-tests.yml` only tests it).
Rules, all from the spec — do not relax any of them:
- **Dry-run is the default** (`PUNTMORE_DRY_RUN`); Callum switches it off, nobody
  else. **The kill switch is a file** (`hub/data/KILL`), honoured by scrapes
  and placements, no code change to flip.
- **No auto-retry, no re-routing of a failed bet.** `failed` is reported and
  escalated. A bet is idempotent on `bet_id`, a message on `msg_id`.
- **The wire contract is `hub/PROTOCOL.md` + `hub/schemas/`.** A change to
  it touches the schema, an example, `hub/puntmore_hub/schemas.py` and the
  Render side in `render/api/` in ONE pull request; `hub/tests/test_protocol.py` is
  the referee. Bet Finder tips are `source_scope: owner` — never offered to
  another client.
- **One Chrome on the Hub, the Worker's.** `hub/` has no browser and no
  budget; the Bet Finder's sources are Worker targets (`site = "generic"`)
  and reach `hub/` as files under `<worker data>/hub/`. BookieBashing is not
  a scrape source without written permission.
- Directory ownership when several agents work at once: `render/api/` + `render/frontend/`
  (Render side) and `hub/` + `scraper/` (home side) are edited by different
  branches; only the protocol files are shared.

