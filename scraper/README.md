# puntmore scraper

The Scraper: one real Chrome on the Hub's desktop, keeping a few Paddy Power
and Bookie Bashing pages **open** and listening to what they receive. After
the first load the site's own scripts keep each page fresh, so most targets
cost no further requests. Runs as the user service `puntmore-scraper` on the
logged-in desktop session, updated by `puntmore-update.timer` with the rest of
the Hub. Docker is the fallback for a box with no screen.

**What it reads is `data/targets.toml` on the Hub**, edited by Hermes; an
edit applies without a restart. Its state goes to `data/status.json`, which
the Hub reads. Nothing on Puntmore configures it: Puntmore only receives the
prices it posts.

The parsers (`parsers/`, one module each) are written only from what the real
pages were seen to do. `bb_saves` DRIVES Bookie Bashing's Bet Builder (select
the game, type each line, read "Total Odds" — see its docstring; needs the
box's Chrome logged in: on the Hub's screen, or over VNC in Docker). `pp_most` is unwritten: see its docstring
for what the samples hold.

## What it will and won't do
- Opens pages only on `paddypower.com` and `bookiebashing.net`, within fixed
  budgets (`sites.py`): Paddy 15 loads/h and 120/day, Bookie Bashing 8/h and
  120/day. Nothing in config can raise them.
- No Paddy traffic between 01:00 and 07:00 (Europe/London). Its pages are
  closed for those hours.
- Paddy 403/429 or Cloudflare check → every Paddy page closes and a 24 h
  cool-off starts. It also needs `resume`, and you get an alert.
- Bookie Bashing logout or check → one alert and a back-off, with the tab left
  open so you can sort it on the Hub's screen (over VNC in Docker).
- A block is also caught on a page that is already open: 3+ refused
  requests in 10 min, a Cloudflare page, or a logout. Every block alert
  carries a screenshot (also in `data/blocks/`). A Paddy cool-off alerts once
  when its hours are served and it waits on `resume`, and once when it runs
  again.
- Paced like one person with a few tabs: page loads are spaced (Paddy 90 s+,
  Bookie Bashing 60 s+, and 20-45 s between any two), so nothing opens in a
  burst at startup or after quiet hours; timed reloads are jittered once per
  load. Market tabs are clicked through the mouse, then read down.
- It never answers a challenge, never types a password, and never fakes a
  fingerprint. On the Hub's desktop Chrome keeps its own defaults on the real
  display: en-GB from the session's locale, London time, the box's own GPU,
  fonts and screen. In Docker, with no screen, Chrome gets a 1600x1000 window
  on Xvfb's 1920x1080 screen and the en-GB language list by flag. Either way
  it is started as a person's Chrome, not a test run (Playwright's
  `--enable-automation` is not passed); `selfcheck` shows exactly what a
  site sees, the WebGL renderer included.
- **A datacentre IP is more likely to be challenged than a home one.** If Paddy
  challenges this server, the scraper stops and tells you. It does not work
  around the challenge.

## Bet Finder sources
Any other site, as a `generic` target (see `targets.example.toml`): loaded,
parsed once and closed, under its own budget in `sites.py` (60 loads/h,
400/day across all generic targets, 20 s+ apart, never Paddy or Bookie
Bashing). The parser returns tips; `post = "hub:tips"` writes them to
`data/hub/tips/` for the Hub's `puntmore inbox`, which queues them for Render.
Too few rows (`min_rows`) or a block raises an alert here and an event in
`data/hub/events/` that the Hub turns into an escalation for the local AI.

## Running on the Hub's desktop (the default)
Since 5 Nov 2026 the Scraper runs on the Hub's real desktop session, so the
sites see a person's Chrome: real GPU, real fonts, real screen. Measured that
day, Chrome 157 in the container reported no WebGL at all (Xvfb, no GPU) and
the container's fonts; the same binary on the Hub's
Wayland session reports `ANGLE (AMD, AMD Radeon 8060S Graphics (radeonsi
strix_halo ACO), OpenGL ES 3.2)`.

What it needs: a desktop session logged in as the Hub's user (the unit is
tied to `graphical-session.target`: it starts with the session and stops
with it, so the Hub should log in automatically), `/usr/bin/google-chrome`,
the venv `~/.venvs/puntmore` with `requirements.txt` installed, and
`/opt/puntmore-scraper/.env` and `/opt/puntmore-scraper/data/targets.toml`
as below. GNOME hands the session's `WAYLAND_DISPLAY`, `DISPLAY` and
`XAUTHORITY` to the user manager, so the unit sets nothing about the display.

```sh
docker stop puntmore-scraper                       # if the Docker Scraper runs: never both
scraper/deploy/native/install.sh                   # from the box's checkout, ~/Projects/puntmore
crontab -l | grep -v puntmore-scraper/update.sh | crontab -   # the container's updater
```

`install.sh` is idempotent: it gives your user `/opt/puntmore-scraper` (sudo,
once; the container's files belong to its uid), copies
`deploy/native/puntmore-scraper.service` into `~/.config/systemd/user/` with
the checkout it was run from as its working directory, and enables and starts
it. It refuses while the container `puntmore-scraper` runs, and so does the
unit (`deploy/native/preflight.sh`, which also clears the profile lock the
container's Chrome left behind); `deploy/run.sh` refuses the other way round.

The unit runs `~/.venvs/puntmore/bin/python -m scraper run` with the data in
`/opt/puntmore-scraper/data` (heartbeat, `status.json`, budget, profile,
targets, the Hub's drops), so the Hub reads it where it always has. It
restarts on any exit (the Scraper exits on its own every 1.5-2 h for a fresh
driver), gives Chrome 90 s to close cleanly, and is capped at 4 GB.

**Updates:** a merge to `main` that touches `scraper/` reaches the box through
`puntmore-update.timer`, which pulls the checkout and restarts
`puntmore-scraper` (and installs `requirements.txt` when it changed). A change
to the unit file itself needs `install.sh` run again.

Logging in to Bookie Bashing or looking at a check: the Chrome window is on
the Hub's screen. No VNC.

```sh
journalctl --user -u puntmore-scraper -f
systemctl --user status|restart|stop puntmore-scraper
# from ~/Projects/puntmore; the commands find the service through its data dir:
export SCRAPER_DATA_DIR=/opt/puntmore-scraper/data
~/.venvs/puntmore/bin/python -m scraper status
~/.venvs/puntmore/bin/python -m scraper selfcheck   # what a site sees, WebGL renderer included
~/.venvs/puntmore/bin/python -m scraper dump bb-saves
~/.venvs/puntmore/bin/python -m scraper resume paddypower
```

## Running in Docker (a box with no screen)
The fallback: one real Chrome on Xvfb inside a container, for a box with no
desktop. The image sets `SCRAPER_IN_CONTAINER=1`, which is what gives Chrome
its fixed window and language flags (`browser.py`). Expect no WebGL there.
The container will not start while `data/heartbeat` is under 90 s old
(`deploy/entrypoint.sh`): another Scraper, the desktop one, runs on that data
dir. So a cron `update.sh` left behind on the Hub cannot put a second Chrome
on the profile.
Any Linux host with Docker, amd64, about 3 GB RAM free.

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

**Updates (Docker):** push to `main` with changes under `scraper/`. CI runs the
tests and publishes the image, and within 15 minutes the server's cron restarts
onto it. Profile, budget and cool-off state live in `data/`, so they survive
the restart.

### Logging in to Bookie Bashing, or clearing a check (VNC, Docker only)
VNC listens on the server's `127.0.0.1:5900` only. From your machine:

```sh
ssh -L 5900:localhost:5900 you@server     # then open a VNC viewer on localhost:5900
```

You'll see the live Chrome window. Log in on the Bookie Bashing tab. The
next read finds it logged in, and you get a "clear again" alert.

### Day to day (Docker)
```sh
docker logs -f puntmore-scraper
docker exec puntmore-scraper python -m scraper status          # budget, cool-offs, each target, heartbeat
docker exec puntmore-scraper python -m scraper dump bb-saves    # save the open page(s) to data/dumps/
docker exec puntmore-scraper python -m scraper resume paddypower
docker exec puntmore-scraper python -m scraper check            # load targets.toml and list the targets
docker exec puntmore-scraper python -m scraper selfcheck        # what a site sees of the browser (no network)
```

## Each tab's state
Each match tab's status says what its market-tab clicks did (`loaded`,
`clicked, nothing new`, `not found`, ...). A tab that is not found saves the
page once per run to `data/samples/<run>/<target>-market-tab-not-found/`.

Samples: each tab keeps its first distinct payloads on disk
(`SCRAPER_CAPTURE_TAB_MB`, default 8, inside the run's `SCRAPER_CAPTURE_MB`),
first-party hosts only, repeats of the same URL+size skipped, plus the page's
HTML once. `index.jsonl` marks each market-tab click and labels what followed it.

Health: on the desktop the Hub Console's Worker card goes down when the
heartbeat is 10 minutes stale (`PUNTMORE_WORKER_STALE_MINUTES`); in Docker the
healthcheck fails at 5.
Set `SCRAPER_HEALTHCHECK_URL` (e.g. healthchecks.io) for an alert when the
whole server goes quiet.

## Targets
One file: `data/targets.toml` (see `targets.example.toml`), or the file
`SCRAPER_TARGETS` names. It lives on the Hub and Hermes edits it. The running
service checks it once a minute and applies an edit without a restart: a
removed or changed target's tabs close, a new one opens on the next tick. A
file that will not load (a half-written save, a bad target) is reported in the
log and the running targets are kept. The service writes its state to
`data/status.json` once a minute; the Hub reads it and `status` prints it.

Targets are of these kinds:
- **matches**: a league's listing page, e.g. a Paddy competition, whose match
  links are opened up to `max_tabs` at a time. Each match tab clicks the
  market's tabs once, then stays open. A tab that goes quiet is reloaded once,
  then retired.
- **page**: one URL kept open, e.g. the Bookie Bashing bet builder. It is
  reloaded every `every_min`, or only when it goes quiet for `stale_min`.
- **fetch**: a `generic` Bet Finder source, loaded, parsed once and closed
  (above).

Timings are clamped to the floors in `config._target`, and `sites.py`
budgets stay the ceiling whatever the file says.

## Tests
```sh
pip install -r scraper/requirements.txt -r scraper/requirements-dev.txt
python -m playwright install chromium   # or SCRAPER_CHROME_PATH=/usr/bin/google-chrome
python -m pytest scraper/tests -q      # from the repo root; includes a real browser against a fake localhost site
```
