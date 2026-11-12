# scraper/ — the Scraper service

The Scraper service: the one real Chrome on the Hub, on its own virtual screen. It reads every
page any tool needs and fills what OddsPapi lacks: Paddy Power pages kept
**open** (it listens to what they receive), Finder's sources loaded and
closed, and one Bookie Bashing page (`bb-saves`, the register's one named
exception). After
the first load the site's own scripts keep each page fresh, so most targets
cost no further requests. Runs as the user service `puntmore-scraper` on its
own virtual screen, the user service `puntmore-screen` (below), updated by
`puntmore-update.timer` with the rest of the Hub. Docker is the fallback for a
box with no screen.

**What it reads is `data/targets.toml` on the Hub**, changed only through
`scraper/targets.py` (the Console, the CLI, HubAI); an edit applies without a
restart. Its state goes to `data/status.json`, which the Hub reads. Its rows go
to Scraper control as drop files (`hub/PROTOCOL.md`), Paddy's prices too: a
price target's rows are `scraper_prices` files that the Hub's inbox posts to
Render. The Scraper sends Render nothing, and nothing on Render configures it.

The parsers (`parsers/`, one module each) are written only from what the real
pages were seen to do. `bb_saves` DRIVES Bookie Bashing's Bet Builder (select
the game, type each line, read "Total Odds" — see its docstring; needs the
box's Chrome logged in, which the Scraper does itself with the login in
`.env`, below). It reads only games kicking off within 48 hours (Callum,
11 Nov 2026); with none that near it rests: its page is closed and nothing
loads until 48 hours before the next game it saw, or a day, whichever is
sooner. `status` shows it `paused` with the reason; `wake bb-saves` ends the
rest by hand.
`pp_most` reads Paddy's most-corners / most-cards three-ways (written from
the 29 Oct 2026 capture; see its docstring).

## What it will and won't do
- Opens pages on `paddypower.com`, `bookiebashing.net` (one target, the named
  exception in `docs/decisions.md`) and Finder's `generic` sources, within
  budgets (`behaviour.py`; these are the defaults): Paddy 15 loads/h and
  120/day, Bookie Bashing 8/h and 120/day, generic 60/h and 400/day.
  `targets.toml` cannot raise them; only `python -m scraper behaviour set`
  changes them ("Changing how it behaves", below).
- No Paddy traffic between 01:00 and 07:00 (Europe/London). Its pages are
  closed for those hours.
- Paddy 403/429 or Cloudflare check → every Paddy page closes and a 24 h
  cool-off starts. It also needs `resume`, and you get an alert.
- Bookie Bashing logout → it signs in once, on the page already open, with the
  login in `.env` (`login.py`), then reloads the tool through the guard. At
  most 3 sign-ins in 6 hours, then it waits for a person (a site that keeps
  ending the session, e.g. when you log in on another device). A failed
  sign-in (refused, no form, still logged out after it, a form the site's
  script did not send, or the 3-in-6-hours limit), a logout with no login in
  `.env`, or a check → one alert and a back-off, with the tab left open so
  you can sort it on the Scraper's screen (over VNC, "Its own screen" below;
  in Docker, the container's VNC). After a failed
  sign-in it does not try again until it sees the site logged in or you run
  `resume bookiebashing`, restarts included. Each sign-in is written to
  `data/login.json` before the form is sent; if that file cannot be written
  it does not sign in at all and the alert says "cannot write login.json"
  (once per start of the service).
- A block is also caught on a page that is already open: 3+ refused
  requests in 10 min, a Cloudflare page, or a logout. Every block alert
  carries a screenshot (also in `data/blocks/`). A Paddy cool-off alerts once
  when its hours are served and it waits on `resume`, and once when it runs
  again.
- Paced like one person with a few tabs: page loads are spaced (Paddy 90 s+,
  Bookie Bashing 60 s+, and 20-45 s between any two), so nothing opens in a
  burst at startup or after quiet hours; timed reloads are jittered once per
  load. Market tabs are clicked through the mouse, then read down.
- It never answers a challenge and never fakes a fingerprint. It types one
  login only, Bookie Bashing's, from `.env`; a person is needed there only
  after a failed sign-in or for a challenge. On the Hub Chrome keeps its
  own defaults: en-GB from the box's locale, London time, the box's own GPU
  and fonts, on its own virtual 1920x1080 screen. In Docker, with
  no screen, Chrome gets a 1600x1000 window on Xvfb's 1920x1080 screen and
  the en-GB language list by flag. Either way it is started as a person's
  Chrome, not a test run (Playwright's `--enable-automation` is not passed);
  `selfcheck` shows exactly what a site sees, the WebGL renderer included.
- **It runs on the Hub's residential connection**, which is less likely to be
  challenged than a datacentre IP. If Paddy challenges it anyway, the
  scraper stops and tells you. It does not work
  around the challenge.

## Finder's sources
Any other site, as a `generic` target (see `targets.example.toml`): loaded,
parsed once and closed, under its own budget in `behaviour.py` (60 loads/h,
400/day across all generic targets, 20 s+ apart, never Paddy or Bookie
Bashing). The parser returns tips; `post = "hub:tips"` writes them to
`data/hub/tips/` for the Hub's inbox, which stores them for HubAI to judge for
Finder. Too few rows (`min_rows`) or a block raises an alert here and an event
in `data/hub/events/` that the Hub turns into an escalation for HubAI.

## Changing targets
A target is a job: a page the Scraper reads, on a schedule (`every_min` or
`stale_min`), on chosen `days`, inside a `window`, until an end date
(`until`). `targets.toml` is changed only through `scraper/targets.py`: this CLI, the
Console and HubAI's scraper-manager all call it, so there is one config
store. It works on `SCRAPER_TARGETS`, else `SCRAPER_DATA_DIR/targets.toml`
(`--file` for another), and needs no browser.

```sh
python -m scraper targets list                 # every target, joined to status.json
python -m scraper targets show pp-epl-most --json
python -m scraper targets add --name racing-tips --site generic \
    --url https://tips.example.org/racing --parser racing_tips --post hub:tips \
    --every-min 360 --min-rows 2 --trust-weight 0.5 --disabled
python -m scraper targets set pp-epl-most --every-min 15 --priority 2 --window 07:00-23:30
python -m scraper targets set racing-tips --window none     # "none" clears a field
python -m scraper targets set racing-tips --days fri,sat --until 2026-11-30
python -m scraper targets set racing-tips --days none --until none   # every day, forever
python -m scraper targets set pp-epl-most --matches '{"max_tabs": 2, "market_tabs": ["Corners"]}'
python -m scraper targets set pp-epl-most --matches '{"link_re": "-v-", "max_tabs": 3,
    "market_tabs": ["Corners", "Cards"],
    "listings": ["https://www.paddypower.com/football/spanish-la-liga",
                 "https://www.paddypower.com/football/italian-serie-a"]}'
python -m scraper targets enable racing-tips
python -m scraper targets disable racing-tips
python -m scraper targets remove racing-tips
python -m scraper targets log racing-tips -n 50   # the job's own log, newest last
```

- `--matches` replaces the whole `[target.matches]` table: a field it leaves
  out goes back to its default, so give every field the job uses (as the
  `listings` example above does).
- Every change is validated as a whole by `config._target` (sites, hosts,
  clamps) before anything is written. A value the clamps would change is
  refused, not quietly raised: `--every-min 2` is an error, not 5. A refusal
  exits 2 with the reason (`{"error": "..."}` with `--json`).
- `--json` prints machine output: `list` the `targets.summary()` list,
  `show`/`add`/`set`/`enable`/`disable` that target's entry, `remove`
  `{"removed": name, "targets": [names left]}`. Each entry is every field of
  the target plus `status`: the running Scraper's last report on it from
  `status.json` (`state`, `detail`, `last_load`, `last_parse`,
  `rows_posted`, `reported_at`), or `null` when it has none (disabled, or
  not reported yet), and `schedule`: the job's schedule in words
  (`targets.schedule_words`, "every 15 min · Fri, Sat · 07:00–23:30 · until
  30 Nov 2026"), which `list` prints too.
- Saves are atomic (temp file + rename in the same directory) under an
  `flock` on `targets.toml.lock`, so the Console, the CLI and HubAI cannot
  interleave and the running Scraper never reads half a file. The file is
  rewritten whole, so comments are lost: the first save keeps the
  hand-written file as `targets.toml.bak`.
- The running Scraper picks a change up within 60 s (its re-read of the
  file). A change to only `every_min`, `stale_min`, `near_min`, `near_hours`,
  `priority`, `window`, `days`, `until`, `min_rows` or `trust_weight` is applied in place: the open tabs stay and
  are rescheduled (a timed reload is redrawn from the last load), so it
  costs no load. A change to `url`, `site`, `mode`, `parser`, `post`,
  `wait_for` or `matches` closes the target's tabs and reopens them on the
  next tick. Disabling or removing a target closes its tabs.
- `priority` (higher goes first when a site's hourly budget is short),
  `window` (`"HH:MM-HH:MM"` local time; outside it the target's tabs close),
  `days` (`--days fri,sat`: only on those days; none = every day) and `until`
  (`--until 2026-11-30`: the last day it runs) are acted on by the runner
  (`schedule.py`). Off its days a job is `paused`, "not a run day"; after
  `until` it is `finished`, "ended <until>"; either way its tabs close.
- Each job keeps its own log, `SCRAPER_DATA_DIR/logs/<name>.log`
  (`joblog.py`): every line the runner prints about that job (window and
  days, kick-off sweep, load and parser failures, a `min_rows` breach and
  its recovery, market tabs, retired match tabs, dumps, signing in) and its
  deliveries ("3 row(s) handed to the Hub (hub:tips), 2 left out"): a
  delivery is logged when what was handed or left out changes, else at most
  every 10 min. A block of its site is a line in every job of that site,
  never with the page's url: "blocked: <reason> - backing off" (Bookie
  Bashing) once per block, as its alert, or "blocked: <reason> - every page
  closed, cooling off 24 h, then needs resume"; and "no longer blocked" when
  a back-off ends (unless "signed in" already says so). One
  `{"ts": <epoch>, "msg": "..."}` per line, a message cut at 2000
  characters. Other site-wide lines (paused, a cool-off served or over,
  `config: ...`) stay in the journal only. Past 512 KB a
  log is rewritten to its newest lines that fit in 256 KB, 1000 at most.
  `targets log <name> [-n 50]` prints it (`--json`: `{"name": name, "lines": [{"ts", "msg"}, ...]}`);
  `targets remove` removes it.

## Changing how it behaves
How the Scraper behaves - each site's limits, quiet hours, the cool-off and
back-off, every gap and jitter between loads, how it arrives on a page, clicks
and reads down it, and the Bookie Bashing bet builder's pacing - is one
catalogue, `scraper/behaviour.py`: every setting with its default, unit,
bounds and a line saying what it is. Read that file first when something has
gone wrong. What a block means, the allowed hosts and never answering a
challenge are not settings, and nothing about what the browser shows a site
is.

```sh
python -m scraper behaviour                        # every setting: group, key, value, default; * = changed
python -m scraper behaviour set paddypower.per_hour 10
python -m scraper behaviour set load.any_gap_s 30-60      # a range: low-high
python -m scraper behaviour set generic.quiet_hours true
python -m scraper behaviour set quiet.hours 1-7           # local hours, start-end
python -m scraper behaviour reset paddypower.per_hour     # back to its default
python -m scraper behaviour reset --all
python -m scraper behaviour --json                 # {"file", "changed", "problems", "settings": [...]}
```

- A change is written to `SCRAPER_DATA_DIR/behaviour.toml`: flat
  `"key" = value` lines, only the settings that differ from their default,
  each under a comment with its default and meaning. It is written only
  through `behaviour.py` (this CLI): checked against the setting's kind and
  bounds, under an `flock` on `behaviour.toml.lock`, saved atomically. A value
  outside the bounds, or an unknown key, is refused with a message naming the
  setting (exit 2; `{"error": "..."}` with `--json`), and nothing is written.
- The running service takes a change up within 60 s, without a restart: it
  checks the file once a minute (with `targets.toml`) and loads it when it
  has moved; a deleted file is every setting back to its default. The guard
  takes the new limits at once; each setting that moved is one line,
  `key: old -> new`, in the journal (`behaviour: ...`) and in
  `data/logs/behaviour.log`. A value already drawn from a range keeps until
  its next draw (a page's next timed reload or fresh page, the next round of
  market-tab clicks, a site's back-off, the next load's spacing, the
  service's own restart time). A file that does not load keeps the values in
  use, and an unknown key or a refused value keeps that setting's value; each
  is said once per edit, and nothing stops. `status.json` carries
  `"behaviour": {"changed": <settings not at their default>, "problems": [...]}`.
  Every other command reads the file when it runs.
- `--json` on `set` and `reset <key>` prints that setting's entry (`key`,
  `group`, `kind`, `value`, `default`, `lo`, `hi`, `unit`, `says`,
  `changed`); `reset --all` prints `{"reset": [the keys that were changed]}`.

## Running on the Hub (the default)
Since 5 Nov 2026 the Scraper runs on the Hub itself, not in a container, so the
sites see a person's Chrome: real GPU, real fonts. Measured that day, Chrome
157 in the container reported no WebGL at all (Xvfb, no GPU) and the
container's fonts; the same binary on the Hub reports `ANGLE (AMD, AMD Radeon
8060S Graphics (radeonsi strix_halo ACO), OpenGL ES 3.2)`. Since 11 Nov 2026
its screen is its own virtual one, not Callum's desktop (below).

What it needs: `sway` and `wayvnc` (`sudo apt install sway wayvnc`),
`/usr/bin/google-chrome`, the venv `~/.venvs/puntmore` with
`requirements.txt` installed, the Hub's user lingering (`loginctl
enable-linger`, so the user services run with nobody logged in) and in the
`render` and `video` groups (the GPU), and `/opt/puntmore-scraper/.env` and
`/opt/puntmore-scraper/data/targets.toml` as below.

```sh
docker stop puntmore-scraper                       # if the Docker Scraper runs: never both
scraper/deploy/native/install.sh                   # from the box's checkout, ~/Projects/puntmore
crontab -l | grep -v puntmore-scraper/update.sh | crontab -   # the container's updater
```

`install.sh` is idempotent: it checks `sway` and `wayvnc` are there, gives
your user `/opt/puntmore-scraper` (sudo, once; the container's files belong to
its uid), copies `deploy/native/puntmore-screen.service` and
`deploy/native/puntmore-scraper.service` into `~/.config/systemd/user/` with
the checkout it was run from in their paths, enables both (`default.target`;
an older install's `graphical-session.target` link is dropped first), and
restarts the screen, then the Scraper. It refuses while the container
`puntmore-scraper` runs, and so does the unit (`deploy/native/preflight.sh`,
which also clears the profile lock the container's Chrome left behind);
`deploy/run.sh` refuses the other way round.

The unit runs `~/.venvs/puntmore/bin/python -m scraper run` with the data in
`/opt/puntmore-scraper/data` (heartbeat, `status.json`, budget, profile,
targets, the Hub's drops), so the Hub reads it where it always has. It
restarts on any exit (the Scraper exits on its own every 1.5-2 h for a fresh
driver), gives Chrome 90 s to close cleanly, and is capped at 4 GB.

**Updates:** a merge to `main` that touches `scraper/` reaches the box through
`puntmore-update.timer`, which pulls the checkout and restarts
`puntmore-scraper` (and installs `requirements.txt` when it changed). A change
to either unit file needs `install.sh` run again; a change to `screen.conf`
needs `systemctl --user restart puntmore-screen` (which restarts the Scraper
too).

**Its own screen** (Callum, 11 Nov 2026). The Scraper's Chrome runs on a
virtual 1920x1080 screen, not on Callum's desktop, so nothing it does (start,
open a tab, take a screenshot) can take his screen, and it runs whether or not
he is logged in. Two user units:

- `puntmore-screen.service`: a headless `sway` (`WLR_BACKENDS=headless`) on
  the box's real GPU with its own Xwayland, configured by
  `deploy/native/screen.conf`: one output, no borders, and `wayvnc` on
  `127.0.0.1:5910` (this machine only: it has no password). It writes its
  Xwayland `DISPLAY` to `$XDG_RUNTIME_DIR/puntmore-screen.env` and counts as
  started only once it has. It never sees the desktop's display variables.
- `puntmore-scraper.service`: requires the screen and starts after it, reads
  `DISPLAY` from that file, and is restarted with it. `browser.py` starts
  Chrome on X11 as before; nothing about the browser changes, and a page
  sees the screen it is really on (measured: screen 1920x1080, dpr 1, the
  Radeon's WebGL, `navigator.webdriver` false).

To look at it, or to sign in by hand (Bookie Bashing after a failed sign-in,
a check), open the screen in a VNC viewer on the Hub:

```sh
remmina -c vnc://127.0.0.1:5910
```

and use the Chrome window there with the mouse and keyboard; close the viewer
when done (the Scraper carries on either way). Once per box, undo the
own-workspace setting this replaces (it named the window for GNOME's Auto Move
Windows extension, which made Chrome drag the desktop to workspace 2):

```sh
dconf reset /org/gnome/shell/extensions/auto-move-windows/application-list
```

The Docker fallback is unchanged (below).

```sh
journalctl --user -u puntmore-scraper -f
journalctl --user -u puntmore-screen -f
systemctl --user status|restart|stop puntmore-scraper
# from ~/Projects/puntmore; the commands find the service through its data dir:
export SCRAPER_DATA_DIR=/opt/puntmore-scraper/data
~/.venvs/puntmore/bin/python -m scraper status
~/.venvs/puntmore/bin/python -m scraper selfcheck   # what a site sees, WebGL renderer included
~/.venvs/puntmore/bin/python -m scraper dump bb-saves
~/.venvs/puntmore/bin/python -m scraper wake bb-saves   # end a job's rest (rest.py)
~/.venvs/puntmore/bin/python -m scraper resume paddypower
```

## Running in Docker (a box with no screen)
The fallback: one real Chrome on Xvfb inside a container, for a box with no
desktop. The image sets `SCRAPER_IN_CONTAINER=1`, which is what gives Chrome
its fixed window and language flags (`browser.py`). Expect no WebGL there.
The container will not start while `data/heartbeat` is under 90 s old
(`deploy/entrypoint.sh`): another Scraper, the native one, runs on that data
dir. So a cron `update.sh` left behind on the Hub cannot put a second Chrome
on the profile.

## Setting up a box with no screen (once)
Any Linux host with Docker, amd64, about 3 GB RAM free. (On the Hub itself the
Scraper service runs natively on its own screen, above; this is the fallback only.)

```sh
sudo mkdir -p /opt/puntmore-scraper/data && cd /opt/puntmore-scraper
# from this folder of the repo:
#   deploy/run.sh, deploy/update.sh  -> /opt/puntmore-scraper/
#   .env.example                     -> /opt/puntmore-scraper/.env           (fill in)
#   targets.example.toml             -> /opt/puntmore-scraper/data/targets.toml (then edit)
chmod 600 .env
# The image is private on GHCR: log in once with a PAT that has read:packages
echo "$GHCR_PAT" | docker login ghcr.io -u cmclarty --password-stdin
./run.sh
( crontab -l 2>/dev/null; echo '*/15 * * * * /opt/puntmore-scraper/update.sh >> /opt/puntmore-scraper/update.log 2>&1' ) | crontab -
```

**Updates:** push to `main` with changes under `scraper/`. CI runs the
tests; `puntmore-update.timer` fast-forwards the Hub and restarts the service
within 10 minutes (in the Docker fallback, CI publishes the image and the
box's 15-minute cron restarts onto it). Profile, budget and cool-off state live in `data/`, so they survive
the restart.

### Logging in to Bookie Bashing after a failed sign-in, or clearing a check (VNC, Docker only)
VNC listens on the box's `127.0.0.1:5900` only. From your machine:

```sh
ssh -L 5900:localhost:5900 you@box        # then open a VNC viewer on localhost:5900
```

You'll see the live Chrome window. Log in on the Bookie Bashing tab. The
next read finds it logged in, and you get a "clear again" alert.

### Day to day (Docker)
```sh
docker logs -f puntmore-scraper
docker exec puntmore-scraper python -m scraper status          # budget, cool-offs, each target, heartbeat
docker exec puntmore-scraper python -m scraper dump bb-saves    # save the open page(s) to data/dumps/
docker exec puntmore-scraper python -m scraper wake bb-saves    # end a job's rest (rest.py)
docker exec puntmore-scraper python -m scraper resume paddypower
docker exec puntmore-scraper python -m scraper check            # load targets.toml and list the targets
docker exec puntmore-scraper python -m scraper selfcheck        # what a site sees of the browser (no network)
```

## Credentials (none in the repo)

In `/opt/puntmore-scraper/.env`: `SCRAPER_ALERT_WEBHOOK` (Discord), `SCRAPER_HEALTHCHECK_URL`
(optional), and `VNC_PASSWORD` on a box with no screen. It holds no Render key: the Hub posts
its prices (`PUNTMORE_RENDER_API_KEY` in the Hub's `.env`). The Scraper no longer reads
`SAVES_INGEST_KEY` or `PUNTMORE_URL`, so both can be deleted from `/opt/puntmore-scraper/.env`.
The Bookie Bashing login is `BB_USERNAME` and `BB_PASSWORD` (Callum's own; leave either blank
and a person logs in instead). The Scraper types it when its page is found logged out, once
per logout and at most 3 sign-ins in 6 hours, then it waits for a person; the password is never
logged or kept, and Chrome is not given either variable. A failed sign-in is noted in
`data/login.json` (with the times of recent sign-ins) and nothing is tried again until the site
is seen logged in (log in on the Hub's screen) or `python -m scraper resume bookiebashing` is
run, which forgets it and the sign-in times. The
session itself lives in the Chrome profile in the data directory; never share
`data/profile/`: it holds the login cookies.

## Each tab's state
Each match tab's status says what its market-tab clicks did (`loaded`,
`clicked, nothing new`, `not found`, ...). A tab that is not found saves the
page once per run to `data/samples/<run>/<target>-market-tab-not-found/`.

Samples: each tab keeps its first distinct payloads on disk
(`SCRAPER_CAPTURE_TAB_MB`, default 8, inside the run's `SCRAPER_CAPTURE_MB`),
first-party hosts only, repeats of the same URL+size skipped, plus the page's
HTML once. The sign-in's own response (it may repeat what was typed) is never
kept. `index.jsonl` marks each market-tab click and labels what followed it.

Health: on the Hub the Console's Scraper card goes down when the
heartbeat is 10 minutes stale (`PUNTMORE_SCRAPER_STALE_MINUTES`); in Docker the
healthcheck fails at 5.
Set `SCRAPER_HEALTHCHECK_URL` (e.g. healthchecks.io) for an alert when the
whole box goes quiet.

## Targets
One file: `data/targets.toml` (see `targets.example.toml`), or the file
`SCRAPER_TARGETS` names. It lives on the Hub and is changed only through
`scraper/targets.py` (Changing targets, above). The running
service checks it once a minute and applies an edit without a restart: a
removed target's tabs close, a new one opens on the next tick, and a changed
one is rescheduled in place or reopened (Changing targets, above). A
file that will not load (a half-written save, a bad target) is reported in the
log and the running targets are kept. The service writes its state to
`data/status.json` once a minute; the Hub reads it and `status` prints it.

Targets are of these kinds:
- **matches**: a league's listing page, e.g. a Paddy competition, whose match
  links are opened up to `max_tabs` at a time. Each match tab clicks the
  market's tabs once, then stays open. A tab that goes quiet is reloaded once,
  then retired. `listings` (in `[target.matches]`, at most 8, each on a host
  allowed for the site, none repeating `url` or each other) adds further
  competitions' listing pages to the same job (Callum, 12 Nov 2026): each is
  kept open in its own tab (`<name>:list`, then `<name>:list:1`, ...), loaded
  and relisted every `relist_min` on its own clock, one load per tick. Their
  match links are interleaved (the first of each listing, then the second of
  each, ...), so every competition gets a tab before any gets two;
  `max_tabs` is for the whole job. A listing tab is parsed like any other of
  the job's tabs.
- **page**: one URL kept open, e.g. the Bookie Bashing bet builder. It is
  reloaded every `every_min`, or only when it goes quiet for `stale_min`.
  Its parser may rest it (`rest.py`): the page closes and nothing loads
  until the time the parser gave, as outside a `window`; `bb_saves` does so
  when no game kicks off within 48 hours.
- **fetch**: a `generic` Finder source, loaded, parsed once and closed
  (above).

Timings are clamped to the floors in `config._target`, and the site budgets
(`behaviour.py`) stay the ceiling whatever the file says.

Six fields say when and who first (`schedule.py`):
- **`near_min`** and **`near_hours`** (default 3): the kick-off sweep. A
  parser's rows may carry a `kickoff` (ISO 8601; `pp_most` and `bb_saves`
  do); the nearest one still ahead is the target's next kick-off (`status`
  shows it). While it is within `near_hours`, the target reloads, or a fetch
  target runs, every `near_min` instead of `every_min`, and among targets of
  equal priority the nearest kick-off gets a short budget's next load. After
  the kick-off the normal interval is back. The spec's "every 2-4 h, ~30 min
  in the last 3 h before kick-off, nearest first" is `every_min = 180,
  near_min = 30`. The site's budget is still the ceiling; a sweep that would
  exceed it simply waits.
- **`window`** (`"HH:MM-HH:MM"`, local time, may wrap midnight as
  `"22:00-02:00"`; the start minute is inside, the end minute is not): the
  hours a target may load at all. Outside it nothing is loaded and its open
  tabs are closed, within a tick of the window's end; `status` shows it
  `paused`, "outside window". When the window starts it opens again through
  the normal spacing and guard; a fetch target runs at the start. No window
  means always. A site's quiet hours and cool-offs still apply inside it.
- **`days`** (a list of `mon` .. `sun`, no repeats; none = every day) and
  **`until`** (`"YYYY-MM-DD"`, the last day it runs; none = forever): the
  days a job runs. Off its days it is `paused`, "not a run day"; after
  `until` it is `finished`, "ended <until>", whatever its site is doing.
  Either way nothing loads and its tabs close, as outside the window. A
  window past midnight belongs to the day it started: a Saturday
  `"20:00-02:00"` job runs until 02:00 on Sunday, and with `until` set to
  that Saturday it finishes at 02:00 (`schedule.job_day`).
- **`priority`** (whole number, default 0): who gets a site's loads when they
  are short. Loads are spaced, so one goes out at a time, to the first target
  in the tick's order that wants one. While a site has at least as many
  loads left (this hour, or today if fewer) as targets wanting one, that
  order is file order. When more of its targets want a load than it has
  left, the highest priority goes first, ties to whichever has been waiting
  longer (overdue the longest). A lower-priority target still gets the loads
  a higher one does not want: priority orders the queue, it does not reserve
  budget.

## Tests
```sh
pip install -r scraper/requirements.txt -r scraper/requirements-dev.txt
python -m playwright install chromium   # or SCRAPER_CHROME_PATH=/usr/bin/google-chrome
python -m pytest scraper/tests -q      # from the repo root; includes a real browser against a fake localhost site
```
