# hub/ — the Hub

The Hub is Callum's box and the one program on it. Its page loads are the Scraper service's
(`scraper/`); this directory is the rest: Scraper control's intake (the daemon's inbox of the
Scraper's drop files, posting the Scraper's Paddy prices to Render, and its tipster-mailbox
poll), HubAI's side (escalations, the Finder CLI and the reading of AW 26's slip photos), the
placement engine the Bet Placer starts from (`docs/placer-spec.md`), and the Console. Read
`docs/decisions.md` first; it wins over this file.

```
Render (render/, cloud)                  Hub (hub/ + scraper/)
───────────────────────                  ──────────────────────────────────────────────
                                         Scraper service (scraper/): one real Chrome, every page
                                           │ drop files (PROTOCOL.md): tips, events, prices
                                           ▼
Paddy board  ◄── POST /api/paddy/* ───── Scraper control: hub/ daemon (inbox + mailbox), targets.py
                                           │ tips (hub.sqlite), escalations
                                           ▼
Finder board ◄── POST /api/finder/rows ── HubAI (Hermes): judges tips, takes escalations
Paddy, 2-Up, Finder feeds ── GET ───────► Bet Placer: staking logic, phone interface → DeviceFarmer → phones
Finder board ◄── placed | gone ────────── Bet Placer
AW 26 photos ── take, image ────────────► hub/ daemon, AW 26 loop: a waiting photo taken, HubAI started on it
AW 26 slips ◄── /aw26/slips/record ────── HubAI (`puntmore aw26 record`): the slip it read
                                         Console (hub/ web): every part, live; the control page
```

## Layout
```
puntmore_hub/
  config.py        settings from .env; nothing hard-coded
  daemon.py        `puntmore daemon`: the inbox pass every 30 s, the price drops every 10 s, the mailbox poll and the AW 26 photo loop (when `PUNTMORE_AW26_READ=1`) on their own threads
  aw26.py          AW 26: Render's photo queue, Paddy's board at a moment, a slip recorded (`puntmore aw26`); printed times to UTC
  aw26_loop.py     the daemon's AW 26 loop: the oldest waiting photo taken, read by HubAI, checked on Render after
  inbox.py         the Scraper's drop files: tips, events -> stored tips for HubAI, escalations; prices -> Render
  mail.py          the tipster mailbox (IMAP) -> the same stored tips and escalations, through inbox.ingest_rows
  tipsters/        one parser per email tipster (PARSERS), named in data/tipsters.toml
  finder.py        Finder: the tips HubAI has not posted; its rows and the Bet Placer's status to Render over HTTPS
  schemas.py       the drop files' types; Tip; the placement engine's Bet and Report
  store.py         SQLite: tips, finder_posted, mail_seen, bets, balances, escalations, alerts
  placement/       the placement engine: engine.py · adapters/{dry_run,phone}.py (README: the adapter interface)
  escalation.py    hands failures to HubAI with a self-contained brief; records the outcome
  alerts.py        Discord/log alerts with de-dup; daily heartbeat
  update.py        self-update: fast-forward to origin/main, reinstall, restart (`puntmore update`)
  cli.py           `puntmore ...`
  web/             the Console (`puntmore web`): one page, every part's state, the kill switch
PROTOCOL.md        the Scraper's drop files; schemas/ holds their JSON Schema and three examples
deploy/update.sh   thin wrapper around `puntmore update`, run by the update timer
deploy/systemd/    the daemon (puntmore-link.service), 09:00 heartbeat timer, 10-minute update timer, the Console
deploy/hermes-skills/  HubAI's runbooks, which `puntmore update` copies in: Scraper ops (scraper-manager), AW 26 slip reading
                   (the AW 26 reader's profile and the chief of staff)
data/              runtime state (git-ignored): hub.sqlite, KILL, tipsters.toml, mail-status.json, daemon-status.json, aw26/ (HubAI's output per photo)
```

## The Scraper's drop files
The Scraper service is the only thing on the Hub that loads a page: Paddy Power pages kept open
(and one Bookie Bashing page, `bb-saves`, the register's single named exception), and Finder's
sources as `generic` targets that are loaded, parsed and closed, all under one Chrome, one guard
and one alert path. For a Finder target it writes one JSON file per run into
`<scraper data>/hub/tips/` and, when something needs a decision, one into
`<scraper data>/hub/events/` (`PROTOCOL.md`). The daemon's inbox pass (`puntmore inbox`, every
30 s) turns those into deduplicated tips for HubAI to judge (Finder, below) and into escalations
for HubAI: a source escalation goes to the `scraper-manager` profile
(`PUNTMORE_HERMES_SCRAPER_PROFILE`), the rest to `PUNTMORE_HERMES_PROFILE`. `puntmore health`
reads the Scraper's heartbeat and `status.json` from the same directory (`PUNTMORE_SCRAPER_DATA`,
the native service's `/opt/puntmore-scraper/data`; the old name `PUNTMORE_WORKER_DATA` is still
read; on a box with no screen, the container's volume).

Paddy's prices are drop files too. For a target whose `post` is a Paddy route
(`/api/paddy/most-prices`, `/api/paddy/saves-fair`) the Scraper writes one `scraper_prices` file
per parse of each tab into `<scraper data>/hub/prices/`. The inbox posts each one to Render as
`{"prices": rows}` on that route (`inbox.post_prices`, with Finder's `PUNTMORE_RENDER_HTTP` and
`PUNTMORE_RENDER_API_KEY`), oldest first, and moves it to `done/prices/`. The
daemon checks the folder every 10 s, so a price reaches Render within about 10 s of the parse;
`puntmore inbox` posts them too. While Render is down or the key is not set, the files wait in
order for the next pass; a refused file or a broken one is moved aside and logged, and a file
older than 15 min is moved aside unsent, so an old price never reaches the board as a fresh one.
The full table is in `PROTOCOL.md`, "Prices: what the Hub does with them"; the Console's Daemon
card warns while drops wait and when the last pass that handled one moved any aside, with the
reason (Render's HTTP status, no key, a network error, a broken file, the 15 min cut-off).

## Tipster emails
Finder's email tipsters arrive in one dedicated mailbox that the Hub polls over IMAP with SSL (`PUNTMORE_IMAP_HOST`, `_PORT` 993, `_USER`, `_PASSWORD`, `_FOLDER` INBOX, `_EVERY_MIN` 10); a blank host means not configured. Render never polls a mailbox and the Scraper never reads one. Which senders are tipsters is `data/tipsters.toml` (copy `tipsters.example.toml`; `PUNTMORE_TIPSTERS_FILE` points elsewhere): a `name` (the source id HubAI sees), a `from` rule (an address, or a domain suffix, matched case-insensitively), a `parser` in `puntmore_hub/tipsters/` with the same row contract as the Scraper's parsers, a `trust_weight` and an `enabled` flag; it is validated and re-read every poll, and a file that will not load is reported and that poll does nothing. `puntmore mail` (run by the daemon on its own thread) fetches the messages the mailbox has not shown us (`UNSEEN`, then flagged), skips any whose Message-ID the `mail_seen` table already holds, hands each message from a tipster to its parser, mints the `tip_id` there, at first sight, and stores the rows down the inbox's own path (`inbox.ingest_rows`), so a mail tip is a tip like any other on its way to HubAI and Finder. A parser that reads zero rows or raises is an `unhealthy_source` escalation for that tipster (subject and error, never the body); mail from no configured tipster is ignored and counted; a mailbox that cannot be reached is one de-duplicated alert and the poll stops. `puntmore mail --dry-run` prints what the parsers would store and changes nothing, not even the mailbox flags. Each poll writes `data/mail-status.json`, which `puntmore status` shows and `puntmore health` judges (unhealthy if the last poll errored or is older than three intervals while a host is configured).

## Finder
Finder is Callum's tips board (`docs/finder-spec.md`): the tips the Hub stores (the drop files, the tipster mailbox) are judged here by HubAI and the verdicts posted to Render, which keeps, serves and expires them; the Bet Placer reads them back from Render.

- `puntmore finder new [--limit N]` — the stored tips HubAI has not posted yet (no `finder_posted` row), oldest first, one JSON object per line: `tip_id, source_id, event, market, selection, advised_price, advised_stake_units, tip_time, expiry, origin, first_seen`.
- `puntmore finder post FILE` (or `-` for stdin) — a JSON list of rows to `POST /api/finder/rows`; prints Render's answer `{stored, updated, refused, duplicates, rows}`. Each row is the spec's: `tip_id` (as given by `new`, never re-minted), `origin`, `sport` (`football` | `horse`), `bookmaker`, `event` ("Home v Away" or "Course HH:MM"), `event_id` (the canonical key, below), `kickoff` (UTC), `market` (`win` | `each_way` for a horse), `selection`, `odds` (the price the tip quotes), `fair` (the fair price it quotes, or null), `advised_stake` (pounds, or null), `reason` (one line), `status`, optional `home`, `away`, `line`, `side`, `expiry`. An `offered` row must carry `kickoff`, `odds` and `fair` (without one, Render refuses the whole post, 422); an `asked` row, and the `rejected` answer to it, may leave all three out. On a 200 each tip is marked in `finder_posted` with the status Render's answer (`rows: [{tip_id, status}]`) says it stored, not the one HubAI sent: an offer stored as a duplicate is marked `rejected`, a withdrawal of a placed row `placed`, and a tip with no row on Render stays new. On anything else nothing is marked and the command fails, so the tips stay new.
- `puntmore finder skip TIP_ID [--reason TEXT]` — a tip HubAI cannot post at all (another sport, nothing readable): marked `skipped` with the reason, so it leaves `finder new` and never reaches Render. Refused for an unknown tip or one already posted (withdraw that one on Render by re-posting it `rejected`).
- `puntmore finder status TIP_ID placed|gone` — the Bet Placer's report; a row already final answers `{"refused": "final"}`.

HubAI's contract (the spec's step 2, which Callum tightens in use): **offer** when the tip gives both prices and the quoted price beats the fair one (its advised stake where it gives one, and a one-line reason); **ask** Callum on Telegram when the tip gives no fair price, cannot be read with confidence, or names an event HubAI cannot place on a day and time (post it `asked` with what HubAI wants to know as the reason, leaving out the kick-off, odds or fair it does not have, and re-post `offered` or `rejected` on his answer; an asked row with no kick-off expires at the tip's own expiry, else 48 h after HubAI last posted it); **reject** when the quoted price does not beat the fair one, with the reason; **withdraw** an offered row (a tipster's retraction) by re-posting it `rejected` with the reason, which Render refuses once the Bet Placer has placed it; **skip** (`finder skip`) a tip that cannot be posted even as a question. No price is looked up. A second row for a bet already asked, offered or placed is stored `rejected` as a duplicate; after kick-off a re-post changes nothing.

**`event_id`, the canonical fixture key.** Render's duplicate rule matches the fixture on `event_id` (the event's words, folded, only when a row has none), so two tipsters' wordings of one fixture must give the same key. HubAI fills it on every row it can place on a day and time:
- football: `football:<YYYY-MM-DD>:<home>-v-<away>`, the date of kick-off in UK time, home side first;
- racing: `horse:<YYYY-MM-DD>:<course>:<HH:MM>`, the date and the off in UK time, 24-hour clock;
- each name is the club's or the course's full common name, not the tip's nickname ("Man Utd" and "Spurs" are `manchester-united` and `tottenham-hotspur`; "FC"/"AFC" left off), in lower-case ASCII (accents dropped: "Atlético" is `atletico`), words joined by hyphens, no punctuation ("St. Mirren" is `st-mirren`, "Brighton & Hove Albion" is `brighton-hove-albion`).

Examples: `football:2026-11-07:manchester-united-v-tottenham-hotspur`, `horse:2026-11-07:cheltenham:13:30`. A key spelt two ways makes a duplicate look like a new bet, so this is the part of the contract to get right; leave `event_id` out of an asked row that has no day and time.

Env: `PUNTMORE_RENDER_HTTP` (the https base of Render) and `PUNTMORE_RENDER_API_KEY`, a Render API client key carrying `ingest` (in Render's shell: `python -m app.apikeys create <name> --tools ingest`), sent as a Bearer key. The inbox posts the Scraper's Paddy prices with the same two settings, and AW 26 uses them too, with the key also granted `aw26` and `cards` (`python -m app.apikeys grant <name> --tools aw26,cards`; the AW 26 loop runs only when `PUNTMORE_AW26_READ=1` as well; `hub/AGENTS.md` has the loop and its runbook).

## Setup (from `hub/`)
```bash
python3 -m venv ~/.venvs/puntmore && ~/.venvs/puntmore/bin/pip install -e '.[web,dev]'
cp .env.example .env                                   # fill in Render URL/key, Discord webhook
puntmore init && pytest -q
```

Services (user units; the Scraper's Chrome has its own virtual screen, `puntmore-screen`):
```bash
cp deploy/systemd/* ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now puntmore-link puntmore-heartbeat.timer puntmore-update.timer   # puntmore-link runs the daemon
```

## Everyday commands
```
puntmore status                      one screen: mode, the Scraper's targets and their state
puntmore inbox                       one pass over the drop files: tips, events, then posts waiting price drops
puntmore mail [--dry-run]            one poll of the tipster mailbox (--dry-run: what the parsers read, nothing stored)
puntmore health                      exit 1 if the Scraper is down, a source is blocked or unhealthy, or an escalation is open
puntmore kill on|off|status          the kill switch (a file: data/KILL)
puntmore escalation list|show|resolve
puntmore tips                        recent stored tips
puntmore finder new [--limit N]      tips HubAI has not posted to Finder yet, one JSON object per line
puntmore finder post FILE|-          POST HubAI's rows (a JSON list) to Render's /api/finder/rows
puntmore finder skip TIP [--reason T] a tip HubAI cannot post: skipped, so `finder new` moves on
puntmore finder status TIP placed|gone  the Bet Placer's report on an offered row
puntmore aw26 photos                 AW 26's photo queue on Render: the photos not done, oldest first
puntmore aw26 take ID                take a waiting photo and save its image under data/aw26/
puntmore aw26 fail ID --reason T     a photo HubAI cannot make a slip of
puntmore aw26 upload FILE [--source page|telegram]  put a photo on the queue (the chief of staff, for one sent on Telegram)
puntmore aw26 board --at T [--match "Home v Away"]  Paddy's board as it stood at T (printed, or ISO with its offset)
puntmore aw26 record FILE|- [--dry-run]  record a slip HubAI read (--dry-run: print the body, send nothing)
puntmore heartbeat                   the daily summary, on demand
puntmore daemon                      the daemon: the inbox every 30 s, the price drops every 10 s, the mailbox poll, and the AW 26 photo loop when switched on (`puntmore link` is the same command)
puntmore web                         the Console on PUNTMORE_WEB_BIND (default http://127.0.0.1:8090)
puntmore update [--dry-run|--check]  fast-forward to origin/main, reinstall, restart (deploy/update.sh)
python -m scraper status|dump|resume the Scraper's own commands, from scraper/
```

## The Console
One web page, served on the Hub itself, that shows what every Hub component is doing, live: a status strip (Scraper, Daemon, Placement, Escalations, Tips, Model, HubAI, Render), each chip coloured up / warn / down / off / unknown with one line of detail, and a tab per component that polls every 5 s and says "stale" when its last good poll is over 20 s old. It is plain HTML, CSS and JS shipped in `puntmore_hub/web/static/`: no build, no CDN, so it loads with the box offline. It reads the same things the CLI reads, through the same functions; it never loads a page and never talks to a bookmaker.

```bash
~/.venvs/puntmore/bin/pip install -e '.[web,dev]'          # FastAPI + uvicorn are the optional `web` extra
cp deploy/systemd/puntmore-web.service ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now puntmore-web
# open http://127.0.0.1:8090   (or run it by hand: puntmore web / python -m puntmore_hub.web, --bind host:port)
```

**Actions** are POSTs that need the header `X-Puntmore-Token` equal to `PUNTMORE_WEB_TOKEN` (type it into the Token box on the page; that browser remembers it). With the token blank every action answers 403 and the buttons are greyed out; the read-only views still work. The actions are exactly: kill switch on (one click, any time); kill switch off (type `OFF` to confirm; only Callum turns it off); resolve an escalation with an outcome; one inbox pass; heartbeat now; and the Scraper tab's target edits (below). Nothing else: no Scraper commands (`resume` stays the Scraper's own CLI), no placement triggers, no `.env` writes. Every action, done or refused, is a line in `data/console-actions.log`.

**The Scraper tab** is the one place a person changes what the Scraper loads and how often. It shows one tile per job, from `scraper.targets.summary()` over `<PUNTMORE_SCRAPER_DATA>/targets.toml` and `status.json` (disabled ones muted): the name (tap it for the rows of its `latest/<name>.json`) and its state, then its site and url, its schedule in words (`every 15 min · Fri, Sat · all day · until 30 Nov 2026`), its state and the Scraper's detail, last load, last read and rows sent, and the newest 5 lines of its log (`<data>/logs/<name>.log`, times local, with the weekday on a line not from today). "all lines" opens the newest 200 in that tile (`GET /api/scraper/log/<name>?n=200`), read again each time the tab refreshes (a failed read keeps the last lines and says why above the tiles); "fewer lines" closes it back to the 5. Per tile: enable / disable, edit (`every_min`, `stale_min`, `near_min`, `near_hours`, `priority`, `window`, the run days (none ticked is every day), an end date `until`, `min_rows`, `trust_weight`, `wait_for`, `url`; blank clears to the default) and remove (type the name to confirm, which also deletes its log); below, an "Add target" form with the fields of `targets add` (site `paddypower`, `bookiebashing` or `generic`; run days and an end date as in the edit form; added disabled unless "enabled now" is ticked). Every write is a POST under the token rule, logged like the others: `/api/actions/scraper/targets/add`, `…/<name>/set`, `…/<name>/enable`, `…/<name>/disable`, `…/<name>/remove` (body `{"confirm": "<name>"}`). Each one is `scraper/targets.py`'s own function, so the rules are the CLI's: a refusal (a frequency below the floor, a bookmaker host on a generic target) is the library's message, shown beside the field, and nothing is written; the library's lock held by HubAI or the CLI answers 409 "being changed by something else", try again. After a write the tab re-reads at once; the Scraper applies the change within 60 s. No targets file yet shows no tiles and the add form (the first add creates it). Under it, the site budgets from `status.json` (loads this hour and today, cool-off, needs resume) are read-only, with the exact `python -m scraper resume <site>` command to run on the Hub when one needs it. On a phone (under 700 px) the tiles stack one per row and the tables become one card per row.

`scraper` is not an installed package: the console imports `scraper.targets` from `PUNTMORE_SCRAPER_SRC` (the directory holding `scraper/`; blank = the repo this `hub/` is in, so the console and the library are always the same commit). It uses an importable `scraper` if there is one (`PYTHONPATH=.` from the repo root) and otherwise loads the package by file location, without touching `sys.path`.

**The LAN warning.** `PUNTMORE_WEB_BIND` defaults to `127.0.0.1:8090`, this box only. To open it from a phone, bind it to the box's LAN address (e.g. `PUNTMORE_WEB_BIND=192.168.1.20:8090`). That exposes the actions to anyone on the LAN, so the token is then required: set a long random `PUNTMORE_WEB_TOKEN` first. The reads have no token at all: anyone on the LAN can see bets, balances, tips, escalation details and the last 50 lines of HubAI's gateway journal (Hermes). There is no TLS, so the token crosses the LAN in clear; treat it as a LAN-only secret. The console answers only to the Host names it was bound for (localhost, the bound address, this machine's hostname), which stops a web page in a browser on this box from reading it through DNS rebinding; bind `0.0.0.0` and that check is off.

Settings (`.env`): `PUNTMORE_WEB_BIND`, `PUNTMORE_WEB_TOKEN`, `PUNTMORE_SCRAPER_SRC` (above), `PUNTMORE_LLAMA_URL` (llama-server, default `http://127.0.0.1:8080`), `PUNTMORE_RENDER_HTTP` (the https base of Render for its `/api/ready`; blank = the Render card says "not configured").

What the states mean: **down** is a source that is there and says it is broken (stale or missing Scraper heartbeat, a failed unit, Render not ready or unreachable); **warn** needs a look (a blocked or unhealthy source, open escalations, kill switch on, failed bets, no inbox pass for 5 min, no tip for a day); **off** is not configured or not running and allowed not to be (Render's URL not set, llama-server not answering); **unknown** is when the console cannot tell (no Scraper data dir, no systemctl, no hub database yet). Reads never create the hub database.

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
