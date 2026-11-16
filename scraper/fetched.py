"""A fetch job's last good read: when its load last ended in a parse that
read rows, and from which url. `data/fetched.json`,
`{name: {"at": <epoch>, "url": "..."}}`.

The runner draws a fetch job's next run from it when it starts, so a restart
is not a load: the next run is the last good read plus the job's interval
(and never sooner than fetch.retry_s after its last load of any kind, from
the guard's record). A load that failed, read nothing or only sent a sign-in
writes nothing here, so after a restart it is tried again on fetch.retry_s,
not a whole interval later. A removed or changed job's entry is dropped.
`load` gives {} for a missing or unreadable file; `save` never raises.
"""
import json
import os
import pathlib
import tempfile

FILE = "fetched.json"


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
            out[str(name)] = {"at": float(entry["at"]), "url": str(entry["url"])}
        except (TypeError, ValueError, KeyError):
            continue
    return out


def save(data_dir, fetched: dict[str, dict]) -> None:
    """Atomic (temp file + rename). Never raises."""
    try:
        p = _path(data_dir)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(fetched, fh, indent=1)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:
        pass
