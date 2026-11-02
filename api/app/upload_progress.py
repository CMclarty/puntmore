"""Live progress of a coupon read, for the upload's waiting screen.

A read runs after the upload has returned (routers/upload.py::_process_upload)
and takes about a minute. The coupon row only says "processing" until it ends,
so a read that hung, died with the server, or failed for a reason nobody saw
looked exactly like one still working. This keeps, per coupon, which step it
is on, when each started and ended, and why it failed; the waiting screen
polls it (GET /upload/{id}/status).

In memory, on purpose: it is about reads running NOW, in this process (the
service is one uvicorn process). A coupon still "processing" with no entry
here is therefore one nothing is reading — the server restarted mid-read —
and the status endpoint says so instead of leaving it spinning.
"""
import threading
import time
from contextvars import ContextVar

# The steps, in order, as the waiting screen lists them.
STEPS = (
    ("saved", "Photo saved"),
    ("read", "Reading the sheet"),
    ("verify", "Cross-checking the prices"),
    ("check", "Checking it is the right sheet"),
    ("build", "Building the selections"),
    ("price", "Pricing against the odds"),
)
_KEYS = [k for k, _ in STEPS]
# Finished reads are kept this long so the screen can show how one ended.
KEEP_SECONDS = 3600

_lock = threading.Lock()
_live: dict[int, dict] = {}
# Which coupon the current thread is reading, so the OCR wrapper can report
# its strip reads without every OCR provider's signature carrying it.
_current: ContextVar[int | None] = ContextVar("upload_progress_coupon", default=None)


def _sweep(now: float) -> None:
    for cid in [c for c, e in _live.items() if e["ended"] and now - e["ended"] > KEEP_SECONDS]:
        del _live[cid]


def start(coupon_id: int) -> None:
    """The upload is saved: step one done, the read queued."""
    now = time.time()
    with _lock:
        _sweep(now)
        _live[coupon_id] = {
            "steps": {"saved": {"started": now, "ended": now, "note": None}},
            "step": "saved", "error": None, "ended": None, "started": now,
        }


def step(coupon_id: int | None, key: str, note: str | None = None) -> None:
    """Move on to `key`, closing whatever step was running."""
    if coupon_id is None:
        return
    now = time.time()
    with _lock:
        e = _live.get(coupon_id)
        if e is None:
            return
        running = e["steps"].get(e["step"])
        if running and running["ended"] is None:
            running["ended"] = now
        e["steps"][key] = {"started": now, "ended": None, "note": note}
        e["step"] = key


def note(coupon_id: int | None, text: str) -> None:
    """A detail on the running step ("3 of 6 strips read")."""
    if coupon_id is None:
        return
    with _lock:
        e = _live.get(coupon_id)
        if e is not None and e["step"] in e["steps"]:
            e["steps"][e["step"]]["note"] = text


def finish(coupon_id: int, error: str | None = None) -> None:
    """The read ended: done, or failed with `error` on the running step."""
    now = time.time()
    with _lock:
        e = _live.get(coupon_id)
        if e is None:
            return
        running = e["steps"].get(e["step"])
        if running and running["ended"] is None:
            running["ended"] = now
        e["error"] = error
        e["ended"] = now


def snapshot(coupon_id: int) -> dict | None:
    """The steps as the screen draws them, or None when nothing here knows
    this coupon (a finished read long gone, or a server restart)."""
    now = time.time()
    with _lock:
        e = _live.get(coupon_id)
        if e is None:
            return None
        steps = []
        for key, label in STEPS:
            s = e["steps"].get(key)
            if s is None:
                # A step a finished read never needed (only priced grids are
                # cross-checked) was skipped, not left to do.
                done_ok = e["ended"] is not None and e["error"] is None
                state, secs, detail = ("skipped" if done_ok else "todo"), None, None
            else:
                failed = e["error"] is not None and key == e["step"]
                state = "failed" if failed else "done" if s["ended"] is not None else "running"
                secs = round((s["ended"] or now) - s["started"], 1)
                detail = s["note"]
            steps.append({"key": key, "label": label, "state": state,
                          "seconds": secs, "note": detail})
        return {
            "steps": steps,
            "step": e["step"],
            "error": e["error"],
            "elapsed": round((e["ended"] or now) - e["started"], 1),
            "finished": e["ended"] is not None,
        }


def reading(coupon_id: int):
    """Context for a read: the OCR wrapper's reports go to this coupon."""
    return _Reading(coupon_id)


class _Reading:
    def __init__(self, coupon_id):
        self.coupon_id = coupon_id

    def __enter__(self):
        self._token = _current.set(self.coupon_id)

    def __exit__(self, *exc):
        _current.reset(self._token)


def current() -> int | None:
    """The coupon this thread is reading, if any."""
    return _current.get()
