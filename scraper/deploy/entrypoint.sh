#!/bin/sh
# Root only long enough to fix the volume's owner, then everything runs as pwuser.
# setpriv EXECs (no wrapper process), so python gets docker's SIGTERM itself
# and closes Chrome cleanly; run with --init so Chrome's children are reaped.
set -e
AS_PW="setpriv --reuid=pwuser --regid=pwuser --init-groups env HOME=/home/pwuser"
mkdir -p /data/profile
chown -R pwuser:pwuser /data
# A container that was killed leaves Chrome's profile lock behind, and Chrome
# then refuses the profile - which would stop an unattended restart dead.
rm -f /data/profile/SingletonLock /data/profile/SingletonSocket /data/profile/SingletonCookie

# A 1920x1080 screen with Chrome's 1600x1000 window on it (browser.py), like an
# ordinary desktop. `selfcheck` in a fresh container (no service running)
# needs the display too, but not VNC.
if [ "$1" = "run" ] || [ "$1" = "selfcheck" ]; then
  if ! [ -e /tmp/.X11-unix/X99 ]; then
    rm -f /tmp/.X99-lock
    mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
    $AS_PW Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
    sleep 1
  fi
fi
if [ "$1" = "run" ]; then
  if [ -n "$VNC_PASSWORD" ]; then
    $AS_PW x11vnc -display :99 -forever -shared -quiet \
      -passwd "$VNC_PASSWORD" -rfbport 5900 >/dev/null 2>&1 &
  else
    echo "VNC_PASSWORD not set - no VNC (you will not be able to log in to Bookie Bashing)"
  fi
fi
exec $AS_PW python -m scraper "$@"
