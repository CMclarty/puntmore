#!/bin/sh
# ExecStartPre of puntmore-scraper.service (and install.sh): may the desktop
# Scraper start on this profile?
#  1. Not while the Docker Scraper runs: both would share the Chrome profile,
#     the budget in guard.sqlite3 and the data dir. Exit 1, the unit fails.
#  2. A lock left by another machine's Chrome (the stopped container: its
#     hostname is the container id) makes this Chrome refuse the profile as
#     "in use on another computer". With the container stopped (1) nothing
#     else holds it, so it is removed. A lock from THIS host is left alone:
#     Chrome itself takes over a dead one and refuses a live one.
set -u
PROFILE=${SCRAPER_PROFILE_DIR:-/opt/puntmore-scraper/data/profile}
CONTAINER=${SCRAPER_CONTAINER:-puntmore-scraper}

if command -v docker >/dev/null 2>&1; then
  out=$(docker ps --filter "name=^${CONTAINER}\$" --format '{{.Names}}' 2>&1)
  case "$out" in
    *"permission denied"*)
      # Cannot tell whether it runs: fail closed. (No daemon at all is fine:
      # then nothing runs.)
      echo "preflight: cannot ask Docker whether $CONTAINER runs ($out)" >&2
      exit 1 ;;
  esac
  if [ "$out" = "$CONTAINER" ]; then
    echo "preflight: the Docker Scraper ($CONTAINER) is running; it shares this profile," \
         "budget and data dir. Stop it first: docker stop $CONTAINER" >&2
    exit 1
  fi
fi

lock="$PROFILE/SingletonLock"
if [ -L "$lock" ]; then
  target=$(readlink "$lock")          # "<hostname>-<pid>"
  host=${target%-*}
  if [ "$host" != "$(hostname)" ]; then
    echo "preflight: removing the profile lock left by $target (not this host)"
    rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket" "$PROFILE/SingletonCookie"
  fi
fi
exit 0
