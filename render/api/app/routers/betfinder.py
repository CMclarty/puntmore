"""/api/2up/betfinder — the Bet Finder's operator routes (`accabonus` grant).

Thin: the logic is app/betfinder/. The source registry (list, hand edits),
the pipeline's tips and its quarantine, what the owner's decision would be
offered now, per-source stats, and a manual pass. Nothing here fetches a
tip, places a bet or changes a trust weight on its own.
"""
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..betfinder import candidates as bf_candidates
from ..betfinder import pipeline, sources, stats
from ..config import settings
from ..database import get_db
from ..models import BetFinderTip
from ..schemas import (
    BetFinderCandidateOut, BetFinderRunOut, BetFinderSourceIn, BetFinderSourceOut,
    BetFinderSourceStats, BetFinderTipOut,
)

router = APIRouter(prefix="/2up/betfinder", tags=["2up"])


def _utc(dt):
    return None if dt is None else dt.replace(tzinfo=timezone.utc)


def _source_out(src, st: dict | None) -> BetFinderSourceOut:
    return BetFinderSourceOut(
        name=src.name, kind=src.kind, trust_weight=src.trust_weight, active=bool(src.active),
        stake_mode=src.stake_mode, note=src.note, created_at=_utc(src.created_at),
        updated_at=_utc(src.updated_at), stats=st,
    )


def _tip_out(t: BetFinderTip) -> BetFinderTipOut:
    return BetFinderTipOut(
        tip_id=t.tip_id, hub_client_id=t.hub_client_id, source=t.source, origin=t.origin,
        event_raw=t.event_raw, market_raw=t.market_raw, selection_raw=t.selection_raw,
        market=t.market, tip_time=_utc(t.tip_time), expiry=_utc(t.expiry),
        advised_price=t.advised_price, advised_stake_units=t.advised_stake_units,
        status=t.status, quarantine=t.quarantine,
        quarantine_detail=pipeline.REASONS.get(t.quarantine) if t.quarantine else None,
        retry_until=_utc(pipeline.retry_until(t))
        if t.status == "quarantined" and t.quarantine in pipeline.RETRYABLE else None,
        duplicate_of=t.duplicate_of, event_id=t.event_id, event=t.event,
        selection=t.selection, side=t.side, kickoff=_utc(t.kickoff),
        competition=t.competition, match_score=t.match_score, price=t.price, p_win=t.p_win,
        fair_price=t.fair_price, edge=t.edge, priced_at=_utc(t.priced_at),
        bet_ids=t.bet_id_list, outcome=t.outcome, staked=t.staked, returned=t.returned,
        settled_at=_utc(t.settled_at), created_at=_utc(t.created_at),
    )


@router.get("/sources", response_model=list[BetFinderSourceOut])
def list_sources(db: Session = Depends(get_db)):
    """Every source with its config and its record (strike rate, ROI)."""
    st = stats.source_stats(db)
    return [_source_out(s, st.get(s.name)) for s in sources.all_sources(db)]


@router.patch("/sources/{name}", response_model=BetFinderSourceOut)
def update_source(name: str, req: BetFinderSourceIn, db: Session = Depends(get_db)):
    """Hand edit: trust weight (0..1), active, stake mode, kind, note."""
    try:
        src = sources.update(db, name, **req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if src is None:
        raise HTTPException(status_code=404, detail="no Bet Finder source by that name")
    return _source_out(src, stats.source_stats(db).get(src.name))


@router.get("/stats", response_model=dict[str, BetFinderSourceStats])
def source_stats(db: Session = Depends(get_db)):
    """Per-source strike rate and level-stake ROI, all-time and rolling."""
    return stats.source_stats(db)


@router.get("/tips", response_model=list[BetFinderTipOut])
def list_tips(
    status: str | None = None,
    source: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """The pipeline's tips, newest first."""
    q = db.query(BetFinderTip)
    if status:
        q = q.filter(BetFinderTip.status == status)
    if source:
        q = q.filter(BetFinderTip.source == source)
    return [_tip_out(t) for t in q.order_by(BetFinderTip.id.desc()).limit(limit).all()]


@router.get("/quarantine", response_model=list[BetFinderTipOut])
def quarantine(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    """Tips the pipeline would not price, each with its reason. Never bet."""
    rows = (db.query(BetFinderTip).filter(BetFinderTip.status == "quarantined")
            .order_by(BetFinderTip.id.desc()).limit(limit).all())
    return [_tip_out(t) for t in rows]


@router.get("/candidates", response_model=list[BetFinderCandidateOut])
def preview_candidates(db: Session = Depends(get_db)):
    """What the owner client's next decision would be offered from the Bet
    Finder (before the decision's own filters and caps)."""
    rows = bf_candidates.extra_candidates(db, settings.hub_owner_client_id)
    return [BetFinderCandidateOut(
        event_id=c.event_id, event=c.event, kickoff=c.kickoff, selection=c.selection,
        side=c.side, price=c.price, p_win=c.p_win, fair_price=c.fair_price, edge=c.edge,
        source=c.source, source_scope=c.source_scope, extra=c.extra,
    ) for c in rows]


@router.post("/run", response_model=BetFinderRunOut)
def run_now(request: Request, db: Session = Depends(get_db)):
    """One pipeline pass now (what the scheduled job does). Fetches nothing:
    prices only off the 2-Up feed's cache."""
    return pipeline.run_pass(db, getattr(request.app.state, "twoup_feed", None))
