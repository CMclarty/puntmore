# hub/ — the Hub

Hub side of the Puntmore 2-Up Engine, in the same repo as the Render service it talks to (`render/api/`) and the Scraper (`scraper/`), which does every page load on the Hub. Read `docs/decisions.md` first; it wins over this file. The wire contract between this and Render is `PROTOCOL.md` + `schemas/`; change them together. One machine on a residential connection that owns everything touching a bookmaker site or a phone. The cloud side (Render) owns pricing, the 2-up decision model and the per-client ledgers, and never loads a bookmaker page.

```
Render (render/, cloud)                Hub (hub/ + scraper/, one repo with Render)
───────────────────────                ───────────────────────────────────────────
2-up engine, Bet Finder orchestrator   hub/ link/     persistent outbound WebSocket to Render
odds API client, ledgers    ◄────────► hub/ inbox     the Scraper's tip and event drops -> Render, escalations
  /api/2up/ws                          hub/ mail      the tipster mailbox (IMAP) -> the same tips and escalations
                                       hub/ placement client engine: bet lists → phones, reports back
                 bet lists, kill switch hub/ escalation local AI (Hermes) owns what happens after a failure
                 reports, tips, outcomes hub/ web      the Hub Console
                                       scraper/       the Scraper: one real Chrome, every page load, budgets
```

## Layout
```
puntmore_hub/
  config.py        settings from .env; nothing hard-coded
  schemas.py       the Render contract: Bet, Report, CandidateTip, ScrapeJob, Envelope
  store.py         SQLite: tips, bets, outbox, escalations
  inbox.py         the Scraper's drops (tips, events) -> stored tips queued for Render, escalations
  mail.py          the tipster mailbox (IMAP) -> the same stored tips and escalations, through inbox.ingest_rows
  tipsters/        one parser per email tipster (PARSERS), named in data/tipsters.toml
  link/client.py   reconnecting WS client, durable outbox, idempotent inbound
  placement/       engine.py (client engine) · adapters/{dry_run,phone}.py
  escalation.py    hands failures to Hermes with a self-contained brief; records the outcome
  alerts.py        Discord/log alerts with de-dup; daily heartbeat
  update.py        self-update: fast-forward to origin/main, reinstall, restart (`puntmore update`)
  cli.py           `puntmore ...`
  web/             the Hub Console (`puntmore web`): one page, every component's state, the kill switch
deploy/update.sh   thin wrapper around `puntmore update`, run by the update timer
deploy/systemd/    link daemon, 09:00 heartbeat timer, 10-minute update timer, the Hub Console
deploy/hermes-skills/  the Scraper-ops runbook the scraper-manager agent follows (`puntmore update` copies it into that profile)
data/              runtime state (git-ignored): hub.sqlite, KILL, tipsters.toml, mail-status.json
```

## Relationship to `scraper/`
The Scraper (`scraper/`) is the only thing on the Hub that loads a page: Paddy Power pages kept open (and one Bookie Bashing page, `bb-saves`, an open item for Callum in `docs/decisions.md`), and the Bet Finder's sources as `generic` targets that are loaded, parsed and closed, all under one Chrome, one guard and one alert path. For a Bet Finder target the Scraper writes one JSON file per run into `<scraper data>/hub/tips/` and, when something needs a decision, into `<scraper data>/hub/events/`. Each file is one envelope of the protocol (`scraper_tips`, `scraper_event` in `schemas/protocol.schema.json`). `puntmore inbox` (run by the link daemon every 30 s) turns those into deduplicated tips queued for Render and into escalations for Hermes: a source escalation goes to the `scraper-manager` profile (`PUNTMORE_HERMES_SCRAPER_PROFILE`), the rest to `PUNTMORE_HERMES_PROFILE`. `puntmore health` reads the Scraper's heartbeat and `status.json` from the same directory (`PUNTMORE_SCRAPER_DATA`, the native service's data dir `/opt/puntmore-scraper/data`; the old name `PUNTMORE_WORKER_DATA` is still read for one release; in the Docker fallback, the container's volume on the host).

## Tipster emails
The Bet Finder's email tipsters (spec, "Tipsters by email") arrive in one dedicated mailbox that the Hub polls over IMAP with SSL (`PUNTMORE_IMAP_HOST`, `_PORT` 993, `_USER`, `_PASSWORD`, `_FOLDER` INBOX, `_EVERY_MIN` 10); a blank host means not configured. Render never polls a mailbox and the Scraper never reads one. Which senders are tipsters is `data/tipsters.toml` (copy `tipsters.example.toml`; `PUNTMORE_TIPSTERS_FILE` points elsewhere): a `name` (the source id Render sees), a `from` rule (an address, or a domain suffix, matched case-insensitively), a `parser` in `puntmore_hub/tipsters/` with the same row contract as the Scraper's parsers, a `trust_weight` and an `enabled` flag; it is validated and re-read every poll, and a file that will not load is reported and that poll does nothing. `puntmore mail` (run by the link daemon on its own thread) fetches the messages the mailbox has not shown us (`UNSEEN`, then flagged), skips any whose Message-ID the `mail_seen` table already holds, hands each message from a tipster to its parser, mints the `tip_id` there, at first sight, and stores the rows down the inbox's own path (`inbox.ingest_rows`), so a mail tip is a tip like any other on its way to Render. A parser that reads zero rows or raises is an `unhealthy_source` escalation for that tipster (subject and error, never the body); mail from no configured tipster is ignored and counted; a mailbox that cannot be reached is one de-duplicated alert and the poll stops. `puntmore mail --dry-run` prints what the parsers would store and changes nothing, not even the mailbox flags. Each poll writes `data/mail-status.json`, which `puntmore status` shows and `puntmore health` judges (unhealthy if the last poll errored or is older than three intervals while a host is configured).

## Setup (from `hub/`)
```bash
python3 -m venv ~/.venvs/puntmore && ~/.venvs/puntmore/bin/pip install -e '.[web,dev]'
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
puntmore status                      one screen: mode, the Scraper's targets and their state
puntmore inbox                       one pass over the Scraper's drops
puntmore mail [--dry-run]            one poll of the tipster mailbox (--dry-run: what the parsers read, nothing stored)
puntmore health                      exit 1 if the Scraper is down, a source is blocked or unhealthy, or an escalation is open
puntmore kill on|off|status          the kill switch (a file: data/KILL)
puntmore escalation list|show|resolve
puntmore tips                        candidate bets found
puntmore heartbeat                   the daily summary, on demand
puntmore link                        the Render link daemon (also runs the inbox and the mail poll)
puntmore link-check <url> --key K    handshake with a Render build
puntmore web                         the Hub Console on PUNTMORE_WEB_BIND (default http://127.0.0.1:8090)
puntmore update [--dry-run|--check]  fast-forward to origin/main, reinstall, restart (deploy/update.sh)
python -m scraper status|dump|resume the Scraper's own commands, from scraper/
```

## Hub Console
One web page, served on the Hub itself, that shows what every Hub component is doing, live: a status strip (Scraper, Link, Placement, Escalations, Tips, Model, Hermes, Render), each chip coloured up / warn / down / off / unknown with one line of detail, and a tab per component that polls every 5 s and says "stale" when its last good poll is over 20 s old. It is plain HTML, CSS and JS shipped in `puntmore_hub/web/static/`: no build, no CDN, so it loads with the box offline. It reads the same things the CLI reads, through the same functions; it never loads a page and never talks to a bookmaker.

```bash
~/.venvs/puntmore/bin/pip install -e '.[web,dev]'          # FastAPI + uvicorn are the optional `web` extra
cp deploy/systemd/puntmore-web.service ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now puntmore-web
# open http://127.0.0.1:8090   (or run it by hand: puntmore web / python -m puntmore_hub.web, --bind host:port)
```

**Actions** are POSTs that need the header `X-Puntmore-Token` equal to `PUNTMORE_WEB_TOKEN` (type it into the Token box on the page; that browser remembers it). With the token blank every action answers 403 and the buttons are greyed out; the read-only views still work. The actions are exactly: kill switch on (one click, any time); kill switch off (type `OFF` to confirm; only Cal turns it off); resolve an escalation with an outcome; one inbox pass; link-check; heartbeat now; and the Scraper tab's target edits (below). Nothing else: no Scraper commands (`resume` stays the Scraper's own CLI), no placement triggers, no `.env` writes. Every action, done or refused, is a line in `data/console-actions.log`.

**The Scraper tab** is the one place a person changes what the Scraper loads and how often. Its table is `scraper.targets.summary()` over `<PUNTMORE_SCRAPER_DATA>/targets.toml` and `status.json`: every target (disabled ones muted) with its site, mode, `every_min`, `stale_min`, priority, window, parser and post, and the Scraper's last word on it (state, last ok, next due, rows, detail). "Next due" is last load + `every_min`, approximate because the Scraper jitters each reload ×0.75–1.35; a target with no `every_min` reloads when its page has been quiet for `stale_min`. Per row: enable / disable, edit (`every_min`, `stale_min`, `priority`, `window`, `min_rows`, `trust_weight`, `wait_for`, `url`; blank clears to the default) and remove (type the name to confirm); below, an "Add target" form with the fields of `targets add` (site `paddypower`, `bookiebashing` or `generic`; added disabled unless "enabled now" is ticked). Every write is a POST under the token rule, logged like the others: `/api/actions/scraper/targets/add`, `…/<name>/set`, `…/<name>/enable`, `…/<name>/disable`, `…/<name>/remove` (body `{"confirm": "<name>"}`). Each one is `scraper/targets.py`'s own function, so the rules are the CLI's: a refusal (a frequency below the floor, a bookmaker host on a generic target) is the library's message, shown beside the field, and nothing is written; the library's lock held by Hermes or the CLI answers 409 "being changed by something else", try again. After a write the tab re-reads at once; the Scraper applies the change within 60 s. No targets file yet shows an empty table and the add form (the first add creates it). Under it, the site budgets from `status.json` (loads this hour and today, cool-off, needs resume) are read-only, with the exact `python -m scraper resume <site>` command to run on the Hub when one needs it. On a phone (under 700 px) the tables become one card per row.

`scraper` is not an installed package: the console imports `scraper.targets` from `PUNTMORE_SCRAPER_SRC` (the directory holding `scraper/`; blank = the repo this `hub/` is in, so the console and the library are always the same commit). It uses an importable `scraper` if there is one (`PYTHONPATH=.` from the repo root) and otherwise loads the package by file location, without touching `sys.path`.

**The LAN warning.** `PUNTMORE_WEB_BIND` defaults to `127.0.0.1:8090`, this box only. To open it from a phone, bind it to the box's LAN address (e.g. `PUNTMORE_WEB_BIND=192.168.1.20:8090`). That exposes the actions to anyone on the LAN, so the token is then required: set a long random `PUNTMORE_WEB_TOKEN` first. The reads have no token at all: anyone on the LAN can see bets, balances, tips, escalation details and the last 50 lines of the Hermes gateway journal. There is no TLS, so the token crosses the LAN in clear; treat it as a LAN-only secret. The console answers only to the Host names it was bound for (localhost, the bound address, this machine's hostname), which stops a web page in a browser on this box from reading it through DNS rebinding; bind `0.0.0.0` and that check is off.

Settings (`.env`): `PUNTMORE_WEB_BIND`, `PUNTMORE_WEB_TOKEN`, `PUNTMORE_SCRAPER_SRC` (above), `PUNTMORE_LLAMA_URL` (llama-server, default `http://127.0.0.1:8080`), `PUNTMORE_RENDER_HTTP` (the https base of Render for its `/api/ready`; blank = the Render card says "not configured").

What the states mean: **down** is a source that is there and says it is broken (stale or missing Scraper heartbeat, a failed unit, Render not ready or unreachable); **warn** needs a look (a blocked or unhealthy source, open escalations, kill switch on, failed bets, a message Render has not acked for 5 min, no tip for a day); **off** is not configured or not running and allowed not to be (the link not configured, llama-server not answering); **unknown** is when the console cannot tell (no Scraper data dir, no systemctl, no hub database yet). Reads never create the hub database.

## Updating the Hub
A change merged to `main` reaches the box within 10 minutes. `puntmore-update.timer` runs `deploy/update.sh` (a wrapper around `puntmore update`, code in `puntmore_hub/update.py`) against the box's checkout, `~/Projects/puntmore`:

1. `git fetch origin main`. If `origin/main` is not ahead of HEAD, nothing else happens.
2. `git pull --ff-only origin main`. Never a merge, never a reset.
3. `uv pip install -e 'hub[web]' --python ~/.venvs/puntmore/bin/python` (plain `hub` while there is no `web` extra; the venv's own pip if uv is not on PATH).
4. `systemctl --user restart` each of `puntmore-link`, `puntmore-web` and `puntmore-heartbeat.timer` that exists and is enabled, and the Scraper's `puntmore-scraper` when the pull changed `scraper/` (a restart reopens its pages, out of the sites' budgets; `scraper/requirements.txt` is installed into the venv first when it changed).

Every run, including "up to date", appends one line to `data/update.log`: time, outcome, branch, the commit moved from and to. A failure is also alerted through `alerts.py` (Discord, de-duplicated per kind for `PUNTMORE_ALERT_DEDUP_HOURS`) and is never retried inside the run.

**What blocks it** (logged; the ones marked *alert* also go to Discord):
- **A bet in flight**: any bet in the store with status `received` or `placing`. Nothing is pulled or restarted; the next tick tries again. A bet stuck in flight for over 30 minutes is a crash, not a placement: *alert*, and it keeps blocking until a human settles that bet.
- **A bet that arrives during the install**: the code is pulled and installed, the restart waits. `data/UPDATE_PENDING` holds the commit, so the next tick restarts even though the checkout is current. An install or restart failure leaves the same marker, so the next tick (not this run) tries once more.
- **Not a fast-forward** (the checkout has commits that are not on `origin/main`): *alert*, left alone. The box's checkout belongs on `main`; a feature branch that was squash-merged diverges.
- **Local edits to tracked files**, or a **detached HEAD**: *alert*, left alone.
- **Another run** holding `data/update.lock`.

It does not copy unit files. When a pull changes `hub/deploy/systemd/` or `scraper/deploy/native/puntmore-scraper.service`, the run sends a warning naming the files; run `scraper/deploy/native/install.sh` for the Scraper's, and copy the others to `~/.config/systemd/user/` and `systemctl --user daemon-reload` by hand.

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
- **Kill switch** at `data/KILL`, global, no code change, honoured by placements (scrapes run on the Scraper's own schedule).
- **Idempotency**: `bet_id` and `msg_id` are never processed twice.
- **Failures**: no auto-retry, no re-routing. `failed` is reported and escalated to the local AI.
- **Scraping is the Scraper's** (`scraper/`): real headful Chrome with one persistent profile, no stealth, budgets in code (generic sources 60/h and 400/day), 24 h cool-off and alert on any challenge, daily 09:00 heartbeat, silence means broken.
- **Bet Finder output is owner-scoped** (`source_scope: owner`): never offered to other clients.
- **Bookie Bashing** is not a Bet Finder source, and the 2-Up rescue model is tuned against BB's tracker by hand (BB's terms ban automated access). The Scraper's one BB target, `bb-saves`, predates that rule: OPEN for Callum in `docs/decisions.md` (keep it as the named exception, or disable it).

## Testing the contract with Render
`pytest` runs the real link against `puntmore_hub/link/mock_render.py` over a localhost socket, and validates every example in `schemas/examples/` against `schemas/protocol.schema.json`. Against a real Render build: `puntmore link-check wss://puntmore.com/api/2up/ws --key <key>` (the `2up` subdomain once it exists).

## Status
Scaffold. The client engine, link protocol, inbox, escalation path and CLI are implemented and tested with fakes; the Bet Finder's sources run in the Scraper. Not yet done: the phone placement adapter and the kick-off-window sweep schedule. (The Render end of the WebSocket and the IMAP tipster source, `mail.py`, landed in Nov 2026.)
