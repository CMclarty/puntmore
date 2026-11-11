"""The Hub daemon (`puntmore daemon`; `puntmore link` is the same command under the name the
installed unit `puntmore-link.service` uses).

Two loops, no socket. The inbox loop wakes every PRICES_EVERY_S (10 s). When INBOX_EVERY_S
(30 s) has passed since the last full pass it runs one (`inbox.ingest`: tips stored for HubAI,
events raised as escalations, then the Scraper's Paddy price drops posted to Render); on the
other wakes it runs the price pass alone (`inbox.post_prices`), so a price reaches Render
within about 10 s of the Scraper dropping it. The mail loop polls the tipster mailbox every
PUNTMORE_IMAP_EVERY_MIN on its own thread when a mailbox is configured (`mail.poll`, which
writes `mail-status.json`).

Each pass is recorded in `daemon-status.json` in the data directory, which the Console's Daemon
card reads: a full pass writes its time, counts and error; a price pass writes only its price
counts, and an error, over the last full pass's record (`record_inbox`).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import alerts, inbox, mail
from .config import Settings, settings as default_settings
from .store import Store, iso, utcnow

log = logging.getLogger("puntmore.daemon")

INBOX_EVERY_S = 30
PRICES_EVERY_S = 10
_PRICE_LOG_KEYS = ("price_files", "price_dropped", "price_waiting")
STATUS_FILE = "daemon-status.json"
_lock = threading.Lock()


def status_file(cfg: Settings) -> Path:
    return cfg.data_dir / STATUS_FILE


def last_status(cfg: Settings) -> dict:
    try:
        return json.loads(status_file(cfg).read_text())
    except (OSError, ValueError):
        return {}


def record_inbox(cfg: Settings, counts: dict | None, error: str | None = None, *,
                 prices_only: bool = False) -> None:
    """Write the last inbox pass (time, counts or error) for the Console, atomically. A price
    pass (`prices_only`) keeps the last full pass's time, tips and events counts and error, and
    lays its own price counts, or its error, over them; the next full pass replaces it all."""
    with _lock:
        out = last_status(cfg)
        prev = out.get("inbox") or {}
        if prices_only and prev:
            merged = {**(prev.get("counts") or {}), **(counts or {})}
            out["inbox"] = {"at": prev.get("at"), "counts": merged or None, "error": error or prev.get("error")}
        else:
            out["inbox"] = {"at": iso(utcnow()), "counts": counts, "error": error}
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = status_file(cfg).with_suffix(".tmp")
        tmp.write_text(json.dumps(out))
        os.replace(tmp, status_file(cfg))


def _mail_loop(cfg: Settings, stop: threading.Event, sleep: Callable[[float], None]) -> None:
    while not stop.is_set():
        try:
            counts = mail.poll(Store(cfg.db_path), cfg)
            log.info("mail: %s", {k: counts.get(k) for k in mail.COUNT_KEYS} | {"error": counts.get("error")})
        except Exception as exc:  # noqa: BLE001 - one bad poll never stops the loop
            log.exception("mail poll failed: %s", exc)
        sleep(max(1, cfg.imap_every_min) * 60)


def _inbox_pass(cfg: Settings, full: bool) -> None:
    """One pass: the full inbox, or the price drops alone. A failure is recorded and logged,
    never raised, so it never stops the loop."""
    try:
        if full:
            counts = inbox.ingest(Store(cfg.db_path), cfg)
            record_inbox(cfg, counts)
            if any(counts.get(k) for k in ("tip_files", "events", "price_files", "price_dropped")):
                log.info("inbox: %s", counts)
        else:
            counts = inbox.post_prices(cfg)
            record_inbox(cfg, counts, prices_only=True)
            if any(counts.get(k) for k in _PRICE_LOG_KEYS):
                log.info("prices: %s", counts)
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        record_inbox(cfg, None, f"{exc.__class__.__name__}: {exc}"[:300], prices_only=not full)
        log.exception("%s pass failed: %s", "inbox" if full else "price", exc)


def run(cfg: Settings = default_settings, *, sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic) -> None:
    """Run until stopped (KeyboardInterrupt, or the service manager's SIGTERM). Wakes every
    PRICES_EVERY_S; a full inbox pass when INBOX_EVERY_S has passed on `clock` since the last
    one (always on the first wake), the price pass alone otherwise."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    alerts.startup(Store(cfg.db_path), "daemon", cfg)
    stop = threading.Event()
    if cfg.mail_configured:
        threading.Thread(target=_mail_loop, args=(cfg, stop, sleep), name="mail", daemon=True).start()
    last_full: float | None = None
    try:
        while True:
            now = clock()
            full = last_full is None or now - last_full >= INBOX_EVERY_S
            if full:
                last_full = now
            _inbox_pass(cfg, full)
            sleep(PRICES_EVERY_S)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
