"""Run one source end to end: budget → fetch → archive raw → parse → validate → store → queue.

Outcomes (``runs.status``):
    ok            parsed at least ``health.min_items`` valid items
    empty         fetched fine but parsed fewer than min_items (suspected layout change) → unhealthy, escalate
    parse_error   parser raised → unhealthy, escalate
    blocked       challenge/CAPTCHA/403 → 24 h cool-off, escalate
    fetch_error   timeout/network → leave health alone, alert if it repeats
    skipped       kill switch, cool-off, budget exhausted, disabled
"""
from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

from .. import alerts, escalation
from ..config import Settings, settings as default_settings
from ..schemas import CandidateTip, utcnow_iso
from ..store import Store, iso, parse_iso, utcnow
from .browser import BlockedError, FetchError, Fetched
from .budget import RequestBudget
from .parsers import load_parser
from .schedule import is_due
from .sources import Source, load_sources

log = logging.getLogger("puntmore.runner")
Fetcher = Callable[[str, Source], Fetched]


@dataclass
class RunResult:
    source: str
    status: str
    items: int = 0
    new_tips: int = 0
    error: str | None = None
    raw_path: str | None = None
    run_id: int | None = None


def archive_raw(cfg: Settings, source: Source, fetched: Fetched) -> Path:
    d = cfg.raw_dir / source.name
    d.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    html_path = d / f"{stamp}.html"
    html_path.write_text(fetched.html)
    if fetched.json_responses:
        (d / f"{stamp}.json").write_text(json.dumps(fetched.json_responses, indent=1)[:5_000_000])
    # keep the last 30 payloads per source
    for old in sorted(d.glob("*.html"))[:-30]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
    return html_path


def validate(items: list[dict], source: Source) -> tuple[list[dict], list[str]]:
    good, problems = [], []
    for it in items:
        missing = [k for k in ("event", "market", "selection") if not it.get(k)]
        if missing:
            problems.append(f"missing {missing}: {it}")
            continue
        price = it.get("advised_price")
        if price is not None and not (source.health.price_min <= float(price) <= source.health.price_max):
            problems.append(f"price {price} out of range: {it}")
            continue
        good.append(it)
    return good, problems


def _default_fetcher(url: str, source: Source, cfg: Settings) -> Fetched:
    from .browser import fetch
    return fetch(url, source, cfg)


def run_source(source: Source, store: Store, *, cfg: Settings = default_settings, fetcher: Fetcher | None = None,
               force: bool = False, notify: bool = True) -> RunResult:
    name = source.name
    if not source.enabled and not force:
        return RunResult(name, "skipped", error="disabled")
    if cfg.kill_switch_on():
        return RunResult(name, "skipped", error="kill switch on")
    state = store.source_state(name)
    cool = parse_iso(state.get("cooloff_until"))
    if cool and cool > utcnow() and not force:
        return RunResult(name, "skipped", error=f"cooling off until {cool.isoformat(timespec='minutes')}")
    budget = RequestBudget(store, cfg.pages_per_hour, cfg.pages_per_day)
    if not budget.allow() and not force:
        alerts.send(store, "budget", f"request budget exhausted (hour/day remaining {budget.remaining()})", cfg=cfg)
        return RunResult(name, "skipped", error="request budget exhausted")

    run_id = store.start_run(name)
    fetch_fn = fetcher or (lambda u, s: _default_fetcher(u, s, cfg))
    try:
        fetched = fetch_fn(source.url, source)
        budget.record(name, source.url, fetched.status)
    except BlockedError as exc:
        budget.record(name, source.url, None)
        until = utcnow() + timedelta(hours=cfg.block_cooloff_hours)
        store.set_source_state(name, healthy=0, cooloff_until=iso(until), note=f"blocked: {exc}")
        store.finish_run(run_id, "blocked", error=str(exc))
        escalation.raise_escalation(store, "blocked_source", name,
                                    f"challenge/block on {source.url}: {exc}. Cooling off until {iso(until)}.",
                                    cfg=cfg, notify=notify)
        return RunResult(name, "blocked", error=str(exc), run_id=run_id)
    except FetchError as exc:
        budget.record(name, source.url, None)
        store.finish_run(run_id, "fetch_error", error=str(exc))
        alerts.send(store, f"fetch_error:{name}", f"{name}: fetch error {exc}", cfg=cfg)
        return RunResult(name, "fetch_error", error=str(exc), run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        store.finish_run(run_id, "fetch_error", error=repr(exc))
        alerts.crash(store, f"fetch:{name}", exc, cfg=cfg)
        return RunResult(name, "fetch_error", error=repr(exc), run_id=run_id)

    raw_path = archive_raw(cfg, source, fetched)
    try:
        parse = load_parser(source.parser, cfg.parsers_dir)
        items = parse(fetched, source)
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()[-800:]
        store.set_source_state(name, healthy=0, note=f"parse_error: {exc!r}")
        store.finish_run(run_id, "parse_error", error=repr(exc), raw_path=str(raw_path))
        escalation.raise_escalation(store, "unhealthy_source", name,
                                    f"parser {source.parser} raised {exc!r}. Raw page archived at {raw_path}.\n{tb}",
                                    cfg=cfg, notify=notify)
        return RunResult(name, "parse_error", error=repr(exc), raw_path=str(raw_path), run_id=run_id)

    good, problems = validate(items, source)
    for p in problems[:5]:
        log.warning("%s: rejected item: %s", name, p)
    if len(good) < source.health.min_items:
        store.set_source_state(name, healthy=0, note=f"empty: {len(good)} items (min {source.health.min_items})")
        store.finish_run(run_id, "empty", items=len(good), raw_path=str(raw_path))
        escalation.raise_escalation(store, "unhealthy_source", name,
                                    f"parsed {len(good)} valid items (min {source.health.min_items}); "
                                    f"{len(problems)} rejected. Suspected layout change. Raw page at {raw_path}.",
                                    cfg=cfg, notify=notify)
        return RunResult(name, "empty", items=len(good), raw_path=str(raw_path), run_id=run_id)

    new = 0
    now = utcnow_iso()
    for it in good:
        tip = CandidateTip(source_id=name, event=it["event"], market=it["market"], selection=it["selection"],
                           advised_price=it.get("advised_price"), advised_stake_units=it.get("advised_stake_units"),
                           tip_time=it.get("tip_time") or now, expiry=it.get("expiry"), origin=it.get("origin", "scrape"),
                           extra={**(it.get("extra") or {}), "trust_weight": source.trust_weight})
        tid = store.add_tip(tip.to_dict())
        if tid:
            new += 1
            store.outbox_add("tip", {"tip_id": tid, **tip.to_dict()})
    store.set_source_state(name, healthy=1, cooloff_until=None, last_ok=now, note=None)
    store.finish_run(run_id, "ok", items=len(good), raw_path=str(raw_path))
    log.info("%s: ok, %d items, %d new", name, len(good), new)
    return RunResult(name, "ok", items=len(good), new_tips=new, raw_path=str(raw_path), run_id=run_id)


def run_due(store: Store, *, cfg: Settings = default_settings, fetcher: Fetcher | None = None,
            notify: bool = True) -> list[RunResult]:
    """Run every enabled source whose schedule says it is due. Called by the timer every few minutes."""
    results: list[RunResult] = []
    now = utcnow()
    for src in load_sources(cfg.sources_dir).values():
        if not src.enabled:
            continue
        last = store.last_run(src.name)
        last_dt = parse_iso(last["started"]) if last else None
        if not is_due(src, last_dt, now, cfg.timezone):
            continue
        results.append(run_source(src, store, cfg=cfg, fetcher=fetcher, notify=notify))
    return results
