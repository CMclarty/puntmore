"""Error capture that can be read back OUT of a running deploy.

Puntmore runs as one Render container with no shell access to speak of, so when
something breaks the only evidence has been whatever the browser happened to
show. This keeps the last N failures — server exceptions, slow requests, and
errors reported by the frontend — in a ring buffer that an admin (or a machine
with the state key) can fetch as JSON.

Two rules it follows, both learned the hard way:

1. EVERY record also goes to stdout. The buffer lives in the process, so a crash
   or a redeploy wipes it — and a crash is exactly when you want the record.
   Render keeps stdout, so the log is the durable copy and this is the
   convenient one.

2. It never stores request bodies, headers, cookies or query strings. Those
   carry session cookies, API keys and OCR payloads. Path, method, status and
   the exception are enough to locate a bug, and anything more is a credential
   leak waiting for the first person who pastes a diagnostics dump into a chat.

NOTE ON 502s: a 502 is Render's proxy saying the container never answered —
the process died, ran out of memory, or exceeded the request timeout. By
definition the app cannot log its own 502, because there was no response. What
this CAN show is the shape around one: a 500 just before it, a request that ran
long enough to be cut off, or a `boot` record proving the process restarted.
"""
import logging
import os
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timezone
from threading import Lock

logger = logging.getLogger("uvicorn.error")

# Bounded so a crash-loop can't exhaust memory. ~200 records is a few hours of
# real failures, and far more than one debugging session needs.
MAX_RECORDS = 200
# Longest single field kept. Tracebacks are the reason this exists; anything
# past this is almost always a repeated frame.
MAX_DETAIL = 4000

_records: deque = deque(maxlen=MAX_RECORDS)
_lock = Lock()

# When this process started, so a reader can tell "still the same container"
# from "it restarted under me" — the tell-tale of a crash behind a 502.
BOOT_ID = uuid.uuid4().hex[:8]
BOOT_TIME = time.time()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _clip(text: str | None, limit: int = MAX_DETAIL) -> str | None:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def record(kind: str, **fields) -> str:
    """Store one diagnostic record and mirror it to stdout. Returns its id.

    The id is handed back to the caller (in the error response, or to the
    frontend) so a user can quote one string and land on the exact record
    rather than describing what they saw.
    """
    ref = uuid.uuid4().hex[:8]
    entry = {
        "id": ref,
        "at": _now(),
        "kind": kind,
        "boot": BOOT_ID,
        **{k: _clip(v) if isinstance(v, str) else v for k, v in fields.items()},
    }
    with _lock:
        _records.append(entry)

    # stdout copy — the one that survives the container. Keep it to a single
    # line per record so Render's log viewer stays greppable by ref.
    summary = " ".join(
        f"{k}={v!r}" for k, v in entry.items()
        if k not in ("traceback", "at", "boot") and v is not None
    )
    log = logger.error if kind in ("server_error", "client_error") else logger.warning
    log("[diag] %s", summary)
    if entry.get("traceback"):
        logger.error("[diag] %s traceback:\n%s", ref, entry["traceback"])
    return ref


def record_exception(exc: BaseException, *, method: str, path: str, user: str | None) -> str:
    return record(
        "server_error",
        method=method,
        path=path,
        user=user,
        error=type(exc).__name__,
        message=str(exc),
        traceback="".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
    )


def snapshot() -> dict:
    """Everything a reader needs: the records plus the context to read them in."""
    with _lock:
        items = list(_records)
    return {
        "boot_id": BOOT_ID,
        "booted_at": datetime.fromtimestamp(BOOT_TIME, timezone.utc).isoformat(
            timespec="seconds"
        ),
        "uptime_seconds": round(time.time() - BOOT_TIME, 1),
        "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:8] or None,
        "count": len(items),
        "capacity": MAX_RECORDS,
        # Newest first — when something just broke, it's the first thing read.
        "records": list(reversed(items)),
    }


def clear() -> int:
    with _lock:
        n = len(_records)
        _records.clear()
    return n
