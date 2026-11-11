"""A job's rest: "nothing for this job to do until <time>".

A parser raises `Rest(until, why)` when its page has nothing worth reading
for a while (bb_saves with no game kicking off within 48 hours). The runner
then closes the job's page and loads nothing for it until `until` - like a
job outside its window - and keeps the rest in `data/resting.json`
(`{name: {"until": <epoch>, "why": "..."}}`), so a restart does not undo it.
`python -m scraper wake <target>` ends one by hand.

`why` is a plain sentence fragment with no time in it; the runner adds the
time ("<why> - resting until Thu 19 Nov 12:30"). The runner clamps `until`
into [now + MIN_REST_S, now + MAX_REST_S]. `load` gives {} for a missing or
unreadable file; `save` never raises (a rest that cannot be written still
holds until a restart).
"""
import json
import os
import pathlib
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
FILE = "resting.json"
MAX_REST_S = 7 * 86400
MIN_REST_S = 60


class Rest(Exception):
    def __init__(self, until: float, why: str):
        super().__init__(why)
        self.until = float(until)
        self.why = why


def when(ts: float) -> str:
    """An epoch time as London time, as the jobs say it: "Thu 19 Nov 12:30"."""
    d = datetime.fromtimestamp(ts, LONDON)
    return f"{d:%a} {d.day} {d:%b %H:%M}"


def _path(data_dir) -> pathlib.Path:
    return pathlib.Path(data_dir) / FILE


def load(data_dir) -> dict[str, dict]:
    try:
        raw = json.loads(_path(data_dir).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name, entry in raw.items():
        try:
            out[str(name)] = {"until": float(entry["until"]), "why": str(entry["why"])}
        except (TypeError, ValueError, KeyError):
            continue
    return out


def save(data_dir, resting: dict[str, dict]) -> None:
    """Atomic (temp file + rename). Never raises."""
    try:
        p = _path(data_dir)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(resting, fh, indent=1)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:
        pass
