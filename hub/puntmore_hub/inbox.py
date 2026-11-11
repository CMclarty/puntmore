"""Ingest what the Scraper (`scraper/`) drops for the Hub.

The Scraper service is the only thing on the Hub that loads a page. For Finder's sources it
writes one drop file per run into `<scraper data>/hub/tips/` and, when something needs a
decision (a block, a source parsing too few rows), one into `<scraper data>/hub/events/`
(`hub/PROTOCOL.md`). This module, part of Scraper control, turns tips into stored,
deduplicated tips for HubAI, and events into escalations for HubAI. The storage step, `ingest_rows`, is the one path
for a source's rows whoever parsed them: the tipster emails `mail.py` reads go through it
too. Files are moved to `done/` once handled, so a crash between the two never loses or
doubles anything: a tip is deduplicated by its id, an event by its file name.

A tip's id is the Scraper's: each row of a drop carries `tip_id`, minted where the tip
was first seen (`ids.py`). Only an old drop without one has it minted here.

Each file is one envelope (`schemas/protocol.schema.json`, type `scraper_tips`,
`scraper_event` or `scraper_prices`); this reads its `body`. A file written before the envelope
(a bare body) is read as it is, so an update never strands what is already on disk.

The inbox also posts the Scraper's Paddy price drops (`<scraper data>/hub/prices/`, one file
per parse) to Render, the one thing in the inbox that crosses a network (`post_prices`). That
pass runs after the tips and events, so a slow Render never delays them, and holds a
non-blocking lock on `prices/.lock` so two passes never post the same file. Files go oldest
first, each as `{"prices": rows}` to its `route` through `finder.post_json` (the Hub's Render
key). A 2xx, a refusal (a 4xx other than 408 and 429), a file older than `PRICE_MAX_AGE_S` or
an unreadable one is moved to `done/prices/`. When Render is down (408, 429, 5xx, a network
error or timeout), answers 1xx or 3xx (a setup fault, such as a redirect from a wrong
PUNTMORE_RENDER_HTTP) or is not configured, the pass stops and that file and every later one wait for the
next pass, so an older price is never posted after a newer one. Nothing retries in place.
"""
from __future__ import annotations

import fcntl
import http.client
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import escalation, finder
from .config import Settings, settings as default_settings
from .schemas import FILE_TYPES, Tip
from .store import Store, utcnow

log = logging.getLogger("puntmore.inbox")

PRICE_MIN, PRICE_MAX = 1.01, 1000.0
KEEP_DONE = 300
_TIP_ID = re.compile(r"^[0-9a-f]{16,40}$")
# The Render routes a price drop may name: the schema's enum (`scraper_prices`), kept here too.
PRICE_ROUTES = ("/api/paddy/saves-fair", "/api/paddy/most-prices")
PRICE_MAX_AGE_S = 900            # a price drop older than this is moved aside unsent
PRICE_COUNT_KEYS = ("price_files", "price_rows", "price_dropped", "price_waiting")


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


def _body(d: dict) -> dict:
    """The drop's body: the envelope's, or the whole file when it predates the envelope."""
    if d.get("type") in FILE_TYPES and isinstance(d.get("body"), dict):
        return d["body"]
    return d


def tips_from_rows(source: str, rows: list[dict], *, at: str, trust_weight: float) -> tuple[list[tuple[str | None, Tip]], list[str]]:
    """The good rows of one source as (tip_id or None, tip): None when the row carries no
    usable tip_id (an old drop), so the store mints it. A row with no event, market or
    selection, or a price outside the band, is a problem, never a guess."""
    good, problems = [], []
    for row in rows:
        missing = [k for k in ("event", "market", "selection") if not row.get(k)]
        if missing or not source:
            problems.append(f"missing {missing or ['source']}: {row}")
            continue
        price = row.get("advised_price")
        if price is not None and not (PRICE_MIN <= float(price) <= PRICE_MAX):
            problems.append(f"price {price} out of range: {row}")
            continue
        tid = row.get("tip_id")
        good.append((tid if isinstance(tid, str) and _TIP_ID.match(tid) else None, Tip(
            source_id=source, event=str(row["event"]), market=str(row["market"]), selection=str(row["selection"]),
            advised_price=None if price is None else float(price),
            advised_stake_units=row.get("advised_stake_units"),
            tip_time=row.get("tip_time") or at, expiry=row.get("expiry"),
            origin=row.get("origin") or "scrape",
            extra={**(row.get("extra") or {}), "trust_weight": trust_weight},
        )))
    return good, problems


def ingest_rows(store: Store, source: str, rows: list[dict], *, at: str, trust_weight: float) -> dict:
    """The one storage path for a source's rows, whoever parsed them (the Scraper's drops here,
    a tipster's email in `mail.py`): the good rows become stored tips, deduplicated on `tip_id`.
    A new tip waits for HubAI (`puntmore finder new`, finder.py), which judges it and posts the
    verdict to Render's Finder. `at` (ISO) is the tip_time of a row that has none.
    The row's own `tip_id`, minted where the tip was first seen, is kept; only a row without
    one has it minted by the store. Returns the counts and the problems."""
    tips, problems = tips_from_rows(source, rows, at=at, trust_weight=trust_weight)
    out = {"tips": len(tips), "new_tips": 0, "rejected": len(problems), "problems": problems}
    for given, tip in tips:
        if store.add_tip(tip.to_dict(), tip_id=given):
            out["new_tips"] += 1
    return out


def ingest(store: Store, cfg: Settings = default_settings, *, notify: bool = True,
           opener: finder.Opener | None = None) -> dict:
    """One pass over the Scraper's drop folders: tips, then events, then the price drops
    (`post_prices`, the only step that talks to Render; `opener` stands in for the network in
    tests). Safe to call every few seconds."""
    counts = {"tip_files": 0, "tips": 0, "new_tips": 0, "rejected": 0, "events": 0}
    tips_dir, events_dir, done = cfg.inbox_dir / "tips", cfg.inbox_dir / "events", cfg.inbox_dir / "done"
    for path in sorted(tips_dir.glob("*.json")) if tips_dir.exists() else []:
        try:
            body = _body(json.loads(path.read_text()))
            source = str(body.get("source") or "")
            at = _iso(float(body.get("at") or utcnow().timestamp()))
            rows = list(body.get("rows") or [])
        except (OSError, ValueError, TypeError) as exc:
            log.warning("inbox: unreadable %s: %s", path.name, exc)
            _finish(path, done)
            continue
        counts["tip_files"] += 1
        got = ingest_rows(store, source, rows, at=at, trust_weight=body.get("trust_weight", 1.0))
        for k in ("tips", "new_tips", "rejected"):
            counts[k] += got[k]
        for p in got["problems"][:3]:
            log.warning("inbox %s: rejected %s", source, p)
        _finish(path, done)
    for path in sorted(events_dir.glob("*.json")) if events_dir.exists() else []:
        try:
            body = _body(json.loads(path.read_text()))
            kind = body.get("kind") if body.get("kind") in escalation.KINDS else "other"
            escalation.raise_escalation(store, kind, str(body.get("ref") or "scraper"),
                                        f"[scraper event {path.name}] {body.get('detail') or ''}",
                                        cfg=cfg, notify=notify)
            counts["events"] += 1
        except (OSError, ValueError) as exc:
            log.warning("inbox: unreadable event %s: %s", path.name, exc)
        _finish(path, done)
    counts.update(post_prices(cfg, opener=opener))
    return counts


def _read_price_drop(path: Path) -> dict:
    """A price drop's body, or ValueError naming what is wrong with it."""
    d = json.loads(path.read_text())
    if not isinstance(d, dict):
        raise ValueError("not a JSON object")
    body = _body(d)
    at, route, rows = body.get("at"), body.get("route"), body.get("rows")
    try:                                # an int too large for a float overflows in isfinite
        finite = not isinstance(at, bool) and isinstance(at, (int, float)) and math.isfinite(at)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("`at` is not a finite number")
    if not isinstance(route, str):
        raise ValueError("`route` is not a string")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise ValueError("`rows` is not a non-empty list of objects")
    return body


def _render_down(code: int) -> bool:
    """Render's answer is no verdict on the file, so it waits: try later (408, 429, a server
    error), or a 1xx/3xx, which means the setup is wrong (a redirect from a bad
    PUNTMORE_RENDER_HTTP). Only a 2xx is posted and only another 4xx is a refusal."""
    return not (200 <= code < 300) and not (400 <= code < 500 and code not in (408, 429))


def post_prices(cfg: Settings = default_settings, *, opener: finder.Opener | None = None) -> dict[str, int]:
    """One pass over the Scraper's price drops (`<inbox>/prices/`), oldest first: each is
    posted to Render as `{"prices": rows}` on its route and moved to `done/prices/`. A stale,
    unreadable or refused drop is moved aside unsent; a route this inbox does not know waits.
    When Render is down or not configured the pass stops and the rest wait, in order, for the
    next pass. Returns the counts named by PRICE_COUNT_KEYS."""
    counts = dict.fromkeys(PRICE_COUNT_KEYS, 0)
    prices_dir = cfg.inbox_dir / "prices"
    if not prices_dir.is_dir():
        return counts
    done = cfg.inbox_dir / "done" / "prices"
    with open(prices_dir / ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:                 # another pass (`puntmore inbox`, the Console) holds it
            counts["price_waiting"] = len(list(prices_dir.glob("*.json")))
            return counts
        files = sorted(prices_dir.glob("*.json"))
        now = time.time()
        for i, path in enumerate(files):
            try:
                body = _read_price_drop(path)
            except (OSError, ValueError) as exc:
                log.warning("prices: unreadable %s (%s); moved aside", path.name, exc)
                _finish(path, done)
                counts["price_dropped"] += 1
                continue
            source, route, rows = str(body.get("source") or path.name), body["route"], body["rows"]
            age = now - body["at"]
            if age > PRICE_MAX_AGE_S:
                log.warning("prices %s: %ds old, past the %ds cut-off; moved aside unsent",
                            source, age, PRICE_MAX_AGE_S)
                _finish(path, done)
                counts["price_dropped"] += 1
                continue
            if route not in PRICE_ROUTES:
                log.warning("prices %s: route %r unknown to this inbox; left for one that knows it", source, route)
                counts["price_waiting"] += 1
                continue
            try:
                code, text = finder.post_json(cfg, route, {"prices": rows}, opener)
                reason = f"HTTP {code}" if _render_down(code) else None
            except (OSError, http.client.HTTPException, RuntimeError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
            if reason:
                left = len(files) - i
                log.warning("prices %s: %s; %d file(s) wait for the next pass", source, reason, left)
                counts["price_waiting"] += left
                break
            if 200 <= code < 300:
                log.info("prices %s -> %s: sent %d row(s), Render answered %s", source, route, len(rows), text[:200])
                counts["price_files"] += 1
                counts["price_rows"] += len(rows)
            else:
                log.warning("prices %s -> %s: refused HTTP %d %s; moved aside", source, route, code, text[:200])
                counts["price_dropped"] += 1
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
