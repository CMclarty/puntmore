import os
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Coupon, Leg
from ..schemas import UploadInitResponse, ExtractedRowOut, ConfirmRequest
from ..config import settings
from ..images import normalise_orientation
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
        status="pending",
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

    db.commit()

    notifier = getattr(request.app.state, "notifier", None)
    if notifier:
        notifier.send(
            f":inbox_tray: New upload: **{coupon_name}** — "
            f"{len(extracted)} rows extracted. Awaiting review."
        )

    return UploadInitResponse(
        coupon_id=coupon.id,
        rows=[
            ExtractedRowOut(match=r.match, pick=r.market, odds=r.your_odds)
            for r in extracted
        ],
    )


@router.post("/{coupon_id}/confirm")
def confirm_upload(
    coupon_id: int,
    body: ConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    coupon = db.get(Coupon, coupon_id)
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")

    db.query(Leg).filter(Leg.coupon_id == coupon_id).delete()

    for rank, row in enumerate(body.rows):
        db.add(Leg(
            coupon_id=coupon_id,
            match_name=row.match,
            pick=row.pick,
            odds=row.odds,
            rank=rank,
        ))

    coupon.status = "live"
    db.commit()

    notifier = getattr(request.app.state, "notifier", None)
    if notifier:
        notifier.send(
            f":white_check_mark: **{coupon.name}** confirmed — "
            f"{len(body.rows)} legs saved."
        )

    return {"coupon_id": coupon_id, "legs_saved": len(body.rows)}
