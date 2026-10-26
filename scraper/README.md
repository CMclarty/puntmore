# puntmore scraper

One real Chrome on a Linux server, keeping a few Paddy Power and Bookie Bashing
pages **open** and listening to what they receive. After the first load the
site's own scripts keep each page fresh, so most targets cost no further
requests. Runs unattended in Docker and updates itself from GHCR.

**What it reads is set on Puntmore's `/scraper` page** — leagues, markets,
timings — and it reports its state back there. The server is the control
plane; this is a worker. Any Linux box with Docker can run it.

**It captures; it does not price yet.** The parsers (`parsers/`, one module
each) are written only from `data/samples/` and `data/dumps/`. `pp_most` and
`bb_saves` are both unwritten: see their docstrings for what the samples hold.

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

## Server setup (once)
Any Linux host with Docker, amd64, about 3 GB RAM free.

```sh
sudo mkdir -p /opt/puntmore-scraper/data && cd /opt/puntmore-scraper
# from this folder of the repo:
#   deploy/run.sh, deploy/update.sh  -> /opt/puntmore-scraper/
#   .env.example                     -> /opt/puntmore-scraper/.env           (fill in)
#   targets.example.toml             -> /opt/puntmore-scraper/data/targets.toml (offline fallback only)
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
docker exec puntmore-scraper python -m scraper check            # the targets it would run, and from where
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
Set on Puntmore's `/scraper` page (`scraper` grant). The box fetches
`GET /api/scraper/config` with `SAVES_INGEST_KEY` every 5 minutes and applies
a change without a restart: a removed or changed target's tabs close, a new
one opens on the next tick. It posts `POST /api/scraper/status` once a minute
(also written to `data/status.json`, which `status` prints).

Where the targets come from, first that works:
1. `SCRAPER_TARGETS` set: that toml file, always.
2. `PUNTMORE_URL` + `SAVES_INGEST_KEY` set: the server, cached to `data/config.json`.
3. The cache, when the server is unreachable.
4. `data/targets.toml` (see `targets.example.toml`).

The page's leagues x markets become targets of two kinds:
- **matches**: a league's listing page, e.g. a Paddy competition, whose match
  links are opened up to `max_tabs` at a time. Each match tab clicks the
  market's tabs once, then stays open. A tab that goes quiet is reloaded once,
  then retired. Named `pp-<league>-<market>`.
- **page**: one URL kept open, e.g. the Bookie Bashing saves page (set on the
  page). It is reloaded every `every_min`, or only when it goes quiet for
  `stale_min`.

Timings on the page are clamped to the floors in `config._target`, and
`sites.py` budgets stay the ceiling whatever the page says.

## Tests
```sh
pip install -r scraper/requirements.txt -r scraper/requirements-dev.txt
python -m playwright install chromium   # or SCRAPER_CHROME_PATH=/usr/bin/google-chrome
python -m pytest scraper/tests -q      # from the repo root; includes a real browser against a fake localhost site
```
