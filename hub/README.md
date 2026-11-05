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
  store.py         SQLite: tips, bets, outbox, escalations
  inbox.py         the Worker's drops (tips, events) -> stored tips queued for Render, escalations
  link/client.py   reconnecting WS client, durable outbox, idempotent inbound
  placement/       engine.py (client engine) · adapters/{dry_run,phone}.py
  escalation.py    hands failures to Hermes with a self-contained brief; records the outcome
  alerts.py        Discord/log alerts with de-dup; daily heartbeat
  cli.py           `puntmore ...`
  web/             the Hub Console (`puntmore web`): one page, every component's state, the kill switch
deploy/systemd/    link daemon, 09:00 heartbeat timer, the Hub Console
deploy/hermes-skills/  the Worker-ops runbook the scraper-manager agent follows
data/              runtime state (git-ignored): hub.sqlite, KILL
```

## Relationship to `scraper/`
The Worker (`scraper/`) is the only thing on the Hub that loads a page: Paddy Power and Bookie Bashing pages kept open, and the Bet Finder's sources as `generic` targets that are loaded, parsed and closed, all under one Chrome, one guard and one alert path. For a Bet Finder target the Worker writes one JSON file per run into `<worker data>/hub/tips/` and, when something needs a decision, into `<worker data>/hub/events/`. `puntmore inbox` (run by the link daemon every 30 s) turns those into deduplicated tips queued for Render and into escalations for Hermes. `puntmore health` reads the Worker's heartbeat and `status.json` from the same directory (`PUNTMORE_WORKER_DATA`, the Docker volume on the host).

## Setup (from `hub/`)
```bash
python3 -m venv ~/.venvs/puntmore && ~/.venvs/puntmore/bin/pip install -e '.[web,dev]'
cp .env.example .env                                   # fill in Render URL/key, Discord webhook
puntmore init && pytest -q
```

Services (user units, headful Chrome needs the desktop session):
```bash
cp deploy/systemd/* ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now puntmore-link puntmore-heartbeat.timer
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
puntmore web                         the Hub Console on PUNTMORE_WEB_BIND (default http://127.0.0.1:8090)
python -m scraper status|dump|resume the Worker's own commands, from scraper/
```

## Hub Console
One web page, served on the Hub itself, that shows what every Hub component is doing, live: a status strip (Worker, Link, Placement, Escalations, Tips, Model, Hermes, Render), each chip coloured up / warn / down / off / unknown with one line of detail, and a tab per component that polls every 5 s and says "stale" when its last good poll is over 20 s old. It is plain HTML, CSS and JS shipped in `puntmore_hub/web/static/`: no build, no CDN, so it loads with the box offline. It reads the same things the CLI reads, through the same functions; it never loads a page and never talks to a bookmaker.

```bash
~/.venvs/puntmore/bin/pip install -e '.[web,dev]'          # FastAPI + uvicorn are the optional `web` extra
cp deploy/systemd/puntmore-web.service ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now puntmore-web
# open http://127.0.0.1:8090   (or run it by hand: puntmore web / python -m puntmore_hub.web, --bind host:port)
```

**Actions** are POSTs that need the header `X-Puntmore-Token` equal to `PUNTMORE_WEB_TOKEN` (type it into the Token box on the page; that browser remembers it). With the token blank every action answers 403 and the buttons are greyed out; the read-only views still work. The actions are exactly: kill switch on (one click, any time); kill switch off (type `OFF` to confirm; only Cal turns it off); resolve an escalation with an outcome; one inbox pass; link-check; heartbeat now. Nothing else: no target edits, no Worker commands, no placement triggers, no `.env` writes. Every action, done or refused, is a line in `data/console-actions.log`.

**The LAN warning.** `PUNTMORE_WEB_BIND` defaults to `127.0.0.1:8090`, this box only. To open it from a phone, bind it to the box's LAN address (e.g. `PUNTMORE_WEB_BIND=192.168.1.20:8090`). That exposes the actions to anyone on the LAN, so the token is then required: set a long random `PUNTMORE_WEB_TOKEN` first. The reads have no token at all: anyone on the LAN can see bets, balances, tips, escalation details and the last 50 lines of the Hermes gateway journal. There is no TLS, so the token crosses the LAN in clear; treat it as a LAN-only secret. The console answers only to the Host names it was bound for (localhost, the bound address, this machine's hostname), which stops a web page in a browser on this box from reading it through DNS rebinding; bind `0.0.0.0` and that check is off.

Settings (`.env`): `PUNTMORE_WEB_BIND`, `PUNTMORE_WEB_TOKEN`, `PUNTMORE_LLAMA_URL` (llama-server, default `http://127.0.0.1:8080`), `PUNTMORE_RENDER_HTTP` (the https base of Render for its `/api/ready`; blank = the Render card says "not configured").

What the states mean: **down** is a source that is there and says it is broken (stale or missing Worker heartbeat, a failed unit, Render not ready or unreachable); **warn** needs a look (a blocked or unhealthy source, open escalations, kill switch on, failed bets, a message Render has not acked for 5 min, no tip for a day); **off** is not configured or not running and allowed not to be (the link before Render's endpoint exists, llama-server not answering); **unknown** is when the console cannot tell (no Worker data dir, no systemctl, no hub database yet). Reads never create the hub database.

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
