# hub/ — the Hub

Hub side of the Puntmore 2-Up Engine, in the same repo as the Render service it talks to (`render/api/`) and the Paddy/Bookie Bashing worker (`scraper/`). The wire contract between this and Render is `PROTOCOL.md` + `schemas/`; change them together. One machine on a residential connection that owns everything touching a bookmaker site or a phone. The cloud side (Render) owns pricing, the 2-up decision model and the per-client ledgers, and never loads a bookmaker page.

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
  store.py         SQLite: tips, bets, outbox, escalations
  inbox.py         the Worker's drops (tips, events) -> stored tips queued for Render, escalations
  link/client.py   reconnecting WS client, durable outbox, idempotent inbound
  placement/       engine.py (client engine) · adapters/{dry_run,phone}.py
  escalation.py    hands failures to Hermes with a self-contained brief; records the outcome
  alerts.py        Discord/log alerts with de-dup; daily heartbeat
  update.py        self-update: fast-forward to origin/main, reinstall, restart (`puntmore update`)
  cli.py           `puntmore ...`
deploy/update.sh   thin wrapper around `puntmore update`, run by the update timer
deploy/systemd/    link daemon, 09:00 heartbeat timer, 10-minute update timer
deploy/hermes-skills/  the Worker-ops runbook the scraper-manager agent follows
data/              runtime state (git-ignored): hub.sqlite, KILL
```

## Relationship to `scraper/`
The Worker (`scraper/`) is the only thing on the Hub that loads a page: Paddy Power and Bookie Bashing pages kept open, and the Bet Finder's sources as `generic` targets that are loaded, parsed and closed, all under one Chrome, one guard and one alert path. For a Bet Finder target the Worker writes one JSON file per run into `<worker data>/hub/tips/` and, when something needs a decision, into `<worker data>/hub/events/`. `puntmore inbox` (run by the link daemon every 30 s) turns those into deduplicated tips queued for Render and into escalations for Hermes. `puntmore health` reads the Worker's heartbeat and `status.json` from the same directory (`PUNTMORE_WORKER_DATA`, the Docker volume on the host).

## Setup (from `hub/`)
```bash
python3 -m venv ~/.venvs/puntmore && ~/.venvs/puntmore/bin/pip install -e '.[dev]'
cp .env.example .env                                   # fill in Render URL/key, Discord webhook
puntmore init && pytest -q
```

Services (user units, headful Chrome needs the desktop session):
```bash
cp deploy/systemd/* ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now puntmore-link puntmore-heartbeat.timer puntmore-update.timer
```

## Everyday commands
```
puntmore status                      one screen: mode, the Worker's targets and their state
puntmore inbox                       one pass over the Worker's drops
puntmore health                      exit 1 if the Worker is down, a source is blocked or unhealthy, or an escalation is open
puntmore kill on|off|status          the kill switch (a file: data/KILL)
puntmore escalation list|show|resolve
puntmore tips                        candidate bets found
puntmore heartbeat                   the daily summary, on demand
puntmore link                        the Render link daemon (also runs the inbox)
puntmore link-check <url> --key K    handshake with a Render build
puntmore update [--dry-run|--check]  fast-forward to origin/main, reinstall, restart (deploy/update.sh)
python -m scraper status|dump|resume the Worker's own commands, from scraper/
```

## Updating the Hub
A change merged to `main` reaches the box within 10 minutes. `puntmore-update.timer` runs `deploy/update.sh` (a wrapper around `puntmore update`, code in `puntmore_hub/update.py`) against the box's checkout, `~/Projects/puntmore`:

1. `git fetch origin main`. If `origin/main` is not ahead of HEAD, nothing else happens.
2. `git pull --ff-only origin main`. Never a merge, never a reset.
3. `uv pip install -e 'hub[web]' --python ~/.venvs/puntmore/bin/python` (plain `hub` while there is no `web` extra; the venv's own pip if uv is not on PATH).
4. `systemctl --user restart` each of `puntmore-link`, `puntmore-web` and `puntmore-heartbeat.timer` that exists and is enabled.

Every run, including "up to date", appends one line to `data/update.log`: time, outcome, branch, the commit moved from and to. A failure is also alerted through `alerts.py` (Discord, de-duplicated per kind for `PUNTMORE_ALERT_DEDUP_HOURS`) and is never retried inside the run.

**What blocks it** (logged; the ones marked *alert* also go to Discord):
- **A bet in flight**: any bet in the store with status `received` or `placing`. Nothing is pulled or restarted; the next tick tries again. A bet stuck in flight for over 30 minutes is a crash, not a placement: *alert*, and it keeps blocking until a human settles that bet.
- **A bet that arrives during the install**: the code is pulled and installed, the restart waits. `data/UPDATE_PENDING` holds the commit, so the next tick restarts even though the checkout is current. An install or restart failure leaves the same marker, so the next tick (not this run) tries once more.
- **Not a fast-forward** (the checkout has commits that are not on `origin/main`): *alert*, left alone. The box's checkout belongs on `main`; a feature branch that was squash-merged diverges.
- **Local edits to tracked files**, or a **detached HEAD**: *alert*, left alone.
- **Another run** holding `data/update.lock`.

It does not copy unit files. When a pull changes `hub/deploy/systemd/`, the run sends a warning naming the files; copy them to `~/.config/systemd/user/` and `systemctl --user daemon-reload` by hand.

Enable it once, after the first pull that brings `puntmore update` onto the box (that one is by hand):
```bash
cp deploy/systemd/puntmore-update.* ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now puntmore-update.timer
```

By hand, from the checkout:
```bash
hub/deploy/update.sh --check     # exit 0 up to date, 1 behind (2: could not reach origin)
hub/deploy/update.sh --dry-run   # what it would do, including a bet it would wait for; changes nothing
hub/deploy/update.sh             # update now
tail hub/data/update.log
```
`git fetch` runs from a systemd user unit with no ssh-agent: the checkout's `origin` must be reachable without a prompt (https, or a key with no passphrase).

## Behaviour that is fixed by the spec
- **Dry-run by default.** Placement logs what it would do and reports `placed` with `dry_run: true`.
- **Kill switch** at `data/KILL`, global, no code change, honoured by scrapes and placements.
- **Idempotency**: `bet_id` and `msg_id` are never processed twice.
- **Failures**: no auto-retry, no re-routing. `failed` is reported and escalated to the local AI.
- **Scraping is the Worker's** (`scraper/`): real headful Chrome with one persistent profile, no stealth, budgets in code (generic sources 60/h and 400/day), 24 h cool-off and alert on any challenge, daily 09:00 heartbeat, silence means broken.
- **Bet Finder output is owner-scoped** (`source_scope: owner`): never offered to other clients.
- **BookieBashing** is not a scrape source without written permission.

## Testing the contract with Render
`pytest` runs the real link against `puntmore_hub/link/mock_render.py` over a localhost socket, and validates every example in `schemas/examples/` against `schemas/protocol.schema.json`. Against a real Render build: `puntmore link-check wss://host/ws --key <key>`.

## Status
Scaffold. The client engine, link protocol, inbox, escalation path and CLI are implemented and tested with fakes; the Bet Finder's sources run in the Worker. Not yet done: the phone placement adapter, the IMAP tipster source kind, the kick-off-window sweep schedule, and the Render end of the WebSocket.
