"""When the Scraper is not working, it says so: one alert when a problem has
stood for its threshold, one when it clears (Callum, 12 Nov 2026).

  stalled   the loop has made no progress for `alerts.stuck_s` (the watchdog
            thread, __main__.Watchdog, the only thing still running then);
            "running again" if it ticks before the watchdog restarts it at
            600 s. Before that exit the watchdog writes data/stalled.json, and
            the next start says "restarted by the watchdog" (`restarted`).
  failing   a job's parser has failed the same way (the same first line) for
            `alerts.failing_min`; cleared by a parse that works or a rest.
  no rows   a running job with pages open that its parser reads has handed
            over no rows for `alerts.no_rows_min`; cleared by a delivery. Not
            sent while the job's failing alert stands.

Each goes through alerts.Alerts (data/alerts.log, the Console, the webhook),
and a job's also as a line in its own log. The thresholds are behaviour.py's,
read each time they are used; 0 turns one off. A job's alert state is kept in
data/alerted.json, so the service's own restart (every 1.5-2 h) neither says
an alert again nor loses a clock. Nothing here raises: a file that cannot be
written, or an alert that cannot be sent, is printed and dropped.
"""
import json
import os
import pathlib
import tempfile
import threading
import time

from . import behaviour

STALLED = "stalled.json"
ALERTED = "alerted.json"


def span(seconds: float) -> str:
    """A stretch of time as an alert says it: "4 min", or "45 s" under a minute."""
    s = max(0, int(seconds))
    return f"{s // 60} min" if s >= 60 else f"{s} s"


def send(alerts, message: str) -> bool:
    """alerts.send that never raises; False when it failed (said on stdout)."""
    try:
        alerts.send(message)
        return True
    except Exception as exc:
        try:
            print(f"alert not sent ({exc.__class__.__name__}: {exc}): {message}", flush=True)
        except Exception:
            pass
        return False


def send_aside(alerts, message: str) -> threading.Thread | None:
    """send() on a thread of its own, so a webhook that hangs holds up
    nothing (the watchdog's exit above all). Never raises."""
    try:
        th = threading.Thread(target=send, args=(alerts, message), name="alert", daemon=True)
        th.start()
        return th
    except Exception as exc:
        print(f"alert not sent ({exc.__class__.__name__}: {exc}): {message}", flush=True)
        return None


def _write(path: pathlib.Path, body) -> bool:
    """Atomic JSON write (temp file + rename). Never raises; False if not written."""
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(body, fh, indent=1)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except Exception as exc:
        try:
            print(f"{path.name} not written: {exc.__class__.__name__}: {exc}", flush=True)
        except Exception:
            pass
        return False


# --- stalled ----------------------------------------------------------------------
def write_stalled(data_dir, stuck_s: float, now: float | None = None) -> bool:
    """What the watchdog leaves for the next start: {"at": epoch, "stuck_s": n}."""
    if data_dir is None:
        return False
    return _write(pathlib.Path(data_dir) / STALLED,
                  {"at": time.time() if now is None else now, "stuck_s": int(stuck_s)})


def restarted(data_dir, alerts) -> str | None:
    """At start: a stalled.json left by the watchdog is said once (unless
    alerts.stuck_s is 0) and removed. Returns what was sent. Never raises."""
    p = pathlib.Path(data_dir) / STALLED
    try:
        if not p.exists():
            return None
        try:
            stuck = json.loads(p.read_text())["stuck_s"]
            msg = f"restarted by the watchdog after a {span(float(stuck))} stall"
        except (OSError, ValueError, KeyError, TypeError):
            msg = "restarted by the watchdog after a stall"
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            print(f"{STALLED} not removed: {exc}", flush=True)
        if not behaviour.get("alerts.stuck_s"):
            return None
        send(alerts, msg)
        return msg
    except Exception as exc:
        print(f"{STALLED}: {exc.__class__.__name__}: {exc}", flush=True)
        return None
