# puntmore scraper

One real Chrome on a Linux server, keeping a few Paddy Power and Bookie Bashing
pages **open** and listening to what they receive. After the first load the
site's own scripts keep each page fresh, so most targets cost no further
requests. Runs unattended in Docker and updates itself from GHCR.

**What it reads is `data/targets.toml` on the Hub**, edited by Hermes; an
edit applies without a restart. Its state goes to `data/status.json`, which
the Hub reads. Nothing on Puntmore configures it: Puntmore only receives the
prices it posts. Any Linux box with Docker can run it.

The parsers (`parsers/`, one module each) are written only from what the real
pages were seen to do. `bb_saves` DRIVES Bookie Bashing's Bet Builder (select
the game, type each line, read "Total Odds" — see its docstring; needs the
box's Chrome logged in over VNC). `pp_most` is unwritten: see its docstring
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
  open so you can sort it over VNC.
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
  fingerprint. Chrome is set up the ordinary way for one UK reader
  (en-GB from the environment, London time, a 1600x1000 window on a
  1920x1080 screen) and started as a person's Chrome, not a test run
  (Playwright's `--enable-automation` is not passed); `selfcheck` shows
  exactly what a site sees.
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

## Changing targets
`targets.toml` is changed only through `scraper/targets.py`: this CLI, the
Hub Console and Hermes's scraper-manager all call it, so there is one config
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
python -m scraper targets set pp-epl-most --matches '{"max_tabs": 2, "market_tabs": ["Corners"]}'
python -m scraper targets enable racing-tips
python -m scraper targets disable racing-tips
python -m scraper targets remove racing-tips
```

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
  not reported yet).
- Saves are atomic (temp file + rename in the same directory) under an
  `flock` on `targets.toml.lock`, so the console, the CLI and Hermes cannot
  interleave and the running Scraper never reads half a file. The file is
  rewritten whole, so comments are lost: the first save keeps the
  hand-written file as `targets.toml.bak`.
- The running Scraper picks a change up within 60 s (its re-read of the
  file). A changed target has its tabs closed and reopened on the next tick,
  whatever the field: today even an `every_min` change costs a fresh load.
- `priority` (whole number, default 0; higher wins a site's contended hourly
  budget) and `window` (`"HH:MM-HH:MM"` local time, may wrap midnight; the
  target is not loaded outside it) are stored, validated and shown, but the
  runner does not act on them yet (`TODO(runner)` in `runner.py`).

## Server setup (once)
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

**Updates:** push to `main` with changes under `scraper/`. CI runs the
tests and publishes the image, and within 15 minutes the server's cron restarts
onto it. Profile, budget and cool-off state live in `data/`, so they survive
the restart.

## Logging in to Bookie Bashing, or clearing a check (VNC)
VNC listens on the server's `127.0.0.1:5900` only. From your machine:

```sh
ssh -L 5900:localhost:5900 you@server     # then open a VNC viewer on localhost:5900
```

You'll see the live Chrome window. Log in on the Bookie Bashing tab. The
next read finds it logged in, and you get a "clear again" alert.

## Day to day
```sh
docker logs -f puntmore-scraper
docker exec puntmore-scraper python -m scraper status          # budget, cool-offs, each target, heartbeat
docker exec puntmore-scraper python -m scraper dump bb-saves    # save the open page(s) to data/dumps/
docker exec puntmore-scraper python -m scraper resume paddypower
docker exec puntmore-scraper python -m scraper check            # load targets.toml and list the targets
docker exec puntmore-scraper python -m scraper selfcheck        # what a site sees of the browser (no network)
```

Each match tab's status says what its market-tab clicks did (`loaded`,
`clicked, nothing new`, `not found`, ...). A tab that is not found saves the
page once per run to `data/samples/<run>/<target>-market-tab-not-found/`.

Samples: each tab keeps its first distinct payloads on disk
(`SCRAPER_CAPTURE_TAB_MB`, default 8, inside the run's `SCRAPER_CAPTURE_MB`),
first-party hosts only, repeats of the same URL+size skipped, plus the page's
HTML once. `index.jsonl` marks each market-tab click and labels what followed it.

Health: Docker's healthcheck fails if the loop hasn't ticked for 5 minutes.
Set `SCRAPER_HEALTHCHECK_URL` (e.g. healthchecks.io) for an alert when the
whole server goes quiet.

## Targets
One file: `data/targets.toml` (see `targets.example.toml`), or the file
`SCRAPER_TARGETS` names. It lives on the Hub and is changed only through
`scraper/targets.py` (Changing targets, above). The running
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
