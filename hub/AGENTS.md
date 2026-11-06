# Working in hub/

This is the Hub side of the Puntmore 2-Up Engine. Render (cloud, `render/api/`) prices and serves a one-way value feed, and has no Hub endpoint since 6 Nov 2026; this box places and reports. Every page load on the Hub is the Scraper's (`scraper/`), including Finder's tip sources; `hub/` reads what the Scraper drops and the tipster mailbox, stores each tip once, and hands it to Hermes, who judges it and posts the verdict to Finder on Render (`puntmore_hub/finder.py`, `docs/finder-spec.md`). `docs/decisions.md` is read first and wins; the repo's `CLAUDE.md` applies here too and wins on any other conflict. Read this directory's `README.md` and `PROTOCOL.md` before changing anything.

## Rules
- Dry-run is on by default (`PUNTMORE_DRY_RUN`). Do not switch it off. Cal does that after two weeks of clean dry-run reports.
- The kill switch is the file `data/KILL`. `puntmore kill on` stops placements immediately. Anyone may turn it on; only Cal turns it off.
- No auto-retry and no re-routing of failed bets, anywhere. A failure is reported and escalated, full stop.
- Every bet is idempotent on `bet_id`; every message on `msg_id`. Never remove those checks.
- `hub/` never loads a page. Scraping, budgets and parsers are the Scraper's (`scraper/`); its rules are in `scraper/AGENTS.md` and the runbook `deploy/hermes-skills/puntmore-scraper-ops/SKILL.md`.
- Email tipsters are polled by the Hub's `mail.py` (one IMAP mailbox, `PUNTMORE_IMAP_*`; senders and parsers in `data/tipsters.toml`), never by Render and never by the Scraper. Its parsers (`puntmore_hub/tipsters/`) never guess: one that cannot read an email returns nothing and the tipster is escalated as unhealthy.
- Secrets go in `.env` (git-ignored). Never commit tokens, account details or `sub_clients.yaml`.

## Where to change what
| Want to... | Change |
|---|---|
| Add or pause a Scraper target (a Finder source is `site = "generic"`) | the console's Scraper tab, or `python -m scraper targets …` (`scraper/README.md`, "Changing targets"); never `targets.toml` by hand |
| Fix a broken parser | `scraper/parsers/<name>.py` + a fixture under `scraper/tests/fixtures/` |
| Add an email tipster | an entry in `data/tipsters.toml` (see `tipsters.example.toml`) + a parser in `puntmore_hub/tipsters/<name>.py` registered in `PARSERS`, written from a real email; `puntmore mail --dry-run` shows what it reads |
| Change where the Scraper's drops are read from | `.env` (`PUNTMORE_SCRAPER_DATA`; the old `PUNTMORE_WORKER_DATA` is still read for one release) |
| Add a bookmaker account (sub-client) | `sub_clients.yaml` (see `sub_clients.example.yaml`) |
| Implement real placement | `puntmore_hub/placement/adapters/phone.py` |
| See what the Hub is doing | open the console at http://127.0.0.1:8090 (`puntmore web`, unit `puntmore-web`; code in `puntmore_hub/web/`) |
| Get a merged change onto the box | Nothing: it arrives within 10 minutes via `puntmore-update.timer`; `hub/deploy/update.sh` by hand (README, "Updating the Hub") |
| Change what Render and the Hub say to each other | `PROTOCOL.md`, `schemas/protocol.schema.json`, an example in `schemas/examples/`, `puntmore_hub/schemas.py`, and the Render side in `render/api/`, all in ONE pull request |

## Checks
```
cd hub && ~/.venvs/puntmore/bin/pytest -q   # must pass before any commit
puntmore health                             # exit 1 = Scraper down, a source blocked/unhealthy, or an open escalation
puntmore inbox                              # one pass over the Scraper's drops (the link daemon does this every 30 s)
puntmore mail --dry-run                     # what the tipster parsers would read from the mailbox; stores nothing
```

## From the repo's CLAUDE.md (moved here 5 Nov 2026)
`hub/` is the Hub side of the 2-Up Engine (spec:
`docs/2up-engine-spec.md`): the persistent outbound link to Render, the client engine
that places bets on sub-client phones, escalation to the local AI, and the inbox
that turns the Scraper's drops into tips for Render and escalations. It runs on Callum's box, never on Render, and is NOT
part of the Render deploy (`.github/workflows/hub-tests.yml` only tests it).
Rules, all from the spec — do not relax any of them:
- **Dry-run is the default** (`PUNTMORE_DRY_RUN`); Callum switches it off, nobody
  else. **The kill switch is a file** (`hub/data/KILL`), honoured by
  placements, no code change to flip.
- **No auto-retry, no re-routing of a failed bet.** `failed` is reported and
  escalated. A bet is idempotent on `bet_id`, a message on `msg_id`.
- **The wire contract is `hub/PROTOCOL.md` + `hub/schemas/`.** A change to
  it touches the schema, an example, `hub/puntmore_hub/schemas.py` and the
  Render side in `render/api/` in ONE pull request; `hub/tests/test_protocol.py` is
  the referee. Tips no longer travel on the wire (the `tip` message was
  retired with the socket, 6 Nov 2026): Hermes posts its verdicts to Finder
  over HTTP (`puntmore finder post`, the ingest key), and Finder rows are
  Callum's only — never offered to another client.
- **One Chrome on the Hub, the Scraper's.** `hub/` has no browser and no
  budget; Finder's sources are Scraper targets (`site = "generic"`)
  and reach `hub/` as files under `<scraper data>/hub/`. Bookie Bashing is not
  a Finder source; the Scraper's one BB target (`bb-saves`) is an OPEN
  item for Callum in `docs/decisions.md`.
- Directory ownership when several agents work at once: `render/api/` + `render/frontend/`
  (Render side) and `hub/` + `scraper/` (Hub side) are edited by different
  branches; only the protocol files are shared.

