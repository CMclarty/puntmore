#!/bin/sh
# Install (or reinstall) the Scraper as the user service puntmore-scraper on the
# Hub's desktop. Idempotent: run it again after a change to the unit file.
#
#   scraper/deploy/native/install.sh              the checkout this script is in
#   scraper/deploy/native/install.sh --repo PATH  another checkout
#
# Run it as the desktop user (not root). It asks sudo once, to give that user
# /opt/puntmore-scraper (the Docker Scraper's files belong to the container's
# uid). It refuses to start the service while the Docker Scraper runs.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
if [ "${1:-}" = "--repo" ]; then REPO=$(cd "$2" && pwd); fi
DIR=${SCRAPER_DIR:-/opt/puntmore-scraper}
VENV=${PUNTMORE_VENV:-$HOME/.venvs/puntmore}
UNIT=puntmore-scraper.service
DEST=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

[ "$(id -u)" -ne 0 ] || { echo "run as the desktop user, not root" >&2; exit 1; }
[ -f "$REPO/scraper/__main__.py" ] || { echo "no scraper/ in $REPO" >&2; exit 1; }
[ -x "$VENV/bin/python" ] || { echo "no venv at $VENV" >&2; exit 1; }
"$VENV/bin/python" -c "import playwright" 2>/dev/null \
  || { echo "playwright is not in $VENV: uv pip install -r $REPO/scraper/requirements.txt --python $VENV/bin/python" >&2; exit 1; }
[ -x /usr/bin/google-chrome ] || { echo "no /usr/bin/google-chrome" >&2; exit 1; }
[ -f "$DIR/.env" ] || { echo "no $DIR/.env (see scraper/.env.example)" >&2; exit 1; }
[ -f "$DIR/data/targets.toml" ] || { echo "no $DIR/data/targets.toml (see scraper/targets.example.toml)" >&2; exit 1; }

# The Docker Scraper would share the profile, the budget and the data dir.
SCRAPER_PROFILE_DIR="$DIR/data/profile" sh "$REPO/scraper/deploy/native/preflight.sh" >/dev/null || {
  echo "Not installing while the Docker Scraper runs. Stop it (docker stop puntmore-scraper), then run this again." >&2
  exit 1; }

if [ -n "$(find "$DIR" ! -user "$(id -un)" -print -quit)" ]; then
  echo "giving $(id -un) $DIR (sudo)"
  sudo chown -R "$(id -un):$(id -gn)" "$DIR"
fi

mkdir -p "$DEST"
# The unit names the box's checkout, ~/Projects/puntmore; point it at $REPO.
sed "s|%h/Projects/puntmore|$REPO|g" "$HERE/$UNIT" > "$DEST/$UNIT.new"
mv "$DEST/$UNIT.new" "$DEST/$UNIT"
systemctl --user daemon-reload
systemctl --user enable "$UNIT"
if systemctl --user is-active --quiet "$UNIT"; then
  systemctl --user restart "$UNIT"
else
  systemctl --user start "$UNIT"
fi
echo "installed $DEST/$UNIT (code: $REPO)"
systemctl --user --no-pager status "$UNIT" | head -5 || true

cat <<EOF

Next:
  journalctl --user -u puntmore-scraper -f            watch the first heartbeat and loads
  cd $REPO && SCRAPER_DATA_DIR=$DIR/data $VENV/bin/python -m scraper selfcheck
                                                      what a site sees (asks the running service)
  The Docker Scraper is the fallback now. Leave it stopped, and take its cron
  updater out so it cannot recreate the container:
    crontab -l | grep -v puntmore-scraper/update.sh | crontab -
  Updates: puntmore-update.timer restarts puntmore-scraper when a pull to main
  changes scraper/. A changed unit file needs this script run again.
EOF
