"""The kill switch: the file ``data/KILL``. One code path for the CLI, the Hub Console and
Render's ``kill_switch`` message (``puntmore link``); Hermes uses the CLI.

Anyone may turn it on; only Cal turns it off (hub/AGENTS.md). Render's message can turn it ON
and never off (PROTOCOL.md, "Kill switches"): an ``on: false`` from Render is escalated and
otherwise ignored.
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


def from_render(store: Store, body: dict, cfg: Settings = default_settings) -> bool:
    """Apply Render's ``kill_switch`` message: ON turns the file on; OFF never removes it.
    Returns whether the switch is on afterwards."""
    if body.get("on") is False:
        escalation.raise_escalation(store, "kill_switch", "render",
                                    "Render asked to turn the kill switch OFF; ignored: only Cal turns it off",
                                    cfg=cfg)
        return cfg.kill_switch_on()
    turn_on(store, cfg, by="Render", notify=True)
    return True


def turn_off(cfg: Settings = default_settings) -> None:
    cfg.kill_switch_file.unlink(missing_ok=True)
