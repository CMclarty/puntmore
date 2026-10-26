#!/bin/sh
# Cron, every 15 min: pull the image; if it changed, restart onto it.
#   */15 * * * * /opt/puntmore-scraper/update.sh >> /opt/puntmore-scraper/update.log 2>&1
set -e
DIR=${SCRAPER_DIR:-/opt/puntmore-scraper}
IMAGE=${SCRAPER_IMAGE:-ghcr.io/cmclarty/puntmore-scraper:latest}
before=$(docker image inspect -f '{{.Id}}' "$IMAGE" 2>/dev/null || echo none)
docker pull -q "$IMAGE" >/dev/null
after=$(docker image inspect -f '{{.Id}}' "$IMAGE")
running=$(docker inspect -f '{{.Image}}' puntmore-scraper 2>/dev/null || echo none)
if [ "$after" != "$running" ]; then
  echo "$(date '+%F %T') updating: ${before#sha256:} -> ${after#sha256:}"
  "$DIR/run.sh"
  docker image prune -f >/dev/null
fi
