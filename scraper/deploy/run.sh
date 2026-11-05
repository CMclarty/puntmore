#!/bin/sh
# (Re)create the container from the current image. Run once by hand, then
# update.sh calls it whenever a new image is published.
set -e
DIR=${SCRAPER_DIR:-/opt/puntmore-scraper}
IMAGE=${SCRAPER_IMAGE:-ghcr.io/cmclarty/puntmore-scraper:latest}
# The desktop Scraper (deploy/native/) shares the profile, budget and data dir.
if XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)} \
   systemctl --user is-active --quiet puntmore-scraper.service 2>/dev/null; then
  echo "puntmore-scraper (the desktop Scraper) is running; not starting the container" >&2
  exit 1
fi
docker rm -f puntmore-scraper >/dev/null 2>&1 || true
docker run -d --name puntmore-scraper \
  --restart unless-stopped \
  --init \
  --stop-timeout 30 \
  --shm-size 1g \
  --memory 3g \
  --env-file "$DIR/.env" \
  -v "$DIR/data:/data" \
  -p 127.0.0.1:5900:5900 \
  --log-opt max-size=10m --log-opt max-file=3 \
  "$IMAGE"
