"""AW 26 — the acca project's slips (aw26/). Gated on the "aw26" grant (main.py).

One pot for the whole team: every signed-in user with the grant sees and edits
every slip. A slip photo is read and saved straight away, priced as soon as
every leg has a fair price, and settled from its legs' results.
"""
import logging
import os
import uuid
from pathlib import Path

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..aw26.legs import classify, find_fixture_id
from ..aw26 import snapshots
from ..aw26.marry import marry_slip
from ..aw26.offers import OFFERS
from ..aw26.service import (
    build_slip, parse_when, price_slip, recent_fairs, settle_slip, summary,
)
from ..config import settings
from ..database import get_db
from ..images import prepare_for_ocr
from ..models import AwLeg, AwSlip
from ..schemas import AwSlipOut
from ..security import require_user
from ..time_utils import utcnow
from .upload import ALLOWED_MEDIA_TYPES, _read_capped

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/aw26", tags=["aw26"])

RESULTS = ("pending", "won", "lost", "void")


def _sweep_events(request: Request) -> list[dict]:
    """The Paddy sweep's current events — they carry OddsPapi's fixture ids,
    which is how a leg is settled. Read through its cache; never fatal."""
    cards = getattr(request.app.state, "cards_client", None)
    if cards is None or not hasattr(cards, "get_events"):
        return []
    try:
        return cards.get_events() or []
    except Exception as exc:
        logger.warning("aw26: Paddy events unavailable for fixture ids: %s", exc)
        return []


def _all(db) -> list[AwSlip]:
    return db.query(AwSlip).order_by(AwSlip.placed_at.desc(), AwSlip.id.desc()).all()


@router.get("/slips")
def list_slips(db: Session = Depends(get_db)):
    slips = _all(db)
    return {
        "summary": summary(slips, snapshot=snapshots.latest(db, "paddy")),
        "slips": [AwSlipOut.model_validate(s).model_dump(mode="json") for s in slips],
    }


@router.post("/slips", response_model=AwSlipOut)
async def upload_slip(
    request: Request,
    file: UploadFile = File(...),
    offer: str = Form("refund"),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    if offer not in OFFERS:
        raise HTTPException(status_code=400, detail=f"offer must be one of {', '.join(OFFERS)}")
    media_type = file.content_type
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {media_type}")
    image_bytes, media_type = prepare_for_ocr(await _read_capped(file), media_type)

    try:
        read = request.app.state.ocr_provider.read_slip(image_bytes, media_type)
    except Exception as exc:
        logger.error("aw26: slip read failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Could not read the slip: {exc}")
    if not read.legs:
        raise HTTPException(status_code=422, detail="No selections could be read off the photo")
    if read.receipt and db.query(AwSlip).filter(AwSlip.receipt == read.receipt).first():
        raise HTTPException(status_code=409, detail=f"Slip {read.receipt} is already in")

    upload_dir = Path(settings.upload_dir) / "aw26"
    upload_dir.mkdir(parents=True, exist_ok=True)
    image_path = str(upload_dir / f"{uuid.uuid4().hex}{ALLOWED_MEDIA_TYPES[media_type]}")
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    slip = build_slip(read, offer=offer, user=user, image_path=image_path,
                      events=_sweep_events(request))
    marry_slip(db, slip)
    price_slip(slip, recent_fairs(db))
    db.add(slip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        os.remove(image_path)
        raise HTTPException(status_code=409, detail=f"Slip {read.receipt} is already in")
    db.refresh(slip)
    return slip


class LegEdit(BaseModel):
    id: int | None = None                  # None = a leg the read missed
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
def edit_slip(slip_id: int, body: SlipEdit, request: Request, db: Session = Depends(get_db)):
    """A correction. Re-prices from scratch: a corrected read is not a later
    re-pricing, so the EV is recomputed off the same day's fairs."""
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
        events = None
        kept = []
        for i, e in enumerate(body.legs):
            leg = existing.get(e.id) if e.id is not None else None
            if leg is None:
                leg = AwLeg(kickoff=None)
            moved = leg.event != e.event
            leg.position, leg.event, leg.market, leg.selection = i, e.event, e.market, e.selection
            leg.odds, leg.result = e.odds, e.result
            leg.kind, leg.line, leg.side = classify(e.market, e.selection)
            # A fair typed in by hand stands; otherwise it is looked up again.
            leg.fair_odds = e.fair_odds
            if moved or leg.fixture_id is None:
                events = events if events is not None else _sweep_events(request)
                leg.fixture_id = find_fixture_id(leg, events)
            kept.append(leg)
        slip.legs[:] = kept
    slip.edited = True
    slip.ev = None
    marry_slip(db, slip, refresh=True)
    price_slip(slip, recent_fairs(db))
    settle_slip(slip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That receipt is already on another slip")
    db.refresh(slip)
    return slip


@router.delete("/slips/{slip_id}")
def delete_slip(slip_id: int, db: Session = Depends(get_db)):
    """The row and its photo go together (the cleanup rule)."""
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
    slip = db.get(AwSlip, slip_id)
    if slip is None or not slip.image_path or not os.path.exists(slip.image_path):
        raise HTTPException(status_code=404, detail="No photo")
    return FileResponse(slip.image_path)


@router.get("/results-check")
def results_check(request: Request, fixture: str):
    """What OddsPapi's results endpoints return for one finished fixture — read
    ONCE before the settlement reader is written against them. Two billable
    requests (/scores and /settlements); nothing is stored."""
    cards = getattr(request.app.state, "cards_client", None)
    client = getattr(cards, "_client", None)
    if client is None or not hasattr(client, "get"):
        return {"error": "OddsPapi is not configured — set ODDSPAPI_API_KEY."}
    out = {"fixture": fixture, "checked_at": utcnow().isoformat()}
    for path in ("/scores", "/settlements"):
        try:
            out[path.strip("/")] = client.get(path, fixtureId=fixture)
        except Exception as exc:
            out[path.strip("/") + "_error"] = str(exc)
    return out


# --- value snapshots: what was on offer (aw26/snapshots.py) -----------------


class SnapshotOut(BaseModel):
    id: int
    at: datetime
    source: str
    n_lines: int
    n_value: int
    best_edge: float | None = None
    sum_edge_positive: float

    model_config = {"from_attributes": True}


class ValueLineOut(BaseModel):
    event_id: str | None = None
    home: str
    away: str
    kickoff: datetime | None = None
    sport_key: str | None = None
    market: str
    team: str | None = None
    line: float | None = None
    side: str | None = None
    pick: str | None = None
    offered_odds: float
    fair_odds: float
    edge: float
    basis: str | None = None

    model_config = {"from_attributes": True}


class LatestSnapshotOut(BaseModel):
    at: datetime
    source: str
    lines: list[ValueLineOut]


class SnapshotsOut(BaseModel):
    snapshots: list[SnapshotOut]
    latest: LatestSnapshotOut | None = None


@router.get("/snapshots", response_model=SnapshotsOut)
def list_snapshots(hours: float = Query(default=24, gt=0, le=24 * snapshots.KEEP_DAYS),
                   source: str | None = Query(default=None),
                   db: Session = Depends(get_db)):
    """The last `hours` of snapshot headlines, and the newest snapshot's lines
    (best edge first). `source` narrows both (paddy | coupon | bb | market)."""
    recent = snapshots.recent(db, hours, source=source)
    newest = snapshots.latest(db, source)
    latest = None
    if newest is not None:
        lines = sorted(newest.lines, key=lambda ln: ln.edge, reverse=True)
        latest = LatestSnapshotOut(at=newest.at, source=newest.source,
                                   lines=[ValueLineOut.model_validate(ln) for ln in lines])
    return SnapshotsOut(snapshots=[SnapshotOut.model_validate(s) for s in recent], latest=latest)
