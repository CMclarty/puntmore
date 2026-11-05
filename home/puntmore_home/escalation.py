"""Escalation to the local AI.

The spec gives the local AI (Hermes on this box) ownership of what happens after a failure:
a failed bet, an unhealthy source, a kill-switch trigger. Nothing here retries or re-routes.
We record the escalation, hand it to Hermes with a self-contained brief, and store whatever it
decides. The outcome is queued for Render as ``escalation_outcome``.

Hermes is called as a subprocess (``hermes -p <profile> chat -q ...``) so this module has no
dependency on how the agent is wired. If the binary is missing or disabled, the escalation is
still recorded and alerted; a human or agent resolves it with ``puntmore escalation resolve``.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from . import alerts
from .config import Settings, settings as default_settings
from .store import Store

log = logging.getLogger("puntmore.escalation")

KINDS = ("failed_bet", "unhealthy_source", "blocked_source", "kill_switch", "quarantined_item", "other")


def raise_escalation(store: Store, kind: str, ref: str, detail: str, *, cfg: Settings = default_settings,
                     notify: bool = True) -> int:
    if kind not in KINDS:
        raise ValueError(f"unknown escalation kind {kind!r}")
    esc_id = store.add_escalation(kind, ref, detail)
    alerts.send(store, f"escalation:{kind}:{ref}", f"escalation #{esc_id} {kind} {ref}: {detail}", level="error", cfg=cfg)
    if notify and cfg.escalate_to_local_ai:
        notify_local_ai(store, esc_id, cfg=cfg)
    return esc_id


def brief_for(store: Store, esc_id: int) -> str:
    row = store.escalation(esc_id)
    if row is None:
        raise KeyError(esc_id)
    return (
        f"Puntmore home box escalation #{row['id']} ({row['kind']}) raised {row['created']}.\n"
        f"Reference: {row['ref']}\n"
        f"Detail: {row['detail']}\n\n"
        "You own the decision on what happens next. Nothing has been retried or re-routed. "
        "Investigate with the puntmore CLI (status, runs, raw archive) and resolve with:\n"
        f"  puntmore escalation resolve {row['id']} --outcome \"<what you decided and did>\"\n"
        "If a human decision is needed, say so in the outcome and ask Cal on Telegram."
    )


def notify_local_ai(store: Store, esc_id: int, *, cfg: Settings = default_settings) -> bool:
    """Start Hermes on the escalation in the background. Returns True if it was launched."""
    binary = Path(cfg.hermes_bin) if cfg.hermes_bin else None
    if binary is None or not binary.is_file():
        log.warning("hermes binary not found at %s; escalation #%s left open", binary, esc_id)
        return False
    out_dir = cfg.data_dir / "escalations"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{esc_id}.txt"
    cmd = [str(binary), "-p", cfg.hermes_profile, "chat", "-q", brief_for(store, esc_id)]
    try:
        with open(out_file, "ab") as fh:
            subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        log.info("escalation #%s handed to hermes profile %s (log %s)", esc_id, cfg.hermes_profile, out_file)
        return True
    except OSError as exc:
        log.error("could not launch hermes for escalation #%s: %s", esc_id, exc)
        return False


def resolve(store: Store, esc_id: int, outcome: str) -> None:
    store.resolve_escalation(esc_id, outcome)
    row = store.escalation(esc_id)
    store.outbox_add("escalation_outcome", {"escalation_id": esc_id, "kind": row["kind"], "ref": row["ref"],
                                            "outcome": outcome})
