"""The Hub daemon (`puntmore daemon`; `puntmore link` is the same command under the name the
installed unit `puntmore-link.service` uses).

Three loops, no socket: an inbox pass over the Scraper's drop files every 30 s (`inbox.ingest`:
tips stored for HubAI, events raised as escalations); a poll of the tipster mailbox every
PUNTMORE_IMAP_EVERY_MIN on its own thread when a mailbox is configured (`mail.poll`, which
writes `mail-status.json`); and, on its own thread when Render and its key are configured, a
pass over AW 26's photo queue on Render every PUNTMORE_AW26_EVERY_S (`aw26_loop.read_once`: the
oldest waiting slip photo taken and read by HubAI, one at a time). The last inbox and AW 26
passes are recorded in `daemon-status.json` in the data directory, which the Console's Daemon
card reads. A failed pass of one loop is logged and never stops another.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import alerts, aw26, aw26_loop, inbox, mail
from .config import Settings, settings as default_settings
from .store import Store, iso, utcnow

log = logging.getLogger("puntmore.daemon")

INBOX_EVERY_S = 30
STATUS_FILE = "daemon-status.json"
_lock = threading.Lock()


def status_file(cfg: Settings) -> Path:
    return cfg.data_dir / STATUS_FILE


def last_status(cfg: Settings) -> dict:
    try:
        return json.loads(status_file(cfg).read_text())
    except (OSError, ValueError):
        return {}


def _record(cfg: Settings, key: str, counts: dict | None, error: str | None) -> None:
    """Write a loop's last pass (time, counts or error) under `key`, atomically."""
    with _lock:
        out = last_status(cfg)
        out[key] = {"at": iso(utcnow()), "counts": counts, "error": error}
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = status_file(cfg).with_suffix(".tmp")
        tmp.write_text(json.dumps(out))
        os.replace(tmp, status_file(cfg))


def record_inbox(cfg: Settings, counts: dict | None, error: str | None = None) -> None:
    """Write the last inbox pass (time, counts or error) for the Console."""
    _record(cfg, "inbox", counts, error)


def _mail_loop(cfg: Settings, stop: threading.Event, sleep: Callable[[float], None]) -> None:
    while not stop.is_set():
        try:
            counts = mail.poll(Store(cfg.db_path), cfg)
            log.info("mail: %s", {k: counts.get(k) for k in mail.COUNT_KEYS} | {"error": counts.get("error")})
        except Exception as exc:  # noqa: BLE001 - one bad poll never stops the loop
            log.exception("mail poll failed: %s", exc)
        sleep(max(1, cfg.imap_every_min) * 60)


def _aw26_loop(cfg: Settings, stop: threading.Event, sleep: Callable[[float], None]) -> None:
    while not stop.is_set():
        try:
            _record(cfg, "aw26", aw26_loop.read_once(cfg), None)
        except Exception as exc:  # noqa: BLE001 - one bad pass (Render down, a 409) never stops the loop
            _record(cfg, "aw26", None, f"{exc.__class__.__name__}: {exc}"[:300])
            log.exception("aw26 pass failed: %s", exc)
        sleep(max(1, cfg.aw26_every_s))


def run(cfg: Settings = default_settings, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """Run until stopped (KeyboardInterrupt, or the service manager's SIGTERM)."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    alerts.startup(Store(cfg.db_path), "daemon", cfg)
    stop = threading.Event()
    if cfg.mail_configured:
        threading.Thread(target=_mail_loop, args=(cfg, stop, sleep), name="mail", daemon=True).start()
    if aw26.configured(cfg):
        threading.Thread(target=_aw26_loop, args=(cfg, stop, sleep), name="aw26", daemon=True).start()
    try:
        while True:
            try:
                counts = inbox.ingest(Store(cfg.db_path), cfg)
                record_inbox(cfg, counts)
                if counts.get("tip_files") or counts.get("events"):
                    log.info("inbox: %s", counts)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                record_inbox(cfg, None, f"{exc.__class__.__name__}: {exc}"[:300])
                log.exception("inbox pass failed: %s", exc)
            sleep(INBOX_EVERY_S)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
