"""The Hub daemon (`puntmore daemon`; `puntmore link` is the same command under the name the
installed unit `puntmore-link.service` uses).

Three loops, no socket. The inbox loop wakes every PRICES_EVERY_S (10 s). When INBOX_EVERY_S
(30 s) has passed since the last full pass it runs one (`inbox.ingest`: tips stored for HubAI,
events raised as escalations, then the Scraper's Paddy price drops posted to Render); on the
other wakes it runs the price pass alone (`inbox.post_prices`), so a price reaches Render
within about 10 s of the Scraper dropping it. The mail loop polls the tipster mailbox every
PUNTMORE_IMAP_EVERY_MIN on its own thread when a mailbox is configured (`mail.poll`, which
writes `mail-status.json`). The AW 26 loop, on its own thread when Render and its key are
configured, passes over AW 26's photo queue on Render every PUNTMORE_AW26_EVERY_S
(`aw26_loop.read_once`: the oldest waiting slip photo taken and read by HubAI, one at a time).
A failed pass of one loop is logged and never stops another.

Each pass is recorded in `daemon-status.json` in the data directory, which the Console's Daemon
card reads: a full pass writes its time, counts and error; a price pass writes only its price
counts, and an error, over the last full pass's record (`record_inbox`). A pass that finds no
price file keeps the last files moved aside, and why, on the record, so the card's warning
lasts until a pass handles another file. The last AW 26 pass is written beside it, under `aw26`.
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
PRICES_EVERY_S = 10
_PRICE_LOG_KEYS = ("price_files", "price_dropped")   # a waiting file: the inbox warns
_PRICE_SEEN_KEYS = ("price_files", "price_dropped", "price_waiting")   # all 0: the pass found no file
_DROPPED_KEYS = ("price_dropped", "price_dropped_reason")
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
    lays its own price counts, or its error, over them; the next full pass replaces it all.
    Either kind that found no price file keeps the last pass's files moved aside and their
    reason (`_DROPPED_KEYS`)."""
    with _lock:
        out = last_status(cfg)
        prev = out.get("inbox") or {}
        prev_counts = prev.get("counts") or {}
        if counts is not None and not any(counts.get(k) for k in _PRICE_SEEN_KEYS):
            counts = {**counts, **{k: prev_counts[k] for k in _DROPPED_KEYS if k in prev_counts}}
        if prices_only and prev:
            merged = {**(prev.get("counts") or {}), **(counts or {})}
            out["inbox"] = {"at": prev.get("at"), "counts": merged or None, "error": error or prev.get("error")}
        else:
            out["inbox"] = {"at": iso(utcnow()), "counts": counts, "error": error}
        _write(cfg, out)


def _record_aw26(cfg: Settings, counts: dict | None, error: str | None) -> None:
    """Write the last AW 26 pass (time, counts or error) under `aw26`, atomically."""
    with _lock:
        out = last_status(cfg)
        out["aw26"] = {"at": iso(utcnow()), "counts": counts, "error": error}
        _write(cfg, out)


def _write(cfg: Settings, out: dict) -> None:
    """Replace the status file with `out`. The caller holds `_lock`."""
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


def _aw26_loop(cfg: Settings, stop: threading.Event, sleep: Callable[[float], None]) -> None:
    while not stop.is_set():
        try:
            _record_aw26(cfg, aw26_loop.read_once(cfg), None)
        except Exception as exc:  # noqa: BLE001 - one bad pass (Render down, a 409) never stops the loop
            _record_aw26(cfg, None, f"{exc.__class__.__name__}: {exc}"[:300])
            log.exception("aw26 pass failed: %s", exc)
        sleep(max(1, cfg.aw26_every_s))


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
    if aw26.configured(cfg):
        threading.Thread(target=_aw26_loop, args=(cfg, stop, sleep), name="aw26", daemon=True).start()
    else:
        log.info("AW 26 photo loop not started: PUNTMORE_RENDER_HTTP or PUNTMORE_RENDER_API_KEY is unset")
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
