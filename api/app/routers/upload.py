import os
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Coupon, Leg
from ..schemas import UploadInitResponse
from ..config import settings
from ..images import normalise_orientation
from ..pricing.legs import price_legs
from ..security import require_user

router = APIRouter(prefix="/upload", tags=["upload"])

ALLOWED_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


@router.post("", response_model=UploadInitResponse)
async def upload_coupon(
    request: Request,
    type_code: str = Form(...),
    match_day: date = Form(...),
    name: str = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    media_type = file.content_type
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {media_type}")

    # Apply the EXIF orientation tag to the pixels before anything else — phone
    # cameras record rotation as metadata, so a portrait shot otherwise reaches
    # the OCR provider sideways. The stored image is normalised too, so a later
    # re-scan reads the same orientation. Never resize or recompress here: OCR
    # accuracy on small print depends on the full resolution.
    image_bytes = normalise_orientation(await file.read(), media_type)

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = ALLOWED_MEDIA_TYPES[media_type]
    filename = f"{uuid.uuid4().hex}{ext}"
    image_path = str(upload_dir / filename)
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    coupon_name = name or f"{type_code} · {match_day.strftime('%a %d %b')}"
    coupon = Coupon(
        name=coupon_name,
        type_code=type_code,
        match_day=match_day,
        status="processing",
        image_path=image_path,
        uploaded_by=user,
    )
    db.add(coupon)
    db.flush()

    ocr = request.app.state.ocr_provider
    try:
        extracted = ocr.extract(image_bytes, media_type)
    except Exception:
        db.rollback()
        if os.path.exists(image_path):
            os.remove(image_path)
        raise HTTPException(status_code=502, detail="OCR extraction failed")

    # Auto-confirm: rows persist and price immediately (SPEC §7). There is no
    # blocking review step — anomalies surface as per-leg ⚠ flags that the
    # syndicate can correct inline afterwards.
    legs = [
        Leg(
            coupon_id=coupon.id,
            match_name=row.match,
            pick=row.market,
            odds=row.your_odds,
            rank=rank,
        )
        for rank, row in enumerate(extracted)
    ]
    db.add_all(legs)
    db.flush()

    price_legs(legs, request.app.state.odds_provider)
    coupon.status = "confirmed"
    db.commit()

    flagged = sum(1 for leg in legs if leg.flags)
    notifier = getattr(request.app.state, "notifier", None)
    if notifier:
        suffix = f", {flagged} flagged for review" if flagged else ""
        notifier.send(
            f":inbox_tray: New upload: **{coupon_name}** — "
            f"{len(legs)} legs saved and priced{suffix}."
        )

    return UploadInitResponse(
        coupon_id=coupon.id,
        legs=sorted(legs, key=lambda l: l.rank),
    )


@router.post("/{coupon_id}/rescan", response_model=UploadInitResponse)
def rescan_coupon(
    coupon_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Re-run OCR over the stored image, replacing only UNEDITED legs.

    A human correction always outranks a machine re-read, so edited rows are
    carried through untouched and the fresh OCR rows are slotted around them by
    rank. Re-scanning is the escape hatch for a bad first read, not a reset.
    """
    coupon = db.get(Coupon, coupon_id)
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    if not coupon.image_path or not os.path.exists(coupon.image_path):
        raise HTTPException(status_code=409, detail="Source image is no longer stored")

    ext = Path(coupon.image_path).suffix.lower()
    media_type = next(
        (mt for mt, e in ALLOWED_MEDIA_TYPES.items() if e == ext), "image/jpeg"
    )
    with open(coupon.image_path, "rb") as f:
        image_bytes = f.read()

    ocr = request.app.state.ocr_provider
    try:
        extracted = ocr.extract(image_bytes, media_type)
    except Exception:
        raise HTTPException(status_code=502, detail="OCR extraction failed")

    existing = db.query(Leg).filter(Leg.coupon_id == coupon_id).order_by(Leg.rank).all()
    kept = [leg for leg in existing if leg.edited]
    kept_ranks = {leg.rank for leg in kept}

    for leg in existing:
        if not leg.edited:
            db.delete(leg)
    db.flush()

    # OCR rows are positional — row i is the i-th line of the coupon. A rank a
    # human has already corrected is skipped outright rather than re-added
    # alongside the edit, which would duplicate that leg.
    fresh = [
        Leg(
            coupon_id=coupon_id,
            match_name=row.match,
            pick=row.market,
            odds=row.your_odds,
            rank=rank,
        )
        for rank, row in enumerate(extracted)
        if rank not in kept_ranks
    ]
    db.add_all(fresh)
    db.flush()

    price_legs(fresh + kept, request.app.state.odds_provider)
    db.commit()

    notifier = getattr(request.app.state, "notifier", None)
    if notifier:
        notifier.send(
            f":arrows_counterclockwise: **{coupon.name}** re-scanned — "
            f"{len(fresh)} row(s) re-read, {len(kept)} edited row(s) preserved."
        )

    return UploadInitResponse(
        coupon_id=coupon_id,
        legs=sorted(fresh + kept, key=lambda l: l.rank),
    )
