# hub/ — the Hub

Hub side of the Puntmore 2-Up Engine, in the same repo as the Render service it talks to (`api/`) and the Paddy/Bookie Bashing worker (`scraper/`). The wire contract between this and Render is `PROTOCOL.md` + `schemas/`; change them together. One machine on a residential connection that owns everything touching a bookmaker site or a phone. The cloud side (Render) owns pricing, the 2-up decision model and the per-client ledgers, and never loads a bookmaker page.

```
Render (cloud)                         Hub (this repo)
──────────────                         ────────────────────
2-up engine, Bet Finder orchestrator   link/      persistent outbound WebSocket to Render
odds API client, ledgers    ◄────────► scraper/   headful-Chrome worker, YAML sources, parsers, budget
                 bet lists, scrape jobs placement/ client engine: bet lists → phones, reports back
                 reports, tips, outcomes escalation local AI (Hermes) owns what happens after a failure
```

## Layout
```
puntmore_hub/
  config.py        settings from .env; nothing hard-coded
  schemas.py       the Render contract: Bet, Report, CandidateTip, ScrapeJob, Envelope
  store.py         SQLite: runs, tips, bets, outbox, escalations, request budget
  link/client.py   reconnecting WS client, durable outbox, idempotent inbound
  scraper/         sources.py (YAML registry) · schedule.py · budget.py · browser.py (Playwright, real Chrome)
                   parsers.py (loader + contract) · runner.py (fetch → archive → parse → validate → store → queue)
  placement/       engine.py (client engine) · adapters/{dry_run,phone}.py
  escalation.py    hands failures to Hermes with a self-contained brief; records the outcome
  alerts.py        Discord/log alerts with de-dup; daily heartbeat
  cli.py           `puntmore ...`
sources/           one YAML per scrape source (template included)
parsers/           one small parse() per source
fixtures/          saved pages + expected output, so parser fixes are tested offline
deploy/systemd/    link daemon, 5-minute scrape timer, 09:00 heartbeat timer
deploy/hermes-skills/  the scraper-ops runbook the scraper-manager agent follows
data/              runtime state (git-ignored): home.sqlite, raw/, chrome-profile/, KILL
```

## Relationship to `scraper/`
`scraper/` is the existing worker: one Chrome with Paddy Power and Bookie Bashing pages kept open, driven by Puntmore's `/scraper` page, posting prices to the API. `hub/` is the 2-Up Hub: the Render link, the client engine, escalation, and Bet Finder sources (tipster-style pages fetched once per run). The Chrome setup is shared: `hub/` borrows `scraper/browser.py` for launch, consent, dwell and block detection so the realism rules live in one file. Budgets are not yet shared (`scraper/guard.py` is per-site and in code; `hub/` has one global budget) — see the status section.

## Setup (from `hub/`)
```bash
python3 -m venv ~/.venvs/puntmore && ~/.venvs/puntmore/bin/pip install -e '.[dev]'
~/.venvs/puntmore/bin/playwright install chromium     # fallback browser; real Chrome is preferred if installed
cp .env.example .env                                   # fill in Render URL/key, Discord webhook
puntmore init && pytest -q
```

Services (user units, headful Chrome needs the desktop session):
```bash
cp deploy/systemd/* ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now puntmore-link puntmore-scrape.timer puntmore-heartbeat.timer
```

## Everyday commands
```
puntmore status                      one screen: mode, budget, sources
puntmore sources                     what is configured and healthy
puntmore scrape run <name> --force   run one source now
puntmore scrape due                  what the timer runs
puntmore runs / puntmore raw <src>   history and archived pages
puntmore parser-test <parser> <file> test a parser against a saved page before enabling it
puntmore health                      exit 1 if a source is unhealthy or an escalation is open
puntmore kill on|off|status          the kill switch (a file: data/KILL)
puntmore escalation list|show|resolve
puntmore tips                        candidate bets found
puntmore heartbeat                   the daily summary, on demand
puntmore link                        the Render link daemon
```

## Behaviour that is fixed by the spec
- **Dry-run by default.** Placement logs what it would do and reports `placed` with `dry_run: true`.
- **Kill switch** at `data/KILL`, global, no code change, honoured by scrapes and placements.
- **Idempotency**: `bet_id` and `msg_id` are never processed twice.
- **Failures**: no auto-retry, no re-routing. `failed` is reported and escalated to the local AI.
- **Scraper hard rules**: real headful Chrome with one persistent profile, no stealth, ~60 pages/h and ~400/day, 24 h cool-off and alert on any challenge, daily 09:00 heartbeat, silence means broken.
- **Bet Finder output is owner-scoped** (`source_scope: owner`): never offered to other clients.
- **BookieBashing** is not a scrape source without written permission.

## Testing the contract with Render
`pytest` runs the real link against `puntmore_hub/link/mock_render.py` over a localhost socket, and validates every example in `schemas/examples/` against `schemas/protocol.schema.json`. Against a real Render build: `puntmore link-check wss://host/ws --key <key>`.

## Status
Scaffold. The scraper pipeline, client engine, link protocol, escalation path and CLI are implemented and tested with fakes. Not yet done: the phone placement adapter, the IMAP tipster source kind, the kick-off-window sweep schedule, the Render end of the WebSocket (`api/`), and unifying the request budget with `scraper/guard.py`.
