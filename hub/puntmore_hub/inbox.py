"""Ingest what the Scraper (`scraper/`) drops for the Hub.

The Scraper is the only thing on the Hub that loads a page. For Bet Finder sources it
writes one JSON file per run into `<scraper data>/hub/tips/` and, when something needs a
decision (a block, a source parsing too few rows), one into `<scraper data>/hub/events/`.
This module turns tips into stored, deduplicated candidate tips queued for Render, and
events into escalations for the local AI. Files are moved to `done/` once handled, so a
crash between the two never loses or doubles anything: a tip is deduplicated by its id,
an event by its file name.

A tip's id is the Scraper's: each row of a drop carries `tip_id`, minted where the tip
was first seen (`ids.py`). Only an old drop without one has it minted here.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from . import escalation
from .config import Settings, settings as default_settings
from .schemas import CandidateTip
from .store import Store, parse_iso, utcnow

log = logging.getLogger("puntmore.inbox")

PRICE_MIN, PRICE_MAX = 1.01, 1000.0
KEEP_DONE = 300
_TIP_ID = re.compile(r"^[0-9a-f]{16,40}$")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _finish(path: Path, done: Path) -> None:
    done.mkdir(parents=True, exist_ok=True)
    try:
        path.rename(done / path.name)
    except OSError:
        path.unlink(missing_ok=True)
    for old in sorted(done.glob("*.json"))[:-KEEP_DONE]:
        old.unlink(missing_ok=True)


def _tips_from(body: dict) -> tuple[list[tuple[str | None, CandidateTip]], list[str]]:
    """The drop's good rows as (tip_id or None, tip): None when the row carries no
    usable tip_id (an old drop), so the store mints it."""
    good, problems = [], []
    at = _iso(float(body.get("at") or utcnow().timestamp()))
    source = str(body.get("source") or "")
    weight = body.get("trust_weight", 1.0)
    for row in body.get("rows") or []:
        missing = [k for k in ("event", "market", "selection") if not row.get(k)]
        if missing or not source:
            problems.append(f"missing {missing or ['source']}: {row}")
            continue
        price = row.get("advised_price")
        if price is not None and not (PRICE_MIN <= float(price) <= PRICE_MAX):
            problems.append(f"price {price} out of range: {row}")
            continue
        tid = row.get("tip_id")
        good.append((tid if isinstance(tid, str) and _TIP_ID.match(tid) else None, CandidateTip(
            source_id=source, event=str(row["event"]), market=str(row["market"]), selection=str(row["selection"]),
            advised_price=None if price is None else float(price),
            advised_stake_units=row.get("advised_stake_units"),
            tip_time=row.get("tip_time") or at, expiry=row.get("expiry"),
            origin=row.get("origin") or "scrape",
            extra={**(row.get("extra") or {}), "trust_weight": weight},
        )))
    return good, problems


def ingest(store: Store, cfg: Settings = default_settings, *, notify: bool = True) -> dict:
    """One pass over the Scraper's drop folders. Safe to call every few seconds."""
    counts = {"tip_files": 0, "tips": 0, "new_tips": 0, "rejected": 0, "events": 0}
    tips_dir, events_dir, done = cfg.inbox_dir / "tips", cfg.inbox_dir / "events", cfg.inbox_dir / "done"
    for path in sorted(tips_dir.glob("*.json")) if tips_dir.exists() else []:
        try:
            body = json.loads(path.read_text())
            tips, problems = _tips_from(body)
        except (OSError, ValueError) as exc:
            log.warning("inbox: unreadable %s: %s", path.name, exc)
            _finish(path, done)
            continue
        counts["tip_files"] += 1
        counts["tips"] += len(tips)
        counts["rejected"] += len(problems)
        for p in problems[:3]:
            log.warning("inbox %s: rejected %s", body.get("source"), p)
        for given, tip in tips:
            tid = store.add_tip(tip.to_dict(), tip_id=given)
            if tid:
                counts["new_tips"] += 1
                store.outbox_add("tip", {"tip_id": tid, **tip.to_dict()})
        _finish(path, done)
    for path in sorted(events_dir.glob("*.json")) if events_dir.exists() else []:
        try:
            body = json.loads(path.read_text())
            kind = body.get("kind") if body.get("kind") in escalation.KINDS else "other"
            escalation.raise_escalation(store, kind, str(body.get("ref") or "scraper"),
                                        f"[scraper event {path.name}] {body.get('detail') or ''}",
                                        cfg=cfg, notify=notify)
            counts["events"] += 1
        except (OSError, ValueError) as exc:
            log.warning("inbox: unreadable event %s: %s", path.name, exc)
        _finish(path, done)
    return counts


def scraper_status(cfg: Settings = default_settings) -> dict:
    """What the Scraper last said about itself: heartbeat age and per-target state,
    read from the files it writes. Stale means the Scraper is not running."""
    out: dict = {"running": False, "heartbeat_age_s": None, "targets": [], "unhealthy": [], "blocked": []}
    hb = cfg.scraper_data_dir / "heartbeat"
    try:
        age = utcnow().timestamp() - float(hb.read_text().strip())
        out["heartbeat_age_s"] = int(age)
        out["running"] = age < cfg.scraper_stale_minutes * 60
    except (OSError, ValueError):
        pass
    status = cfg.scraper_data_dir / "status.json"
    try:
        body = json.loads(status.read_text())
    except (OSError, ValueError):
        return out
    for t in body.get("targets", []):
        out["targets"].append(t)
        detail = (t.get("detail") or "").lower()
        if t.get("state") == "blocked":
            out["blocked"].append(t["name"])
        elif "layout change" in detail or "parser failed" in detail or "parser not written" in detail:
            out["unhealthy"].append(t["name"])
    out["sites"] = body.get("sites", [])
    return out
