"""The kill switch: the file ``data/KILL``. One code path for the CLI and the Console; HubAI
uses the CLI. Anyone may turn it on; only Callum turns it off (hub/AGENTS.md).
"""
from __future__ import annotations

from . import escalation
from .config import Settings, settings as default_settings
from .store import Store, utcnow


def turn_on(store: Store, cfg: Settings = default_settings, *, by: str = "the CLI",
            notify: bool = False) -> None:
    f = cfg.kill_switch_file
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"set {utcnow().isoformat()} from {by}\n")
    escalation.raise_escalation(store, "kill_switch", "global", f"kill switch turned ON from {by}",
                                cfg=cfg, notify=notify)


def turn_off(cfg: Settings = default_settings) -> None:
    cfg.kill_switch_file.unlink(missing_ok=True)
