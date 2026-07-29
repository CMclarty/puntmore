import json
import logging
import os
import uuid
from datetime import date
from pathlib import Path

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request,
    UploadFile,
)
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import Coupon, Leg
from ..schemas import UploadInitResponse
from ..config import settings
from .. import coupon_types, coupon_periods, normalise
from ..images import normalise_orientation
from ..pricing.legs import price_legs
from ..providers.base import OcrHint
from ..security import require_user

logger = logging.getLogger("uvicorn.error")


def _selection_for(ctype, team: str | None) -> str:
    """This coupon's selection text, with the team filled in when it names one.

    The template is "{team} to Score 1"; a coupon whose market applies to the
    whole fixture (GG) has no placeholder and formats to itself.
    """
    if team is None or "{team}" not in ctype.selection:
        return ctype.selection
    return ctype.selection.format(team=team)


def _build_legs(coupon_id, extracted, ctype, reference: date) -> list[Leg]:
    """Turn a read coupon into legs.

    Two things OCR is deliberately NOT asked to supply are filled in here:
    the market (known from WHICH coupon was photographed, so it is never
    guessed from the header) and the kickoff date (coupons print a weekday
    only, resolved forward from the upload).

    A TWO-SIDED priced coupon prints an H and an A price per fixture, and each
    is its own bet ("Kairat to Score 1" and "Omonia Nicosia to Score 1"), so
    such a row yields TWO legs. Value can be on either side, and collapsing
    them to one would discard half the coupon. A side whose price was
    unreadable is skipped rather than stored priceless — on a priced coupon a
    leg with no odds has nothing to value.
    """
    legs = []
    rank = 0
    for row in extracted.rows:
        kickoff = coupon_periods.parse_kickoff(row.kickoff_text, reference)
        sides = normalise.split_sides(row.match) if ctype.two_sided else None

        if ctype.two_sided and sides and (row.home_odds or row.away_odds):
            home, away = sides
            for team, odds in ((home, row.home_odds), (away, row.away_odds)):
                if odds is None:
                    continue
                legs.append(Leg(
                    coupon_id=coupon_id,
                    match_name=row.match,
                    pick=row.market or _selection_for(ctype, team),
                    odds=odds,
                    kickoff=kickoff,
                    rank=rank,
                ))
                rank += 1
            continue

        legs.append(Leg(
            coupon_id=coupon_id,
            match_name=row.match,
            pick=row.market or _selection_for(ctype, None),
            # Only priced coupons carry a per-fixture price; a ladder coupon
            # prints none and any number here would be invented.
            odds=row.your_odds if ctype.prices_each_selection else None,
            kickoff=kickoff,
            rank=rank,
        ))
        rank += 1
    return legs


def _apply_coupon_window(coupon, extracted, ctype, period, reference, legs) -> None:
    """Set the coupon's date window and payout ladder.

    A variable window (GG midweek/weekend) has no schedule to derive from, so
    it is taken from the fixtures the coupon actually lists. A fixed window
    resolves from the calendar as before.
    """
    window = coupon_periods.date_range(period, reference)
    if window is None:
        kickoffs = [leg.kickoff.date() for leg in legs if leg.kickoff]
        window = coupon_periods.range_from_kickoffs(kickoffs)
    if window:
        coupon.match_day, coupon.period_ends = window

    # The coupon's OWN ladder wins: it is printed on each issue and can change.
    # Config is only a floor so a bad scan does not leave it unvaluable.
    ladder = extracted.payout_ladder or ctype.default_ladder
    if ladder and ctype.pays_ladder:
        coupon.payout_ladder = json.dumps({str(k): v for k, v in ladder.items()})
        if not extracted.payout_ladder:
            logger.warning(
                "Coupon %s: payout ladder unreadable, using the configured "
                "default. Verify it against the printed coupon.", coupon.id,
            )


def _hint_for(ctype) -> OcrHint:
    """What to tell OCR about the sheet being read. The type is chosen on the
    upload form, so which market block to read is known rather than guessed."""
    return OcrHint(
        priced=ctype.prices_each_selection,
        two_sided=ctype.two_sided,
        board_label=ctype.board_label,
    )


def _coupon_name(ctype, period, coupon) -> str:
    label = coupon_periods.get(period).label
    if coupon.match_day and coupon.period_ends and coupon.match_day != coupon.period_ends:
        span = f"{coupon.match_day:%d %b}–{coupon.period_ends:%d %b}"
    elif coupon.match_day:
        span = f"{coupon.match_day:%d %b}"
    else:
        span = ""
    return f"{ctype.label} · {label} {span}".strip()

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
    background: BackgroundTasks,
    type_code: str = Form(...),
    period: str = Form(...),
    uploaded_on: date = Form(None),
    name: str = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    if not coupon_types.is_valid(type_code):
        raise HTTPException(
            status_code=400, detail=f"Unknown coupon type: {type_code}"
        )
    ctype = coupon_types.get(type_code)
    if period not in ctype.period_codes:
        raise HTTPException(
            status_code=400,
            detail=f"{ctype.label} does not run a {period} period",
        )

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

    # Weekdays printed on the coupon ("MON 17:00") resolve forward from here.
    reference = uploaded_on or date.today()

    coupon = Coupon(
        type_code=type_code,
        period=period,
        # Provisional: replaced below once the fixtures are known. Fixed
        # windows resolve from the schedule; variable ones cannot be known
        # until the coupon has been read.
        match_day=reference,
        status="processing",
        image_path=image_path,
        uploaded_by=user,
    )
    # Provisional name so the row is never nameless in the list while it reads.
    # The real window is only known once the fixtures are, so the background
    # task recomputes it below unless the user supplied a name of their own.
    coupon.name = name or _coupon_name(ctype, period, coupon)
    db.add(coupon)
    db.commit()
    db.refresh(coupon)

    # Reading a coupon takes ~a minute: a dense sheet is 78 fixtures and the OCR
    # emits thousands of tokens of JSON for it. Holding the HTTP request open for
    # that long does not survive a phone — iOS Safari abandons the fetch and the
    # upload reads as "Load failed" even though the server goes on to finish it,
    # which invites a duplicate upload of a coupon that actually saved.
    #
    # So the request returns NOW, with the coupon saved and status "processing".
    # The read happens after the response, and the client polls the coupon until
    # it turns "confirmed" (or "failed"). Navigating away, backgrounding the tab
    # or losing signal no longer costs anything — the work is already server-side.
    background.add_task(
        _process_upload,
        coupon_id=coupon.id,
        type_code=type_code,
        period=period,
        reference=reference,
        media_type=media_type,
        ocr=request.app.state.ocr_provider,
        odds_provider=request.app.state.odds_provider,
        user_name=name,
        notifier=getattr(request.app.state, "notifier", None),
        # The request's session is closed by the time the task runs, so it opens
        # its own. Taken from app.state so a test can point it at the same
        # engine its fixtures use — otherwise this writes to the real database.
        session_factory=getattr(request.app.state, "session_factory", SessionLocal),
    )

    # No legs yet — they exist only once the read finishes. The client polls.
    return UploadInitResponse(coupon_id=coupon.id, legs=[])


def _process_upload(
    *, coupon_id, type_code, period, reference, media_type,
    ocr, odds_provider, notifier, user_name=None, session_factory=None,
) -> None:
    """Read and price an already-saved coupon. Runs AFTER the response.

    Owns its own session: the request's is closed by the time this runs. Every
    exit leaves the coupon in a terminal status, because a row stuck on
    "processing" is indistinguishable in the UI from one still being read.
    """
    db = (session_factory or SessionLocal)()
    try:
        coupon = db.get(Coupon, coupon_id)
        if coupon is None:
            return
        ctype = coupon_types.get(type_code)
        try:
            with open(coupon.image_path, "rb") as f:
                image_bytes = f.read()
            extracted = ocr.extract(image_bytes, media_type, _hint_for(ctype))
        except Exception:
            # Log the cause: a bare 502 hid a plain AttributeError for a day.
            logger.exception("OCR extraction failed (%s)", type(ocr).__name__)
            coupon.status = "failed"
            db.commit()
            return

        legs = _build_legs(coupon.id, extracted, ctype, reference)
        db.add_all(legs)
        db.flush()
        _apply_coupon_window(coupon, extracted, ctype, period, reference, legs)
        # Now the window is real, so the auto-name can be. A name the user typed
        # is theirs and is left alone.
        if not user_name:
            coupon.name = _coupon_name(ctype, period, coupon)

        price_legs(legs, odds_provider)
        coupon.status = "confirmed"
        db.commit()

        if notifier:
            notifier.send(
                f":inbox_tray: New upload: **{coupon.name}** — "
                f"{len(legs)} selections saved and priced."
            )
    except Exception:
        logger.exception("Coupon %s failed after upload", coupon_id)
        try:
            coupon = db.get(Coupon, coupon_id)
            if coupon is not None:
                coupon.status = "failed"
                db.commit()
        except Exception:
            logger.exception("Could not mark coupon %s failed", coupon_id)
    finally:
        db.close()


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

    ctype = coupon_types.get(coupon.type_code)
    ocr = request.app.state.ocr_provider
    try:
        extracted = ocr.extract(image_bytes, media_type, _hint_for(ctype))
    except Exception:
        logger.exception("OCR re-scan failed (%s)", type(ocr).__name__)
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
    reference = coupon.match_day or date.today()
    fresh = [
        leg for rank, leg in enumerate(
            _build_legs(coupon_id, extracted, ctype, reference)
        )
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
