"""Alerts: Discord webhook (if configured) plus the log, with de-duplication.

Rules inherited from the PP scraper handover: daily 09:00 heartbeat with a 24 h summary, startup
message, crash alert with traceback, stale-data check, alert de-dup. Silence means broken, so the
heartbeat is the one message that must always arrive.
"""
from __future__ import annotations

import json
import logging
import traceback
import urllib.request
from datetime import timedelta

from .config import Settings, settings as default_settings
from .store import Store, utcnow

log = logging.getLogger("puntmore.alerts")


def _post_discord(webhook: str, content: str) -> bool:
    data = json.dumps({"content": content[:1900]}).encode()
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json", "User-Agent": "puntmore-hub"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 - alerting must never raise
        log.warning("discord webhook failed: %s", exc)
        return False


def send(store: Store, key: str, message: str, *, level: str = "warn",
         cfg: Settings = default_settings, dedup: bool = True) -> bool:
    """Send an alert once per de-dup window for ``key``. Returns True if it went out."""
    if dedup and store.alert_recent(key, timedelta(hours=cfg.alert_dedup_hours)):
        log.info("alert suppressed (dedup) %s: %s", key, message)
        return False
    store.add_alert(key, message)
    prefix = {"info": "ℹ️", "warn": "⚠️", "error": "🚨"}.get(level, "⚠️")
    text = f"{prefix} [{cfg.client_id}] {message}"
    getattr(log, "error" if level == "error" else "warning" if level == "warn" else "info")("%s", text)
    if cfg.discord_webhook:
        return _post_discord(cfg.discord_webhook, text)
    return True


def crash(store: Store, where: str, exc: BaseException, cfg: Settings = default_settings) -> None:
    tb = "".join(traceback.format_exception(exc))[-1500:]
    send(store, f"crash:{where}", f"crash in {where}: {exc!r}\n```\n{tb}\n```", level="error", cfg=cfg)


def startup(store: Store, what: str, cfg: Settings = default_settings) -> None:
    send(store, f"startup:{what}", f"{what} started (dry_run={cfg.dry_run}, kill_switch={cfg.kill_switch_on()})",
         level="info", cfg=cfg, dedup=False)


def heartbeat(store: Store, cfg: Settings = default_settings, scraper: dict | None = None) -> str:
    """Daily summary of the last 24 h. Always sent, never de-duped."""
    s = store.summary_since(utcnow() - timedelta(hours=24))
    scraper = scraper or {}
    lines = [
        f"heartbeat 24h: tips {s['tips']}, bets received {s['bets_received']}, placed {s['bets_placed']}, "
        f"failed {s['bets_failed']}",
        f"open escalations {s['escalations_open']}, outbox pending {s['outbox_pending']}",
    ]
    if scraper:
        lines.append(f"scraper: {'running' if scraper.get('running') else 'NOT RUNNING'}, "
                     f"{len(scraper.get('targets', []))} target(s)")
        if scraper.get("blocked"):
            lines.append("blocked: " + ", ".join(scraper["blocked"]))
        if scraper.get("unhealthy"):
            lines.append("unhealthy sources: " + ", ".join(scraper["unhealthy"]))
    if cfg.kill_switch_on():
        lines.append("KILL SWITCH IS ON")
    if cfg.dry_run:
        lines.append("dry-run mode")
    text = "\n".join(lines)
    send(store, "heartbeat", text, level="info", cfg=cfg, dedup=False)
    return text
