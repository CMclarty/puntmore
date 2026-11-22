"""The fit caches, kept across a restart.

A fit is ~1.3 s a match and lived only in memory (cards/laddered.py: _FITS,
_PRICES, _SPLITS), so every deploy threw the lot away and both boards were
refitted in the first minutes after it: Hills' feed took 29 s and Paddy's 61 s
on the first load after a restart (22 Nov 2026). The caches are written to the
disk after a pricing pass that changed them, and read back at boot.

- PATH is None until main.py's lifespan sets it from FIT_CACHE_PATH
  (render.yaml: on the disk), so tests and a dev box never write a file.
- An entry keeps its age: the time it had been held, plus the time the file
  lay on the disk, so nothing outlives the cache's own TTL by restarting.
- VERSION is a hash of the pricing code. A file written by other code is
  dropped unread into the caches: a fit from an older model is not a fit.
- The file is this app's own, read with pickle; anything unreadable is logged
  and ignored, and the boards refit as they did before.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import sys
import threading
import time
from pathlib import Path

from . import laddered

logger = logging.getLogger("uvicorn.error")

PATH: str | None = None
_REFUSED_MARK = "__refused__"
_SAVE_LOCK = threading.Lock()        # one writer at a time
_saved_writes: int | None = None     # the caches' write count at the last save


def _caches() -> dict:
    return {"fits": laddered._FITS, "prices": laddered._PRICES, "splits": laddered._SPLITS}


def _version() -> str:
    """A hash of the code a fit depends on, and the Python that pickled it."""
    here = Path(__file__).resolve().parent
    files = sorted((here.parent / "pricing" / "counts").glob("*.py")) + [here / "laddered.py"]
    h = hashlib.sha256(sys.version.encode())
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def _writes() -> int:
    return sum(c.writes for c in _caches().values())


def _snapshot(cache) -> list:
    """[(key, age in seconds, value)] for one cache. Another thread may be
    writing to it (the caches are not locked), so a changed-size error is
    retried."""
    for _ in range(5):
        try:
            return cache.dump()
        except RuntimeError:
            continue
    return []


def save() -> bool:
    """Write the caches to PATH if they changed since the last save. True when
    a file was written. Never raises."""
    global _saved_writes
    if PATH is None:
        return False
    with _SAVE_LOCK:
        writes = _writes()
        if writes == _saved_writes:
            return False
        try:
            body = {"version": _version(), "saved_at": time.time(), "caches": {
                name: [(k, age, _REFUSED_MARK if v is laddered._REFUSED else v)
                       for k, age, v in _snapshot(cache)]
                for name, cache in _caches().items()}}
            tmp = f"{PATH}.tmp"
            with open(tmp, "wb") as fh:
                pickle.dump(body, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, PATH)
        except Exception as exc:
            logger.warning("fit cache not saved: %s", exc)
            return False
        _saved_writes = writes
        return True


def load() -> int:
    """Fill the caches from PATH. Returns the entries loaded; 0 when there is
    no file, it is another version's, or it cannot be read. Never raises."""
    global _saved_writes
    if PATH is None or not os.path.exists(PATH):
        return 0
    try:
        with open(PATH, "rb") as fh:
            body = pickle.load(fh)
        if body.get("version") != _version():
            logger.info("fit cache: written by other pricing code, not loaded")
            return 0
        lay = max(0.0, time.time() - float(body["saved_at"]))
        loaded = 0
        for name, cache in _caches().items():
            loaded += cache.load(
                [(k, age + lay, laddered._REFUSED if isinstance(v, str) and v == _REFUSED_MARK
                  else v) for k, age, v in body["caches"].get(name, [])])
    except Exception as exc:
        logger.warning("fit cache not loaded: %s", exc)
        return 0
    _saved_writes = _writes()
    logger.info("fit cache: %d entr(ies) loaded, %.0f s old", loaded, lay)
    return loaded
