"""The console's Behaviour pane: how the Scraper behaves (its limits, timings and clicks), read and
changed through the Scraper's own catalogue, ``scraper/behaviour.py``.

That library holds every setting, its default, kind and bounds, the check of a value, the lock and
the atomic save of ``<scraper data>/behaviour.toml``. Nothing here parses or writes the TOML or
repeats a bound: a refusal is the library's message, passed back to the page with the setting's key
as its field. The library is loaded from the same package ``scraper_targets.library`` loads.

The values shown are the FILE's: each read loads it into this process (``behaviour.load``) under a
lock, so the console's own use of the catalogue (``schedule_words``' default fetch interval) follows
an edit too. The running Scraper takes a change up within 60 s (``scraper/runner.py``); HubAI does
not change these (the register, "Scraping conduct").
"""
from __future__ import annotations

import importlib
import json
import threading
from types import ModuleType
from typing import Any

from ..config import Settings
from . import scraper_targets

CONFIRM_ALL = "RESET"
APPLY_NOTE = scraper_targets.APPLY_NOTE
FILE = "behaviour.toml"

_lock = threading.Lock()          # behaviour.load / listing share module state in this process


def library(cfg: Settings) -> ModuleType:
    """``scraper.behaviour``, from the package ``scraper_targets.library`` loads."""
    scraper_targets.library(cfg)
    return importlib.import_module("scraper.behaviour")


def follow(cfg: Settings) -> list[str]:
    """Make this process's values the file's; its problems. Raises if the library does not load."""
    lib = library(cfg)
    with _lock:
        return lib.load(cfg.scraper_data_dir)


def view(cfg: Settings) -> dict[str, Any]:
    """Every setting with the file's value, in catalogue order (``behaviour.listing``), the file, how
    many differ from their default and what in the file was refused. Never raises: a library that
    does not load is ``error`` with no settings."""
    out: dict[str, Any] = {"file": str(cfg.scraper_data_dir / FILE), "changed": 0, "problems": [],
                           "settings": [], "error": None, "apply_note": APPLY_NOTE}
    try:
        lib = library(cfg)
        with _lock:
            problems = lib.load(cfg.scraper_data_dir)
            rows = lib.listing()
        out["file"] = str(lib.path(cfg.scraper_data_dir))
    except Exception as exc:  # noqa: BLE001 - LibraryMissing, an import error: shown, never raised
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        return out
    out.update(problems=problems, settings=rows, changed=sum(1 for r in rows if r["changed"]))
    return out


# ---- writing -----------------------------------------------------------------------------------

def _lib(cfg: Settings) -> ModuleType:
    from .actions import ActionError
    try:
        return library(cfg)
    except Exception as exc:  # noqa: BLE001
        raise ActionError(503, f"the Scraper's behaviour library is not available: {exc}") from None


def _refused(exc: BaseException, field: str):
    from .actions import ActionError
    msg = str(exc)
    if scraper_targets.BUSY in msg:
        return ActionError(409, msg + " (the CLI is mid-change)", retry=True, field=field)
    return ActionError(400, msg, field=field)


def _key(body: dict[str, Any]) -> str:
    from .actions import ActionError
    key = (body or {}).get("key")
    if not isinstance(key, str) or not key.strip():
        raise ActionError(400, "key is required: the setting's name, as scraper/behaviour.py has it", field="key")
    return key.strip()


def _row(cfg: Settings, lib: ModuleType, key: str) -> tuple[dict[str, Any] | None, int]:
    """The setting's entry as the file now has it, and how many settings differ from default."""
    with _lock:
        lib.load(cfg.scraper_data_dir)
        rows = lib.listing()
    return next((r for r in rows if r["key"] == key), None), sum(1 for r in rows if r["changed"])


def said(value: Any) -> str:
    """A value as the action log shows it."""
    return value if isinstance(value, str) else json.dumps(value)


def set_(cfg: Settings, body: dict[str, Any]) -> dict[str, Any]:
    from .actions import ActionError
    key = _key(body)
    if "value" not in (body or {}) or body["value"] is None:
        raise ActionError(400, f"{key}: a value is required", field=key)
    lib = _lib(cfg)
    try:
        value = lib.set_(cfg.scraper_data_dir, key, body["value"], timeout=scraper_targets.LOCK_WAIT_S)
    except lib.BehaviourError as exc:
        raise _refused(exc, key) from None
    row, changed = _row(cfg, lib, key)
    s = lib.setting(key)
    unit = f" {s.unit}" if s.unit else ""
    return {"ok": True, "setting": row, "changed": changed,
            "message": f"{key} = {lib.show(value)}{unit} (default {lib.show(s.default)}); {APPLY_NOTE}"}


def reset(cfg: Settings, body: dict[str, Any]) -> dict[str, Any]:
    from .actions import ActionError
    body = body or {}
    lib = _lib(cfg)
    if body.get("all") is True:
        if body.get("confirm") != CONFIRM_ALL:
            raise ActionError(400, f'reset all needs {{"all": true, "confirm": "{CONFIRM_ALL}"}}: type {CONFIRM_ALL}',
                              field="confirm")
        try:
            gone = lib.reset(cfg.scraper_data_dir, None, timeout=scraper_targets.LOCK_WAIT_S)
        except lib.BehaviourError as exc:
            raise _refused(exc, "_") from None
        return {"ok": True, "reset": gone, "changed": 0,
                "message": f"every setting back to its default ({len(gone)} were changed); {APPLY_NOTE}"}
    key = _key(body)
    try:
        gone = lib.reset(cfg.scraper_data_dir, key, timeout=scraper_targets.LOCK_WAIT_S)
    except lib.BehaviourError as exc:
        raise _refused(exc, key) from None
    row, changed = _row(cfg, lib, key)
    default = f" {lib.show(row['default'])}" if row else ""
    return {"ok": True, "setting": row, "changed": changed,
            "message": f"{key} back to its default{default}{'' if gone else ' (it was already)'}; {APPLY_NOTE}"}
