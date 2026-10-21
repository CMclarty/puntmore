"""POST /api/aw26/fairs — the day's fair prices from Bookie Bashing's Group
Tracker, sent by the local script (local/bookiebashing_tracker.py).

Key-guarded like /paddy/saves-fair, and with the SAME key: both come from the
one Bookie Bashing script machine, behind Callum's login, which never reaches
the server. Every slip still waiting on a fair price is priced on arrival.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..aw26.legs import classify
from ..aw26.offers import OFFERS
from ..aw26.service import (
    FAIRS_KEEP_DAYS, build_slip, parse_when, price_slip, recent_fairs, settle_slip,
)
from ..database import get_db
from ..models import AwFair, AwSlip
from ..providers.slip_ocr import ExtractedSlip, ExtractedSlipLeg
from ..time_utils import utcnow
from .aw26 import _sweep_events
from .saves_ingest import require_ingest_key

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


@router.post("/fairs", dependencies=[Depends(require_ingest_key)])
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


class SlipLegIn(BaseModel):
    event: str = Field(min_length=1, max_length=120)
    market: str | None = Field(default=None, max_length=120)
    selection: str | None = Field(default=None, max_length=120)
    odds: float | None = Field(default=None, gt=1, le=1000)
    kickoff: str | None = None           # ISO 8601 UTC, or as printed (London)
    fair_odds: float | None = Field(default=None, gt=1, le=1000)


class SlipIn(BaseModel):
    receipt: str | None = Field(default=None, max_length=80)
    placed_at: str | None = None         # as printed (London), or ISO 8601
    offer: str = "refund"
    stake: float | None = Field(default=None, ge=0, lt=1_000_000)
    user: str = Field(default="ingest", max_length=80)
    legs: list[SlipLegIn] = Field(min_length=1, max_length=40)


@router.post("/slips/ingest", dependencies=[Depends(require_ingest_key)])
def ingest_slip(body: SlipIn, request: Request, db: Session = Depends(get_db)):
    """Create an AW 26 slip from an already-read bet (no photo, no server OCR).

    Priced and settled on the spot, the same as an upload: a later edit or
    fairs scrape re-prices it the usual way. A receipt already in is refused,
    so the same slip sent twice is not counted twice."""
    if body.offer not in OFFERS:
        raise HTTPException(status_code=400, detail=f"offer must be one of {', '.join(OFFERS)}")
    if body.receipt and db.query(AwSlip).filter(AwSlip.receipt == body.receipt).first():
        raise HTTPException(status_code=409, detail=f"Slip {body.receipt} is already in")

    read = ExtractedSlip(
        receipt=body.receipt,
        placed_at_text=body.placed_at,
        bet_type=None,
        stake=body.stake,
        potential_returns=None,
        legs=[ExtractedSlipLeg(event=lg.event, market=lg.market, selection=lg.selection,
                               odds=lg.odds, kickoff_text=lg.kickoff) for lg in body.legs],
    )
    slip = build_slip(read, offer=body.offer, user=body.user, image_path=None,
                      events=_sweep_events(request))
    # A fair looked up on BB at read time stands; the rest are matched below.
    for leg, given in zip(slip.legs, body.legs):
        if given.fair_odds is not None:
            leg.fair_odds = given.fair_odds
    price_slip(slip, recent_fairs(db))
    settle_slip(slip)
    db.add(slip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Slip {body.receipt} is already in")
    db.refresh(slip)
    return {"id": slip.id, "receipt": slip.receipt, "offer": slip.offer,
            "stake": slip.stake, "ev": slip.ev, "ev_note": slip.ev_note,
            "status": slip.status, "legs": len(slip.legs),
            "unpriced_legs": sum(1 for lg in slip.legs if lg.fair_odds is None)}
