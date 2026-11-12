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
from . import joblog

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


# --- a job's alerts ----------------------------------------------------------------
def _empty() -> dict:
    return {"failing": {}, "no_rows": {}}


def load(data_dir) -> dict:
    """data/alerted.json: {"failing": {job: {"first", "since", "alerted", "text"}},
    "no_rows": {job: {"ran_s", "text"}}}. Missing or unreadable: empty; an
    entry that does not read is left out."""
    try:
        raw = json.loads((pathlib.Path(data_dir) / ALERTED).read_text())
    except (OSError, ValueError):
        return _empty()
    out = _empty()
    if not isinstance(raw, dict):
        return out
    for kind, read in (("failing", _failing_entry), ("no_rows", _no_rows_entry)):
        entries = raw.get(kind)
        if not isinstance(entries, dict):
            continue
        for name, e in entries.items():
            try:
                out[kind][str(name)] = read(e)
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
    return out


def _text(v) -> str | None:
    return None if v is None else str(v)


def _failing_entry(e) -> dict:
    return {"first": str(e["first"]), "since": float(e["since"]),
            "alerted": bool(e.get("alerted")), "text": _text(e.get("text"))}


def _no_rows_entry(e) -> dict:
    return {"ran_s": float(e["ran_s"]), "text": _text(e.get("text"))}


def save(data_dir, state: dict) -> bool:
    """Atomic; never raises (False, said on stdout, when not written)."""
    return _write(pathlib.Path(data_dir) / ALERTED, state)


SAVE_EVERY_S = 60         # the no-rows clocks reach the file at most this often
FIRST_MAX = 300           # characters of a failure's first line in its alert


class JobAlerts:
    """Each job's failing and no-rows alerts, for the runner. `failed`,
    `reading`, `delivered`, `running` and `idle` are what the runner saw;
    each alert is sent once, as "<job>: <text>", and is a line in the job's
    log. `standing(job)` is the text of what stands (status.json's "alert").

    The no-rows clock counts only the time between two ticks at which the
    job was running with pages open (`running` both times); a tick without
    (`idle`), or a new process, stops it without resetting it, so a rest,
    a quiet night or a restart pauses it and a delivery starts it again."""

    def __init__(self, data_dir, alerts):
        self.data_dir, self.alerts = pathlib.Path(data_dir), alerts
        state = load(data_dir)
        self.failing: dict[str, dict] = state["failing"]
        self.no_rows: dict[str, dict] = state["no_rows"]
        self.held: dict[str, float] = {}       # job -> the last tick it was running (this process)
        self.dirty = False
        self.next_save = 0.0

    # --- what the runner saw -------------------------------------------------------
    def failed(self, name: str, first: str, now: float) -> None:
        """A parse of the job failed; `first` is the first line of what it said."""
        f = self.failing.get(name)
        if f is None or f["first"] != first:
            # a different failure: the clock starts again; what was said stands
            f = self.failing[name] = {"first": first, "since": now, "alerted": False,
                                      "text": f["text"] if f else None}
            self._save()
        limit = behaviour.get("alerts.failing_min")
        if limit and not f["alerted"] and now - f["since"] >= limit * 60:
            f["alerted"] = True
            f["text"] = f"failing for {limit} min: {first[:FIRST_MAX]}"
            self._alert(name, f["text"])
            self._save()

    def reading(self, name: str) -> None:
        """A parse of the job worked, or the job began a rest."""
        f = self.failing.pop(name, None)
        if f is None:
            return
        if f["text"]:
            self._alert(name, "reading again")
        self._save()

    def delivered(self, name: str, now: float) -> None:
        """Rows of the job were handed over: its no-rows clock starts again."""
        n = self.no_rows.pop(name, None)
        if name in self.held:
            self.held[name] = now
        if n is None:
            return
        if n["text"]:
            self._alert(name, "delivering again")
        self._save()

    def running(self, name: str, now: float, detail) -> None:
        """This tick the job is running with pages open that its parser reads.
        `detail()` is its status detail, asked for only when the alert goes."""
        n = self.no_rows.setdefault(name, {"ran_s": 0.0, "text": None})
        last = self.held.get(name)
        if last is not None and now > last:
            n["ran_s"] += now - last
            self.dirty = True
        self.held[name] = now
        limit = behaviour.get("alerts.no_rows_min")
        if (limit and n["text"] is None and n["ran_s"] >= limit * 60
                and not self.failing.get(name, {}).get("text")):
            try:
                said = detail()
            except Exception as exc:
                said = f"{exc.__class__.__name__}: {exc}"
            n["text"] = f"no rows for {limit} min ({said})"
            self._alert(name, n["text"])
            self._save()

    def idle(self, name: str) -> None:
        """This tick the job is not running with pages open: its clock stops."""
        self.held.pop(name, None)

    # --- what stands -----------------------------------------------------------------
    def standing(self, name: str) -> str | None:
        said = [e["text"] for e in (self.failing.get(name), self.no_rows.get(name)) if e and e["text"]]
        return "; ".join(said) or None

    def keep_only(self, names) -> None:
        """Forget the jobs that are not these (removed from targets.toml)."""
        names = set(names)
        gone = [(kind, n) for kind in ("failing", "no_rows") for n in getattr(self, kind) if n not in names]
        for kind, n in gone:
            getattr(self, kind).pop(n, None)
        for n in [n for n in self.held if n not in names]:
            del self.held[n]
        if gone:
            self._save()

    def flush(self, now: float) -> None:
        """Save the no-rows clocks, at most every SAVE_EVERY_S."""
        if self.dirty and now >= self.next_save:
            self._save()
            self.next_save = now + SAVE_EVERY_S

    # --- out ----------------------------------------------------------------------------
    def _alert(self, name: str, text: str) -> None:
        send(self.alerts, f"{name}: {text}")
        joblog.write(self.data_dir, name, text)

    def _save(self) -> None:
        self.dirty = False
        save(self.data_dir, {"failing": self.failing, "no_rows": self.no_rows})
