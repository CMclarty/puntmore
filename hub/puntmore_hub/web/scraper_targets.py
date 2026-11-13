"""The console's Scraper tab: what targets.toml says, the five changes a person may make to it, and
each job's log.

Every read and write goes through the Scraper's own library, ``scraper/targets.py``: it holds the
lock, the validation (``config._target``'s sites, hosts and clamps) and the atomic save. Nothing here
parses or writes the TOML, and nothing here repeats one of its rules: a refusal is the library's
message, passed back to the page with the field it names.

``scraper`` is not an installed package. The hub and the Scraper share the venv and the repo, so the
library is imported from ``Settings.scraper_src`` (``PUNTMORE_SCRAPER_SRC``; default the repo this
package was checked out in): if ``scraper`` is already importable (``PYTHONPATH=.`` from the repo
root) that one is used, otherwise the package is loaded from that directory by file location. No
``sys.path`` edit either way.

A job's log is the Scraper's too (``scraper/joblog.py``, loaded the same way): each tile carries its
newest LOG_TAIL lines, and ``GET /api/scraper/log/{target}`` up to LOG_MAX. The log's path is always
``joblog``'s (it makes the name safe, so no name reaches outside ``<data>/logs/``); nothing here
builds one. A log that is missing or does not read is an empty list, never an error on the tab.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from ..config import Settings

# How long a console write waits for the library's lock before answering 409. The library's own
# default is 10 s; a person at a page should hear "busy, try again" sooner than that.
LOCK_WAIT_S = 3.0
SITES = ("paddypower", "bookiebashing", "teamcasino", "generic")
# What the edit form may change (the rest - name, site, mode, parser, post - is remove + add).
EDITABLE = ("every_min", "stale_min", "near_min", "near_hours", "priority", "window", "min_rows", "trust_weight",
            "wait_for", "url", "days", "until", "listings", "within_h")
APPLY_NOTE = "the Scraper applies changes within 60 s"
BUSY = "being changed by something else"
# A tile shows a job's newest LOG_TAIL log lines; the log route gives at most LOG_MAX (joblog keeps 1000).
LOG_TAIL = 5
LOG_MAX = 1000

_load_lock = threading.Lock()


class LibraryMissing(RuntimeError):
    pass


def library(cfg: Settings) -> ModuleType:
    """``scraper.targets``, imported once per process."""
    with _load_lock:
        if "scraper.targets" in sys.modules:
            return sys.modules["scraper.targets"]
        if "scraper" not in sys.modules and importlib.util.find_spec("scraper") is None:
            init = Path(cfg.scraper_src) / "scraper" / "__init__.py"
            if not init.is_file():
                raise LibraryMissing(f"the Scraper's code is not at {init.parent} (set PUNTMORE_SCRAPER_SRC)")
            spec = importlib.util.spec_from_file_location("scraper", init,
                                                          submodule_search_locations=[str(init.parent)])
            mod = importlib.util.module_from_spec(spec)
            sys.modules["scraper"] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop("scraper", None)
                raise
        return importlib.import_module("scraper.targets")


def joblog(cfg: Settings) -> ModuleType:
    """``scraper.joblog``, from the same package ``library`` loads."""
    library(cfg)
    return importlib.import_module("scraper.joblog")


# ---- reading -----------------------------------------------------------------------------------

def _lines(jl, cfg: Settings, name: str, n: int, ts_iso) -> list[dict[str, Any]]:
    return [{"at": ts_iso(x["ts"]), "msg": x["msg"]} for x in jl.read(cfg.scraper_data_dir, name, n)]


def log(cfg: Settings, name: str, n: int) -> dict[str, Any]:
    """A job's newest n log lines (n clamped to 1..LOG_MAX), oldest first, times in ISO. A job with
    no log (or no such job) has no lines. LibraryMissing when the Scraper's code does not load."""
    from .collect import _ts_iso
    n = max(1, min(LOG_MAX, int(n)))
    try:
        jl = joblog(cfg)
    except LibraryMissing:
        raise
    except Exception as exc:  # noqa: BLE001 - an import error in the Scraper's code
        raise LibraryMissing(f"the Scraper's library does not load: {exc.__class__.__name__}: {exc}") from None
    return {"target": name, "n": n, "lines": _lines(jl, cfg, name, n, _ts_iso)}


def view(cfg: Settings, latest_count, ts_iso) -> dict[str, Any]:
    """targets.summary() for the tab, each row with ISO times, the rows its latest parse found, an
    approximate next load, its schedule in words and the newest LOG_TAIL lines of its log. Never
    raises: a missing library or a file that does not load comes back as ``error`` with an empty
    list (the page still shows the add form)."""
    path = cfg.scraper_targets_file
    out: dict[str, Any] = {"file": str(path), "exists": path.is_file(), "targets": [], "error": None,
                           "sites": list(SITES), "editable": list(EDITABLE), "apply_note": APPLY_NOTE}
    try:
        lib = library(cfg)
        _follow_behaviour(cfg)
        rows = lib.summary(path, cfg.scraper_data_dir / "status.json")
    except Exception as exc:  # noqa: BLE001 - TargetsError, LibraryMissing, an import error: all shown
        out["error"] = f"{exc.__class__.__name__}: {exc}" if not _is_targets_error(exc) else str(exc)
        return out
    try:
        jl = joblog(cfg)
    except Exception:  # noqa: BLE001 - no log is shown as no lines, never as a broken tab
        jl = None
    for r in rows:
        st = r.get("status") or {}
        last_load = st.get("last_load")
        next_due = None
        every = r.get("near_min") if st.get("near") and r.get("near_min") else r.get("every_min")
        if r.get("enabled") and every and last_load:
            try:
                next_due = ts_iso(float(last_load) + float(every) * 60)
            except (TypeError, ValueError):
                pass
        rows_parsed = None
        try:
            rows_parsed = latest_count(str(r.get("name") or ""))
        except (OSError, ValueError):
            pass
        tail: list[dict[str, Any]] = []
        if jl is not None:
            try:
                tail = _lines(jl, cfg, str(r.get("name") or ""), LOG_TAIL, ts_iso)
            except Exception:  # noqa: BLE001 - see above
                pass
        r["view"] = {"state": st.get("state"), "detail": st.get("detail"), "last_ok": ts_iso(st.get("last_parse")),
                     "last_load": ts_iso(last_load), "next_due": next_due, "rows_last_parsed": rows_parsed,
                     "next_kickoff": ts_iso(st.get("next_kickoff")), "near": bool(st.get("near")),
                     "rows_posted": st.get("rows_posted"), "reported_at": ts_iso(st.get("reported_at")),
                     "schedule": r.get("schedule"), "log": tail, "alert": st.get("alert")}
    out["targets"] = rows
    return out


def _follow_behaviour(cfg: Settings) -> None:
    """The schedule in words reads the Scraper's behaviour (a fetch job's default interval): load
    ``behaviour.toml`` into this process first, so the words follow a change. Never raises."""
    from . import scraper_behaviour
    try:
        scraper_behaviour.follow(cfg)
    except Exception:  # noqa: BLE001 - the words then show the defaults; the pane says what is wrong
        pass


def _is_targets_error(exc: BaseException) -> bool:
    return type(exc).__name__ == "TargetsError"


# ---- writing -----------------------------------------------------------------------------------

def field_of(message: str) -> str | None:
    """The form field a library refusal is about, so the page can put the message beside it."""
    rest = re.sub(r"^target .*?: ", "", message, count=1)
    if rest.startswith("listing"):            # "listings ..." and "listing <url> is not on an allowed host"
        return "listings"
    if "allowed host" in rest:
        return "url"
    if rest.startswith("unknown site"):
        return "site"
    if "already exists" in rest or rest.startswith("no target named"):
        return "name"
    m = re.match(r"(matches)\.|([a-z_]+)\b", rest)
    if m:
        name = m.group(1) or m.group(2)
        if name in ("name", "site", "url", "mode", "parser", "post", "matches", "enabled", *EDITABLE):
            return name
    return None


def _refused(exc: BaseException):
    """A library refusal -> the console's ActionError, with the status that fits."""
    from .actions import ActionError
    msg = str(exc)
    if BUSY in msg:
        return ActionError(409, msg + " (HubAI or the CLI is mid-change)", retry=True)
    if msg.startswith("no target named"):
        return ActionError(404, msg, field="name")
    if "already exists" in msg:
        return ActionError(409, msg, field="name")
    return ActionError(400, msg, field=field_of(msg))


def _call(cfg: Settings, op: str, *args, **kw):
    from .actions import ActionError
    try:
        lib = library(cfg)
    except Exception as exc:  # noqa: BLE001
        raise ActionError(503, f"the Scraper's targets library is not available: {exc}") from None
    try:
        return lib, getattr(lib, op)(cfg.scraper_targets_file, *args, timeout=LOCK_WAIT_S, **kw)
    except lib.TargetsError as exc:
        raise _refused(exc) from None


def _clean(body: dict[str, Any], keys=None) -> dict[str, Any]:
    """Blank strings are "not set" (None); strings are trimmed. Types are the library's to check."""
    out = {}
    for k, v in (body or {}).items():
        if keys is not None and k not in keys:
            continue
        if isinstance(v, str):
            v = v.strip() or None
        out[k] = v
    return out


def _done(lib, t, message: str) -> dict[str, Any]:
    return {"ok": True, "target": lib.to_mapping(t), "message": f"{message}; {APPLY_NOTE}"}


def add(cfg: Settings, body: dict[str, Any]) -> dict[str, Any]:
    from .actions import ActionError
    mapping = {k: v for k, v in _clean(body).items() if v is not None}
    if not isinstance(mapping.get("name"), str) or not mapping.get("name"):
        raise ActionError(400, "name is required", field="name")
    lib, t = _call(cfg, "add", mapping)
    return _done(lib, t, f"added {t.name}" + ("" if t.enabled else " (disabled)"))


def set_(cfg: Settings, name: str, body: dict[str, Any]) -> dict[str, Any]:
    from .actions import ActionError
    unknown = sorted(set(body or {}) - set(EDITABLE))
    if unknown:
        raise ActionError(400, f"the console edits only {', '.join(EDITABLE)}; not {', '.join(unknown)} "
                               "(remove and add the target, or use `python -m scraper targets set`)",
                          field=unknown[0])
    changes = _clean(body, EDITABLE)
    if not changes:
        raise ActionError(400, "nothing to change")
    lib, t = _call(cfg, "set", name, **changes)
    said = ", ".join(f"{k}={'(default)' if v is None else v}" for k, v in changes.items())
    return _done(lib, t, f"{name}: {said}")


def enable(cfg: Settings, name: str) -> dict[str, Any]:
    lib, t = _call(cfg, "enable", name)
    return _done(lib, t, f"{name} enabled")


def disable(cfg: Settings, name: str) -> dict[str, Any]:
    lib, t = _call(cfg, "disable", name)
    return _done(lib, t, f"{name} disabled")


def remove(cfg: Settings, name: str, body: dict[str, Any]) -> dict[str, Any]:
    from .actions import ActionError
    if (body or {}).get("confirm") != name:
        raise ActionError(400, f'remove needs {{"confirm": "{name}"}}: type the target\'s name', field="confirm")
    _lib, left = _call(cfg, "remove", name, data_dir=cfg.scraper_data_dir)
    return {"ok": True, "removed": name, "targets": [t.name for t in left],
            "message": f"removed {name}; {APPLY_NOTE}"}
