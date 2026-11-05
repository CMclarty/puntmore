"""Per client, per day: which candidates to bet, how much, on which account.

Order (spec, "Decision model"): rank candidates by edge; size each with
fractional Kelly on the client's POOLED bankroll; split the stake across the
client's enabled accounts, each share bounded by that account's balance less
its open exposure; apply the constraints — account preservation (3), then
bankroll (1), then availability (2) — and drop what fails. The result is a
list of bets in the `hub/PROTOCOL.md` Bet shape, recorded in the ledger.

Singles only in this version. FOLLOW-UP: doubles / trebles / occasional
fourfolds (spec, "Purpose and boundaries") — combine the top candidates on
an account, with the multi-leg timing rule (>= 1 leg within 24 h, all legs
within 72 h) and `expected_price` the combined price. Cover bets (spec,
"Cover bets") are also not produced yet.

`bet_id` is deterministic for (client, account, event, selection, London
day), so re-running the decision every few minutes can never queue the same
bet twice. A `price_changed` report is the one case that re-bets a selection
the same day: the new bet gets a revision suffix (a new bet_id, as
PROTOCOL.md requires) and is decided at the lower of the feed's price and
the reported one. A `failed` bet's selection is never bet again that day.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import settings
from . import ledger, model
from .model import Candidate, Params

logger = logging.getLogger("uvicorn.error")

# What the last scheduled pass did, for /api/2up/diagnostics. One dict, so
# bounded by construction.
LAST_RUN: dict = {}


def extra_candidates(db: Session, client_id: str) -> list[Candidate]:
    """Bet Finder candidates for this client. Left returning [] — the
    orchestrator fills it in. Rows must carry `source_scope="owner"`; the
    decision only ever considers them for the owner client."""
    return []


def bet_id_for(client_id: str, account_id: str, event_id: str, selection: str,
               day: date, revision: int = 0) -> str:
    key = f"{client_id}|{account_id}|{event_id}|{selection}|{day.isoformat()}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return f"2u-{day:%Y%m%d}-{digest}" + (f"-r{revision}" if revision else "")


@dataclass
class Decision:
    client_id: str
    day: date | None = None
    bets: list[dict] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    skipped: str | None = None           # why nothing was decided at all
    bankroll: float = 0.0
    dry_run: bool = True

    def drop(self, reason: str, n: int = 1) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + n


@dataclass
class _Acct:
    account_id: str
    balance: float
    available: float                     # balance less open exposure, less this pass
    count_today: int
    events_today: set
    trailing: list


def _window_bounds(day: date, params: Params) -> tuple[datetime, datetime]:
    tz = ZoneInfo(params.timezone)
    start = datetime.combine(day, params.window_start, tzinfo=tz)
    end = datetime.combine(day, params.window_end, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def decide(db: Session, client_id: str, candidates: list[Candidate], *,
           now: datetime | None = None, params: Params | None = None) -> Decision:
    """The bets for this client now. Pure with respect to the ledger: reads
    it, writes nothing (see `run_for_client`)."""
    params = params or Params.from_settings()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out = Decision(client_id=client_id)
    client = ledger.get_client(db, client_id)
    if client is None:
        out.skipped = "unknown client"
        return out
    out.dry_run = bool(client.dry_run)
    if settings.twoup_kill_switch:
        out.skipped = "global kill switch on"
        return out
    if client.kill_switch:
        out.skipped = "client kill switch on"
        return out
    local = now.astimezone(ZoneInfo(params.timezone))
    if not model.in_window(local, params):
        out.skipped = "outside betting window"
        return out
    day = local.date()
    out.day = day
    win_start, win_end = _window_bounds(day, params)

    # Bet Finder rows are the owner's alone; any that reach another client
    # are dropped here, whatever produced them.
    pool = list(candidates) + list(extra_candidates(db, client_id))
    owner_rows = [c for c in pool if c.source_scope == "owner"]
    if not client.is_owner and owner_rows:
        out.drop("owner-only candidate", len(owner_rows))
        pool = [c for c in pool if c.source_scope != "owner"]

    # What this client already has today: never the same selection twice,
    # except a re-price after price_changed.
    today = ledger.bets_on_day(db, client_id, day)
    by_sel: dict[tuple, list] = {}
    for b in today:
        by_sel.setdefault((b.event_id, b.selection), []).append(b)

    ranked: list[tuple[Candidate, int]] = []
    for c in pool:
        prior = by_sel.get((c.event_id, c.selection), [])
        revision = 0
        if prior:
            if any(b.status != "price_changed" for b in prior):
                out.drop("already bet today")
                continue
            revision = len(prior)
            reported = min((b.new_price for b in prior if b.new_price), default=None)
            price = min(c.price, reported) if reported else c.price
            c = replace(c, price=price, edge=model.edge(c.p_win, c.rescue, price))
        reason = model.passes_filters(c, params)
        if reason:
            out.drop(reason)
            continue
        ranked.append((c, revision))
    ranked.sort(key=lambda cr: cr[0].edge, reverse=True)

    accts: list[_Acct] = []
    exposure_total = 0.0
    for a in ledger.accounts(db, client_id, enabled_only=True):
        exp = ledger.open_exposure(db, client_id, a.account_id)
        exposure_total += exp
        mine = [b for b in today if b.account_id == a.account_id]
        accts.append(_Acct(
            account_id=a.account_id, balance=a.balance or 0.0,
            available=max(0.0, (a.balance or 0.0) - exp), count_today=len(mine),
            events_today={b.event_id for b in mine},
            trailing=ledger.trailing_stakes(db, client_id, a.account_id, params.trailing_stakes),
        ))
    bankroll = ledger.pooled_bankroll(db, client)
    out.bankroll = bankroll
    pool_left = max(0.0, bankroll - exposure_total)
    if not accts:
        out.skipped = "no enabled accounts"
        return out

    for c, revision in ranked:
        not_before = max(now, win_start)
        not_after = min(c.kickoff - timedelta(minutes=params.min_lead_minutes), win_end)
        if not_after <= not_before:
            out.drop("no placement time inside the window")
            continue
        # Constraint 1 (bankroll): Kelly on the pool, never past what the
        # pool has left after open exposure and this pass's bets.
        total = model.round_stake(min(model.kelly_stake(c, bankroll, params), pool_left),
                                  cap=pool_left)
        if total <= 0:
            out.drop("stake rounds to nothing")
            continue
        remaining = total
        placed_any = False
        # Most room first, so a stake lands on as few accounts as it can.
        order = sorted(accts, key=lambda a: min(a.available, model.account_stake_cap(
            a.balance, a.trailing, params)), reverse=True)
        for a in order:
            if remaining < 1.0:
                break
            # Constraint 3 (account preservation) before the balance.
            if a.count_today >= params.max_bets_per_account_day:
                continue
            if c.event_id in a.events_today:
                continue
            cap = min(model.account_stake_cap(a.balance, a.trailing, params), a.available)
            share = model.round_down_stake(min(remaining, cap))
            if share <= 0:
                continue
            bet_id = bet_id_for(client_id, a.account_id, c.event_id, c.selection, day, revision)
            out.bets.append({
                "bet_id": bet_id,
                "account_id": a.account_id,
                "legs": [{"event_id": c.event_id, "market": c.market,
                          "selection": c.selection, "price": c.price}],
                "stake": share,
                "expected_price": c.price,
                "bet_type": "single",
                "not_before": not_before.isoformat(),
                "not_after": not_after.isoformat(),
                "source_scope": c.source_scope,
                # Not protocol fields: stored by record_bet_list, never sent.
                "edge": round(c.edge, 6),
                "day": day.isoformat(),
            })
            a.count_today += 1
            a.events_today.add(c.event_id)
            a.available -= share
            a.trailing.append(share)
            pool_left -= share
            remaining -= share
            placed_any = True
        if not placed_any:
            out.drop("no account can take it")
    return out


def run_for_client(db: Session, client_id: str, candidates: list[Candidate], *,
                   now: datetime | None = None, params: Params | None = None) -> Decision:
    """Decide and queue. Dry-run clients' bets are recorded too (flagged
    dry_run, never handed out by `pending_bet_lists`)."""
    d = decide(db, client_id, candidates, now=now, params=params)
    if d.bets:
        ledger.record_bet_list(db, client_id, d.bets)
    return d


def run_all(db: Session, feed, *, now: datetime | None = None,
            params: Params | None = None) -> dict:
    """One scheduled pass over every client. Outside the betting window it
    does nothing and reads nothing (no feed refresh, no OddsPapi request).
    ONE log line per pass."""
    params = params or Params.from_settings()
    now = now or datetime.now(timezone.utc)
    summary: dict = {"at": now.isoformat(), "clients": {}, "candidates": 0}
    if settings.twoup_kill_switch:
        summary["skipped"] = "global kill switch on"
    elif not model.in_window(now.astimezone(ZoneInfo(params.timezone)), params):
        summary["skipped"] = "outside betting window"
    elif feed is None:
        summary["skipped"] = "no feed (ODDSPAPI_API_KEY unset)"
    if "skipped" in summary:
        LAST_RUN.clear()
        LAST_RUN.update(summary)
        return summary
    candidates = feed.value(now)
    summary["candidates"] = len(candidates)
    queued = 0
    for client in ledger.clients(db):
        try:
            d = run_for_client(db, client.client_id, candidates, now=now, params=params)
        except Exception as exc:          # one client's failure must not stop the rest
            db.rollback()
            summary["clients"][client.client_id] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            continue
        queued += len(d.bets)
        summary["clients"][client.client_id] = {
            "bets": len(d.bets), "stake": sum(b["stake"] for b in d.bets),
            "dry_run": d.dry_run, "skipped": d.skipped, "dropped": d.dropped,
        }
    summary["queued"] = queued
    errors = [c for c, s in summary["clients"].items() if "error" in s]
    (logger.warning if errors else logger.info)(
        "2up decision: %d value candidate(s), %d bet(s) queued across %d client(s)%s",
        len(candidates), queued, len(summary["clients"]),
        f"; failed for {', '.join(errors)}" if errors else "")
    LAST_RUN.clear()
    LAST_RUN.update(summary)
    return summary
