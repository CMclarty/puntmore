"""/api/2up — the 2-Up engine, part of the 365 tool (`accabonus` grant).

Thin: the logic is app/twoup/. Read and report, plus the per-client kill
switch. Nothing here places a bet or talks to the Hub; the Hub endpoint
sends what `ledger.pending_bet_lists` returns.

These routes are the OPERATOR's view (every client, behind the 365 grant).
Per-client keys for clients reading their own ledger belong to the Hub
endpoint, not here.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..schemas import (
    TwoUpAccountOut, TwoUpBalanceOut, TwoUpBetOut, TwoUpCandidateOut, TwoUpClientOut,
    TwoUpDiagnosticsOut, TwoUpFeedOut, TwoUpLedgerOut,
)
from ..twoup import ledger, model

router = APIRouter(prefix="/2up", tags=["2up"])

# The dials /diagnostics reports: the four filters plus the feed's own three.
_DIAGNOSTIC_DIALS = (
    "twoup_min_edge", "twoup_min_price", "twoup_max_price", "twoup_min_lead_minutes",
    "twoup_tournament_ids", "twoup_feed_ttl", "twoup_horizon_hours",
)


def _feed(request: Request):
    return getattr(request.app.state, "twoup_feed", None)


def _client_out(db: Session, c) -> TwoUpClientOut:
    return TwoUpClientOut(
        client_id=c.client_id, name=c.name, bankroll=c.bankroll or 0.0,
        pooled_bankroll=ledger.pooled_bankroll(db, c), kill_switch=bool(c.kill_switch),
        dry_run=bool(c.dry_run), is_owner=bool(c.is_owner),
    )


def _utc(dt):
    return None if dt is None else dt.replace(tzinfo=timezone.utc)


@router.get("/feed", response_model=TwoUpFeedOut)
def get_feed(request: Request, value_only: bool = Query(default=False)):
    """Every priced side of every qualifying fixture in the horizon (or only
    those passing the price band and edge floor), best edge first."""
    feed = _feed(request)
    now = datetime.now(timezone.utc)
    if feed is None:
        return TwoUpFeedOut(generated_at=now, available=False,
                            status={"degraded": True,
                                    "cannot_price": ["ALL — ODDSPAPI_API_KEY unset"]},
                            candidates=[])
    rows = sorted(feed.candidates(now), key=lambda c: c.edge, reverse=True)
    out = []
    for c in rows:
        ok = model.passes_filters(c, feed.params) is None
        if value_only and not ok:
            continue
        out.append(TwoUpCandidateOut(
            event_id=c.event_id, event=c.event, home=c.home, away=c.away, kickoff=c.kickoff,
            competition=c.competition, selection=c.selection, side=c.side, market=c.market,
            price=c.price, p_win=round(c.p_win, 6), rescue=c.rescue,
            fair_price=c.fair_price, edge=round(c.edge, 6), ev=round(c.ev, 6), value=ok,
            source_scope=c.source_scope,
        ))
    return TwoUpFeedOut(generated_at=now, available=True, status=feed.status(), candidates=out)


@router.get("/ledger/{client_id}", response_model=TwoUpLedgerOut)
def get_ledger(client_id: str, limit: int = Query(default=200, ge=1, le=2000),
               db: Session = Depends(get_db)):
    """One client's bets (newest first), balances, and exposure per account."""
    c = ledger.get_client(db, client_id)
    if c is None:
        raise HTTPException(status_code=404, detail=f"Unknown client: {client_id}")
    today = ledger.local_day()
    todays = ledger.bets_on_day(db, client_id, today)
    accs = []
    for a in ledger.accounts(db, client_id):
        accs.append(TwoUpAccountOut(
            account_id=a.account_id, balance=a.balance or 0.0, balance_at=_utc(a.balance_at),
            enabled=bool(a.enabled),
            open_exposure=ledger.open_exposure(db, client_id, a.account_id),
            bets_today=sum(1 for b in todays if b.account_id == a.account_id),
        ))
    bets = ledger.recent_bets(db, client_id, limit)
    settled = [b for b in bets if b.status == "settled"]
    staked = round(sum(b.stake for b in settled), 2)
    returned = round(sum(b.return_amount or 0.0 for b in settled), 2)
    return TwoUpLedgerOut(
        client=_client_out(db, c), accounts=accs,
        open_exposure=round(sum(a.open_exposure for a in accs), 2),
        staked=staked, returned=returned, pnl=round(returned - staked, 2),
        bets=[TwoUpBetOut(
            bet_id=b.bet_id, account_id=b.account_id, legs=b.legs_list, stake=b.stake,
            expected_price=b.expected_price, bet_type=b.bet_type, source_scope=b.source_scope,
            not_before=_utc(b.not_before), not_after=_utc(b.not_after), day=b.day,
            edge=b.edge, status=b.status, dry_run=bool(b.dry_run), hub_dry_run=b.hub_dry_run,
            actual_price=b.actual_price, new_price=b.new_price, fail_reason=b.fail_reason,
            outcome=b.outcome, return_amount=b.return_amount, created_at=_utc(b.created_at),
            sent_at=_utc(b.sent_at), placed_at=_utc(b.placed_at), settled_at=_utc(b.settled_at),
        ) for b in bets],
        balances=[TwoUpBalanceOut(account_id=r.account_id, balance=r.balance, ts=_utc(r.ts))
                  for r in ledger.recent_balances(db, client_id)],
    )


def _set_kill(db: Session, client_id: str, on: bool) -> TwoUpClientOut:
    c = ledger.set_kill(db, client_id, on)
    if c is None:
        raise HTTPException(status_code=404, detail=f"Unknown client: {client_id}")
    return _client_out(db, c)


@router.post("/clients/{client_id}/kill", response_model=TwoUpClientOut)
def kill_client(client_id: str, db: Session = Depends(get_db)):
    """Stop this client's bet feed at once: no bets decided, none handed out."""
    return _set_kill(db, client_id, True)


@router.post("/clients/{client_id}/resume", response_model=TwoUpClientOut)
def resume_client(client_id: str, db: Session = Depends(get_db)):
    """Clear this client's kill switch (the global one is an .env setting)."""
    return _set_kill(db, client_id, False)


@router.get("/diagnostics", response_model=TwoUpDiagnosticsOut)
def get_diagnostics(request: Request, db: Session = Depends(get_db)):
    """What the feed last saw, the rescue table in use, and the dials. No secrets."""
    feed = _feed(request)
    table = feed.table if feed is not None else model.load_rescue_table(
        settings.twoup_rescue_table_path or None)
    return TwoUpDiagnosticsOut(
        feed=feed.status() if feed is not None else {
            "degraded": True, "cannot_price": ["ALL — ODDSPAPI_API_KEY unset"]},
        rescue_table={
            "path": table.path, "loaded": table.loaded, "default": table.default,
            "key": table.key,
            "buckets": [{"lo": b.lo, "hi": b.hi, "rescue": b.rescue, "n": b.n,
                         "source": b.source} for b in table.buckets],
        },
        global_kill_switch=settings.twoup_kill_switch,
        settings={k: getattr(settings, k) for k in _DIAGNOSTIC_DIALS},
    )
