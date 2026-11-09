"""AW 26 — Callum's slips (aw26/). Gated on the "aw26" grant (main.py).

A page over the stored slips. Slips are recorded by HubAI (an API client key
granted `aw26`) or corrected on the page; both call these routes. A slip is
priced once every leg has a fair price and settled from its legs' results,
here, when a request asks. Render reads no photo and runs nothing in the
background for AW 26.
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..aw26 import selections
from ..aw26.offers import OFFERS
from ..aw26.record import Refused, record_slip
from ..aw26.service import parse_when, price_slip, settle_slip, summary
from ..database import get_db
from ..models import AwLeg, AwSlip
from ..schemas import AwMarkIn, AwMarkOut, AwSlipOut, AwSlipRecordIn, AwSlipsOut, DeletedOut
from ..security import require_user

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/aw26", tags=["aw26"])

RESULTS = ("pending", "won", "lost", "void")


def _all(db) -> list[AwSlip]:
    return db.query(AwSlip).order_by(AwSlip.placed_at.desc(), AwSlip.id.desc()).all()


@router.get("/slips", response_model=AwSlipsOut)
def list_slips(db: Session = Depends(get_db)):
    slips = _all(db)
    return {
        "summary": summary(slips),
        "slips": [AwSlipOut.model_validate(s).model_dump(mode="json") for s in slips],
    }


@router.post("/slips/record", response_model=AwSlipOut)
def record(body: AwSlipRecordIn, db: Session = Depends(get_db),
           user: str = Depends(require_user)):
    """RECORD a bet already placed — structured legs, each with the fair price
    it was judged on where there is one. Priced and settled on the spot; the
    slip is filed under the caller (a key acts as "api:<name>"). A receipt
    already in is refused (409). Nothing here places a bet."""
    try:
        return record_slip(db, body, user)
    except Refused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


class LegEdit(BaseModel):
    id: int | None = None                  # None = a leg the record missed
    event: str = Field(min_length=1, max_length=120)
    market: str | None = Field(default=None, max_length=120)
    selection: str | None = Field(default=None, max_length=120)
    odds: float | None = Field(default=None, gt=1, le=1000)
    fair_odds: float | None = Field(default=None, gt=1, le=1000)
    result: str = "pending"


class SlipEdit(BaseModel):
    offer: str | None = None
    stake: float | None = Field(default=None, ge=0, lt=1_000_000)
    placed_at: str | None = None           # as printed, London
    receipt: str | None = Field(default=None, max_length=80)
    legs: list[LegEdit] | None = None


@router.patch("/slips/{slip_id}", response_model=AwSlipOut)
def edit_slip(slip_id: int, body: SlipEdit, db: Session = Depends(get_db)):
    """A correction, or the legs' results. Re-prices from scratch: a corrected
    record is not a later re-pricing, so the EV is recomputed off the legs'
    fairs as they now stand."""
    slip = db.get(AwSlip, slip_id)
    if slip is None:
        raise HTTPException(status_code=404, detail="No such slip")
    if body.offer is not None:
        if body.offer not in OFFERS:
            raise HTTPException(status_code=400, detail=f"offer must be one of {', '.join(OFFERS)}")
        slip.offer = body.offer
    if body.stake is not None:
        slip.stake = round(body.stake, 2)
    if body.placed_at is not None:
        when = parse_when(body.placed_at, slip.placed_at)
        if when is None:
            raise HTTPException(status_code=400, detail="placed_at unreadable")
        slip.placed_at = when
    if body.receipt is not None:
        slip.receipt = body.receipt.strip() or None
    if body.legs is not None:
        if any(leg.result not in RESULTS for leg in body.legs):
            raise HTTPException(status_code=400, detail=f"result must be one of {', '.join(RESULTS)}")
        existing = {leg.id: leg for leg in slip.legs}
        kept = []
        for i, e in enumerate(body.legs):
            leg = existing.get(e.id) if e.id is not None else None
            if leg is None:
                leg = AwLeg(kickoff=None, fair_assumed=False)
            # kind/line/side and fixture_id were read off a slip's words before
            # the retirement; once the words change they no longer describe the
            # leg, and nothing reads them again.
            if (leg.market, leg.selection) != (e.market, e.selection):
                leg.kind = leg.line = leg.side = None
            if leg.event != e.event:
                leg.fixture_id = None
            # An assumed fair is the odds: it follows a corrected price, until a
            # fair is typed (one that differs from the stored), which is real.
            if e.fair_odds != leg.fair_odds:
                leg.fair_assumed = False
            fair = e.odds if leg.fair_assumed else e.fair_odds
            leg.position, leg.event, leg.market, leg.selection = i, e.event, e.market, e.selection
            leg.odds, leg.result = e.odds, e.result
            leg.fair_odds = fair
            kept.append(leg)
        slip.legs[:] = kept
    slip.edited = True
    slip.ev = None
    price_slip(slip)
    settle_slip(slip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That receipt is already on another slip")
    db.refresh(slip)
    return slip


@router.post("/selections/{leg_id}/result", response_model=AwMarkOut)
def mark_selection(leg_id: int, body: AwMarkIn, db: Session = Depends(get_db)):
    """One result for a selection on every slip from AW 26 that carries it
    (aw26/selections.py), each slip re-settled. A mark is not a correction:
    the EV, the fairs and `edited` stand."""
    if body.result not in RESULTS:
        raise HTTPException(status_code=400, detail=f"result must be one of {', '.join(RESULTS)}")
    try:
        legs, slips = selections.mark(db, leg_id, body.result)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"legs": legs, "slips": slips}


@router.delete("/slips/{slip_id}", response_model=DeletedOut)
def delete_slip(slip_id: int, db: Session = Depends(get_db)):
    """The row and its photo (slips from before the retirement) go together."""
    slip = db.get(AwSlip, slip_id)
    if slip is None:
        raise HTTPException(status_code=404, detail="No such slip")
    path = slip.image_path
    db.delete(slip)
    db.commit()
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            logger.warning("aw26: could not delete image for slip %s", slip_id)
    return {"deleted": slip_id}


@router.get("/slips/{slip_id}/image")
def slip_image(slip_id: int, db: Session = Depends(get_db)):
    """The photo a slip from before the retirement was read from; 404 when
    there is none (every slip recorded since)."""
    slip = db.get(AwSlip, slip_id)
    if slip is None or not slip.image_path or not os.path.exists(slip.image_path):
        raise HTTPException(status_code=404, detail="No photo")
    return FileResponse(slip.image_path)
