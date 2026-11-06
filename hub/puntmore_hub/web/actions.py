"""The few things the Hub Console can do. Each is the CLI's own function, never a shell-out.

Kill switch on and off, resolve an escalation, one inbox pass, a heartbeat, and the
Scraper's target edits (add, set, enable, disable, remove), which are ``scraper/targets.py``'s own
functions (``scraper_targets.py``). Nothing else: no Scraper commands (``resume`` is the Scraper's
CLI), no placement triggers, no ``.env`` writes. Every call, done or refused, is one line in
``data/console-actions.log``.

Token checking lives in ``app.py``; these functions assume the caller is allowed.
"""
from __future__ import annotations

from typing import Any

from .. import alerts, escalation, inbox, killswitch
from ..config import Settings
from ..store import Store, iso, utcnow

ACTIONS_LOG = "console-actions.log"
CONFIRM_OFF = "OFF"
MAX_OUTCOME = 2000


class ActionError(Exception):
    """A refused action: ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra          # more for the page: the form field a refusal is about, retry


def log_action(cfg: Settings, action: str, result: str, client: str | None = None) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    line = f"{iso(utcnow())}\t{client or '-'}\t{action}\t{result}".replace("\n", " ")
    with (cfg.data_dir / ACTIONS_LOG).open("a", encoding="utf-8") as fh:
        fh.write(line[:1000] + "\n")


def kill_on(cfg: Settings, store: Store) -> dict[str, Any]:
    killswitch.turn_on(store, cfg, by="the Hub Console")
    return {"ok": True, "kill_switch": True, "message": "kill switch ON: no placements until it is turned off"}


def kill_off(cfg: Settings, body: dict[str, Any]) -> dict[str, Any]:
    if (body or {}).get("confirm") != CONFIRM_OFF:
        raise ActionError(400, f'kill off needs {{"confirm": "{CONFIRM_OFF}"}}; only Callum turns it off')
    killswitch.turn_off(cfg)
    return {"ok": True, "kill_switch": False, "message": "kill switch off"}


def escalation_resolve(cfg: Settings, store: Store, body: dict[str, Any]) -> dict[str, Any]:
    try:
        esc_id = int((body or {}).get("id"))
    except (TypeError, ValueError):
        raise ActionError(400, "id must be an escalation number") from None
    outcome = str((body or {}).get("outcome") or "").strip()
    if not outcome:
        raise ActionError(400, "outcome is required: what was decided and done")
    if len(outcome) > MAX_OUTCOME:
        raise ActionError(400, f"outcome is longer than {MAX_OUTCOME} characters")
    row = store.escalation(esc_id)
    if row is None:
        raise ActionError(404, f"no escalation #{esc_id}")
    if row["resolved"]:
        raise ActionError(409, f"escalation #{esc_id} was already resolved {row['resolved']}")
    escalation.resolve(store, esc_id, outcome)
    return {"ok": True, "id": esc_id, "message": f"escalation #{esc_id} resolved"}


def inbox_now(cfg: Settings, store: Store) -> dict[str, Any]:
    counts = inbox.ingest(store, cfg)
    return {"ok": True, "counts": counts, "message": ", ".join(f"{k}={v}" for k, v in counts.items())}


def heartbeat_now(cfg: Settings, store: Store) -> dict[str, Any]:
    text = alerts.heartbeat(store, cfg, scraper=inbox.scraper_status(cfg))
    return {"ok": True, "message": text}
