#!/bin/sh
# Timer, every 10 min (puntmore-update.timer): fast-forward this checkout to origin/main,
# reinstall hub/ into the venv, restart the enabled user units. The work is `puntmore update`
# (puntmore_hub/update.py), which logs every run to hub/data/update.log and alerts on failure.
#   hub/deploy/update.sh             update now
#   hub/deploy/update.sh --dry-run   print what it would do
#   hub/deploy/update.sh --check     exit 0 up to date, 1 behind
set -u
HUB=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$HUB/.." && pwd)
VENV=${PUNTMORE_VENV:-$HOME/.venvs/puntmore}
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"   # uv, if installed
if [ ! -x "$VENV/bin/puntmore" ]; then
  mkdir -p "$HUB/data"
  echo "$(date -u '+%FT%T+00:00') failed no $VENV/bin/puntmore; install hub/ into the venv first" | tee -a "$HUB/data/update.log" >&2
  exit 1
fi
exec "$VENV/bin/puntmore" update --repo "$REPO" "$@"
