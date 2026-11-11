"""One log per job: what the runner said about it, kept apart from the service's journal.

`<data>/logs/<name>.log` (the name made safe by sinks._safe, so no name can
reach outside logs/), one `{"ts": <epoch>, "msg": "..."}` per line, newest
last. Once a file passes MAX_BYTES it is rewritten (temp file + rename) to
its newest KEEP_LINES lines, so a log never grows without bound. The runner
writes it (Runner._say); `python -m scraper targets log` reads it; removing a
job removes its log (targets.remove).

A write never raises: a log that cannot be written must not break a tick. A
line that does not parse (a crash mid-write) is skipped by read, and the next
write starts on a fresh line, so a torn line never hides another.
"""
import json
import os
import pathlib
import stat
import tempfile
import time

from .sinks import _safe

MAX_BYTES = 512 * 1024
KEEP_LINES = 1000
MSG_MAX = 2000          # characters; a longer message (an exception's text) is cut and ends "…"


def path(data_dir: pathlib.Path, name: str) -> pathlib.Path:
    return pathlib.Path(data_dir) / "logs" / f"{_safe(name)}.log"


def write(data_dir, name: str, msg: str, now: float | None = None) -> None:
    """Append one line; past MAX_BYTES keep the newest KEEP_LINES. Never raises."""
    p = path(data_dir, name)
    if len(msg) > MSG_MAX:
        msg = msg[:MSG_MAX - 1] + "…"
    line = json.dumps({"ts": float(time.time() if now is None else now), "msg": msg}).encode() + b"\n"
    try:
        try:
            f = p.open("a+b")
        except FileNotFoundError:
            p.parent.mkdir(parents=True, exist_ok=True)
            f = p.open("a+b")
        with f:
            size = os.fstat(f.fileno()).st_size
            if size and os.pread(f.fileno(), 1, size - 1) != b"\n":
                line = b"\n" + line             # a torn last line: start afresh
            f.write(line)
        if size + len(line) > MAX_BYTES:
            _trim(p)
    except OSError:
        pass


def _trim(p: pathlib.Path) -> None:
    keep = p.read_bytes().splitlines(keepends=True)[-KEEP_LINES:]
    mode = stat.S_IMODE(p.stat().st_mode)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, mode)                     # mkstemp makes 0600; keep the log's own bits
        with os.fdopen(fd, "wb") as f:
            f.writelines(keep)
        os.replace(tmp, p)
    except OSError:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def read(data_dir, name: str, n: int = 50) -> list[dict]:
    """The newest n lines that parse, oldest first. A missing file gives []."""
    try:
        raw = path(data_dir, name).read_bytes()
    except OSError:
        return []
    out = []
    for line in reversed(raw.splitlines()):
        if len(out) >= n:
            break
        try:
            d = json.loads(line)
            out.append({"ts": float(d["ts"]), "msg": str(d["msg"])})
        except (ValueError, TypeError, KeyError):
            continue
    return out[::-1]


def remove(data_dir, name: str) -> None:
    try:
        path(data_dir, name).unlink(missing_ok=True)
    except OSError:
        pass
