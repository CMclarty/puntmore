"""POST /api/aw26/fairs — the day's fair prices from Bookie Bashing's Group
Tracker, sent by the local script (local/bookiebashing_tracker.py).

Key-guarded like /paddy/saves-fair, and with the SAME key: both come from the
one Bookie Bashing script machine, behind Callum's login, which never reaches
the server. Every slip still waiting on a fair price is priced on arrival.
"""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..aw26.legs import TEAM_MIN, classify
from ..aw26.results import MatchStats, apply_match_stats
from ..aw26.service import (
    FAIRS_KEEP_DAYS, parse_when, price_slip, recent_fairs,
)
from ..database import get_db
from ..models import AwFair, AwLeg, AwSlip, ValueLine
from ..normalise import split_sides, team_similarity
from ..aw26.record import Refused, record_slip
from ..betfeed.service import record_scores
from ..schemas import (
    AwFairsOut, AwResultsOut, AwSlipIngestOut, AwSlipLegIn, AwSlipRecordIn, OpenLeaguesOut,
)
from ..time_utils import utcnow
from .aw26 import _sweep_events
from .saves_ingest import require_ingest_key

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/aw26")


class FairIn(BaseModel):
    home: str = Field(min_length=1, max_length=80)
    away: str = Field(min_length=1, max_length=80)
    kickoff: str | None = None           # ISO 8601 UTC, or as printed (London)
    market: str | None = Field(default=None, max_length=120)
    selection: str | None = Field(default=None, max_length=120)
    fair_odds: float = Field(gt=1, lt=1000)


class FairsIn(BaseModel):
    fairs: list[FairIn] = Field(max_length=5000)


@router.post("/fairs", response_model=AwFairsOut, dependencies=[Depends(require_ingest_key)])
def post_fairs(batch: FairsIn, db: Session = Depends(get_db)):
    now = utcnow()
    for f in batch.fairs:
        kind, line, side = classify(f.market, f.selection)
        db.add(AwFair(received_at=now, home=f.home, away=f.away,
                      kickoff=parse_when(f.kickoff, now) if f.kickoff else None,
                      market=f.market, selection=f.selection,
                      kind=kind, line=line, side=side, fair_odds=f.fair_odds))
    # Bounded: two weeks of scrapes, never every one ever sent.
    db.query(AwFair).filter(AwFair.received_at < now - timedelta(days=FAIRS_KEEP_DAYS)).delete()
    db.flush()
    fairs = recent_fairs(db, now)
    waiting = db.query(AwSlip).filter(AwSlip.ev.is_(None)).all()
    for slip in waiting:
        price_slip(slip, fairs)
    db.commit()
    return {"stored": len(batch.fairs),
            "priced": sum(1 for s in waiting if s.ev is not None),
            "still_unpriced": sum(1 for s in waiting if s.ev is None)}


# --- a slip read OFF this machine (desktop Claude), not the server OCR --------
#
# The /slips upload runs the server OCR on a photo. This path is for a slip that
# has ALREADY been transcribed elsewhere — the phone-driven desktop session
# reads the terminal photo and BB fairs, then POSTs the result here. Same key as
# the fairs/saves ingest (one Bookie Bashing machine, behind Callum's login).
#
# It reuses build_slip + price_slip + settle_slip, so an ingested slip is priced,
# settled and profit-tracked exactly like a photo upload — only the reading moved
# off the server. A fair_odds given per leg (looked up on BB at read time) is
# kept; a leg left without one is matched against the fairs table as usual.


# The request shapes live in schemas.py (shared with POST /aw26/slips/record).
SlipLegIn = AwSlipLegIn


class SlipIn(AwSlipRecordIn):
    user: str = Field(default="ingest", max_length=80)


@router.post("/slips/ingest", response_model=AwSlipIngestOut,
             dependencies=[Depends(require_ingest_key)])
def ingest_slip(body: SlipIn, request: Request, db: Session = Depends(get_db)):
    """Create an AW 26 slip from an already-read bet (no photo, no server OCR).

    Priced and settled on the spot, the same as an upload: a later edit or
    fairs scrape re-prices it the usual way. A receipt already in is refused,
    so the same slip sent twice is not counted twice."""
    try:
        slip = record_slip(db, body, body.user, _sweep_events(request))
    except Refused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return {"id": slip.id, "receipt": slip.receipt, "offer": slip.offer,
            "stake": slip.stake, "ev": slip.ev, "ev_note": slip.ev_note,
            "status": slip.status, "legs": len(slip.legs),
            "unpriced_legs": sum(1 for lg in slip.legs if lg.fair_odds is None)}


# --- finished-match results, scraped off FlashScore on the desktop -----------
#
# The FlashScore scraper (local/flashscore_results.py) reads each finished
# match's stats and POSTs them here. Leg results are set DETERMINISTICALLY from
# the stats (aw26/results.py) and the slip settles on arrival — single source,
# applied directly, no second-source double-check (Callum, 22 Oct 2026). Same
# key as the fairs/slips ingest. home/away must be in the match's printed order.


class MatchStatsIn(BaseModel):
    home: str = Field(min_length=1, max_length=80)
    away: str = Field(min_length=1, max_length=80)
    goals_home: int | None = Field(default=None, ge=0, le=100)
    goals_away: int | None = Field(default=None, ge=0, le=100)
    corners_home: int | None = Field(default=None, ge=0, le=100)
    corners_away: int | None = Field(default=None, ge=0, le=100)
    cards_home: int | None = Field(default=None, ge=0, le=100)   # yellow + red count
    cards_away: int | None = Field(default=None, ge=0, le=100)
    saves_home: int | None = Field(default=None, ge=0, le=100)
    saves_away: int | None = Field(default=None, ge=0, le=100)


class ResultsIn(BaseModel):
    matches: list[MatchStatsIn] = Field(min_length=1, max_length=200)


@router.post("/results", response_model=AwResultsOut, dependencies=[Depends(require_ingest_key)])
def post_results(batch: ResultsIn, db: Session = Depends(get_db)):
    stats = [MatchStats(**m.model_dump()) for m in batch.matches]
    out = apply_match_stats(db, stats)
    # The same scores settle the bet feed's served fixtures (GET /api/results).
    # Its own commit, after AW 26's; a failure there never fails this ingest,
    # and this route's response is unchanged.
    try:
        record_scores(db, stats)
    except Exception as exc:
        db.rollback()
        logger.error("bet feed: recording results failed: %s", exc)
    return out


# --- which leagues the FlashScore scraper should read -------------------------
#
# The scraper used to read a fixed list of eight leagues every run. It now asks
# here first: the events on legs still pending on open slips, and the league of
# each, looked up in the value snapshots (a snapshot line carries the sweep's
# sport_key, and outlives the fixture on the sweep by KEEP_DAYS). An event no
# snapshot knows is listed under `unknown`; the scraper then falls back to its
# full list, so a leg is never left unsettled for want of a league.

OPEN_LEAGUES_LOOKBACK_DAYS = 14


@router.get("/open-leagues", response_model=OpenLeaguesOut, dependencies=[Depends(require_ingest_key)])
def open_leagues(db: Session = Depends(get_db)):
    events = sorted({
        leg.event for leg in db.query(AwLeg).join(AwSlip)
        .filter(AwSlip.status == "open", AwLeg.result == "pending").all()
    })
    cutoff = utcnow() - timedelta(days=OPEN_LEAGUES_LOOKBACK_DAYS)
    known = (db.query(ValueLine.home, ValueLine.away, ValueLine.sport_key)
             .filter(ValueLine.sport_key.isnot(None), ValueLine.kickoff >= cutoff)
             .distinct().all())
    sport_keys, unknown = set(), []
    for event in events:
        sides = split_sides(event)
        hit = None
        if sides:
            hit = next((k for h, a, k in known
                        if min(team_similarity(sides[0], h), team_similarity(sides[1], a)) >= TEAM_MIN),
                       None)
        if hit:
            sport_keys.add(hit)
        else:
            unknown.append(event)
    return {"events": events, "sport_keys": sorted(sport_keys), "unknown": unknown}
