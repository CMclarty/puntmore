# puntmore scraper v0.1

One real Chrome on a Linux server, keeping a few Paddy Power and Bookie Bashing
pages **open** and listening to what they receive. After the first load the
site's own scripts keep each page fresh, so most targets cost no further
requests. Runs unattended in Docker and updates itself from GHCR.

**v0.1 captures; it does not price yet.** The parsers (`parsers.py`) need
real payloads, and collecting them is v0.1's first job. They're written from
`data/samples/` and `data/dumps/` once the server has run.

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
- It never answers a challenge, never types a password, and never fakes a
  fingerprint.
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
#   targets.example.toml             -> /opt/puntmore-scraper/data/targets.toml (edit)
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
docker exec puntmore-scraper python -m scraper status          # budget, cool-offs, heartbeat
docker exec puntmore-scraper python -m scraper dump bb-saves    # save the open page(s) to data/dumps/
docker exec puntmore-scraper python -m scraper resume paddypower
docker exec puntmore-scraper python -m scraper check            # validate targets.toml
docker restart puntmore-scraper                                 # after editing targets.toml
```

Health: Docker's healthcheck fails if the loop hasn't ticked for 5 minutes.
Set `SCRAPER_HEALTHCHECK_URL` (e.g. healthchecks.io) for an alert when the
whole server goes quiet.

## Targets
See `targets.example.toml`. There are two kinds:
- **page**: one URL kept open, e.g. the Bookie Bashing saves page. It is
  reloaded every `every_min`, or only when it goes quiet for `stale_min`.
- **matches**: a listing page, e.g. a Paddy competition, whose match links are
  opened up to `max_tabs` at a time. Each match tab clicks `market_tabs` once,
  then stays open. A tab that goes quiet is reloaded once, then retired.

## Tests
```sh
pip install -r scraper/requirements.txt -r scraper/requirements-dev.txt
python -m playwright install chromium   # or SCRAPER_CHROME_PATH=/usr/bin/google-chrome
python -m pytest scraper/tests -q      # from the repo root      # includes a real browser against a fake localhost site
```
