"""AW 26 — Callum's slips (aw26/). Gated on the "aw26" grant (main.py).

A page over the stored slips. Slips are recorded by HubAI (an API client key
granted `aw26`) or corrected on the page; both call these routes. A slip is
priced once every leg has a fair price and settled from its legs' results,
here, when a request asks.

Render stores a slip photo (from the page or Telegram) on the photo queue
(`/aw26/photos`, aw26/photos.py) and serves it back; Qwen reads it on the Hub
and records the slip. Render reads no photo and runs nothing in the background
for AW 26.
"""
import logging
import os
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, selectinload

from ..aw26 import photos, selections, service
from ..aw26.offers import OFFERS
from ..aw26.record import Refused, record_slip
from ..aw26.service import parse_when, price_slip, settle_slip
from ..database import get_db
from ..models import AwLeg, AwPhoto, AwSlip
from .upload import ALLOWED_MEDIA_TYPES, _read_capped
from ..schemas import (
    AwMarkIn, AwMarkOut, AwPhotoFailIn, AwPhotoOut, AwSlipOut, AwSlipRecordIn, AwSlipsOut,
    AwSummaryOut, DeletedOut,
)
from ..security import require_user

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/aw26", tags=["aw26"])

RESULTS = ("pending", "won", "lost", "void")


def _today() -> date:
    return service.london_day(service.utcnow())


def _start():
    """The first moment of AW 26 (naive UTC): a slip placed before it is in
    no figure and no list."""
    return service.day_bounds(service.START)[0]


@router.get("/slips", response_model=AwSlipsOut)
def list_slips(day: date | None = None, db: Session = Depends(get_db)):
    """The slips placed on the London day `day` (default today), newest
    first; none from before the start."""
    lo, hi = service.day_bounds(day or _today())
    slips = (db.query(AwSlip).options(selectinload(AwSlip.legs))
             .filter(AwSlip.placed_at >= max(lo, _start()), AwSlip.placed_at < hi)
             .order_by(AwSlip.placed_at.desc(), AwSlip.id.desc()).all())
    return {"slips": [AwSlipOut.model_validate(s).model_dump(mode="json") for s in slips]}


@router.get("/summary", response_model=AwSummaryOut)
def day_summary(day: date | None = None, db: Session = Depends(get_db)):
    """The figures for the London day `day` (default today) and to date, and
    the selections on that day: a leg's day is its kick-off's, or its slip's
    placed day when it has none; today lists today and every later day. A day
    before the start or after today is refused."""
    today = _today()
    day = day or today
    if not service.START <= day <= today:
        raise HTTPException(status_code=400,
                            detail=f"day must be from {service.START.isoformat()} to {today.isoformat()}")
    start = _start()
    slips = db.query(AwSlip).filter(AwSlip.placed_at >= start).all()
    lo, hi = service.day_bounds(day)

    def within(col):
        return col >= lo if day == today else and_(col >= lo, col < hi)

    legs = (db.query(AwLeg).join(AwLeg.slip).options(contains_eager(AwLeg.slip))
            .filter(AwSlip.placed_at >= start,
                    or_(and_(AwLeg.kickoff.is_not(None), within(AwLeg.kickoff)),
                        and_(AwLeg.kickoff.is_(None), within(AwSlip.placed_at))))
            .order_by(AwLeg.id).all())
    return service.day_summary(slips, selections.group(legs), day, today)


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
    # The photo it was read from goes with it (the slip owns the file).
    db.query(AwPhoto).filter(AwPhoto.slip_id == slip_id).delete()
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
    """The photo the slip was read from: one from before the retirement, or a
    queued photo recorded with it; 404 when there is none."""
    slip = db.get(AwSlip, slip_id)
    if slip is None or not slip.image_path or not os.path.exists(slip.image_path):
        raise HTTPException(status_code=404, detail="No photo")
    return FileResponse(slip.image_path)


# --- the photo queue (aw26/photos.py) ----------------------------------------


@router.post("/photos", response_model=list[AwPhotoOut])
async def upload_photos(files: list[UploadFile] = File(...), source: str = Form("page"),
                        db: Session = Depends(get_db), user: str = Depends(require_user)):
    """Store each photo, waiting for Qwen; nothing reads it here. Every file is
    checked before any is stored: one refused refuses the batch."""
    if source not in photos.SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {', '.join(photos.SOURCES)}")
    if len(files) > photos.MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"At most {photos.MAX_BATCH} photos at once")
    batch = []
    for f in files:
        if f.content_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported image type: {f.content_type}")
        batch.append((await _read_capped(f), f.content_type))
    stored = []
    try:
        for data, media_type in batch:
            stored.append(photos.store(db, data, media_type, user=user, source=source))
        db.commit()
    except Exception:
        db.rollback()
        for photo in stored:
            photos.remove_file(photo.image_path)
        raise
    return stored


@router.get("/photos", response_model=list[AwPhotoOut])
def list_photos(db: Session = Depends(get_db)):
    """Every photo not done, oldest first, once any taken too long ago is
    released."""
    photos.release_stale(db)
    db.commit()
    return (db.query(AwPhoto).filter(AwPhoto.state != "done")
            .order_by(AwPhoto.created_at, AwPhoto.id).all())


def _photo(db: Session, photo_id: int) -> AwPhoto:
    photo = db.get(AwPhoto, photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="No such photo")
    return photo


def _change(db: Session, photo_id: int, change) -> AwPhoto:
    """Apply one of photos.py's state changes and commit; 409 when the state
    does not allow it."""
    photo = _photo(db, photo_id)
    try:
        change(photo)
    except Refused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    db.commit()
    db.refresh(photo)
    return photo


@router.post("/photos/{photo_id}/take", response_model=AwPhotoOut)
def take_photo(photo_id: int, db: Session = Depends(get_db)):
    """Hand a waiting photo to Qwen, once."""
    return _change(db, photo_id, photos.take)


@router.get("/photos/{photo_id}/image")
def photo_image(photo_id: int, db: Session = Depends(get_db)):
    """The stored file, for Qwen to read on the Hub."""
    photo = _photo(db, photo_id)
    if not os.path.exists(photo.image_path):
        raise HTTPException(status_code=404, detail="No photo")
    return FileResponse(photo.image_path)


@router.post("/photos/{photo_id}/fail", response_model=AwPhotoOut)
def fail_photo(photo_id: int, body: AwPhotoFailIn, db: Session = Depends(get_db)):
    """Qwen could not make a slip of it: back on the queue while tries are left."""
    return _change(db, photo_id, lambda p: photos.fail(p, body.reason))


@router.post("/photos/{photo_id}/resend", response_model=AwPhotoOut)
def resend_photo(photo_id: int, db: Session = Depends(get_db)):
    """A failed photo back on the queue, tries from 0."""
    return _change(db, photo_id, photos.resend)


@router.delete("/photos/{photo_id}", response_model=DeletedOut)
def delete_photo(photo_id: int, db: Session = Depends(get_db)):
    """The row and its file go together. A done photo's file is its slip's:
    it goes when the slip is deleted (409 here)."""
    photo = _photo(db, photo_id)
    if photo.state == "done":
        raise HTTPException(status_code=409, detail="The photo is a slip's: delete the slip")
    path = photo.image_path
    db.delete(photo)
    db.commit()
    photos.remove_file(path)
    return {"deleted": photo_id}
