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
from ..coupon_images import delete_coupon_image
from ..coupon_types import COUPON_TYPES
from ..images import normalise_orientation
from ..pricing.legs import price_legs
from ..providers.base import OcrHint
from ..providers.claude_ocr import normalise_board_label
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


def _pair_for(row, ctype) -> tuple[float | None, float | None]:
    """The (home, away) prices this coupon TYPE takes from a transcribed row.

    A sheet prints several look-alike blocks side by side and OCR transcribes
    them all, keyed by heading. Choosing between them is a string match done
    HERE — the model is never asked to find one block and ignore the rest,
    which is a guess it can get wrong invisibly.

    Falls back to the row's flat pair when the response carried a single block
    (an older shape, or a coupon photographed as one market).
    """
    if row.prices:
        key = normalise_board_label(ctype.board_label)
        # No entry for this type's heading = that block is not on this sheet.
        # Nothing is taken from a neighbouring block to stand in for it.
        return row.prices.get(key, (None, None))
    return row.home_odds, row.away_odds


def _types_on_sheet(extracted, period: str) -> list:
    """Every coupon type whose block was transcribed off this photo.

    One sheet carries six markets, so one photo fills six lists. A type only
    qualifies if it runs the period being uploaded — the window is a property
    of the sheet, and a type that is not issued for it has no business being
    created from it.
    """
    seen = set()
    for row in extracted.rows:
        seen.update(row.prices)
    return [
        t for t in COUPON_TYPES
        if t.board_label
        and normalise_board_label(t.board_label) in seen
        and period in t.period_codes
    ]


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
        home_odds, away_odds = _pair_for(row, ctype) if ctype.two_sided else (None, None)

        if ctype.two_sided and sides and (home_odds or away_odds):
            home, away = sides
            for team, odds in ((home, home_odds), (away, away_odds)):
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


def _supersede_previous(db, coupon) -> int:
    """Drop earlier uploads of the same (type, period) — the newest wins.

    Two issues of one type coexist by design (a GG midweek and a GG weekend are
    two different bets), so the key is type AND period: a re-upload of the
    weekend issue replaces the weekend issue and leaves midweek alone.

    Called only after the new coupon has read rows. A failed or empty scan must
    never take the working coupon with it — an old issue beats no issue, and
    the replacement can be retried.

    Deletes the image file alongside the row, never one without the other
    (same rule as the scheduled cleanup).
    """
    if coupon.period is None:
        return 0
    old = (
        db.query(Coupon)
        .filter(
            Coupon.type_code == coupon.type_code,
            Coupon.period == coupon.period,
            Coupon.id != coupon.id,
        )
        .all()
    )
    for prev in old:
        delete_coupon_image(db, prev)
        db.delete(prev)
    if old:
        logger.info(
            "Coupon %s (%s %s) superseded %s",
            coupon.id, coupon.type_code, coupon.period,
            ", ".join(str(c.id) for c in old),
        )
    return len(old)


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


def _build_sibling_coupons(
    db, coupon, extracted, period, reference, odds_provider,
) -> list[Coupon]:
    """A coupon for every OTHER market block transcribed off this sheet.

    A Hill sheet prints six markets over one fixture list, and the photo carries
    all of them. The uploaded type is the one that was asked for; the rest are
    built from the same read, each with its own legs, window and name, each
    superseding its own previous issue.

    They SHARE the stored image, so any of them can be re-scanned and the file
    survives until the last one is deleted (see delete_coupon_image).

    A block that produced no legs is skipped rather than saved empty: an empty
    coupon on the landing page reads as a failed scan of a real coupon.
    """
    made = []
    for sibling_type in _types_on_sheet(extracted, period):
        if sibling_type.code == coupon.type_code:
            continue
        legs = _build_legs(0, extracted, sibling_type, reference)
        if not legs:
            continue

        sibling = Coupon(
            type_code=sibling_type.code,
            period=period,
            match_day=coupon.match_day,
            period_ends=coupon.period_ends,
            status="confirmed",
            image_path=coupon.image_path,
            uploaded_by=coupon.uploaded_by,
        )
        db.add(sibling)
        db.flush()
        for leg in legs:
            leg.coupon_id = sibling.id
        db.add_all(legs)
        db.flush()
        _apply_coupon_window(sibling, extracted, sibling_type, period, reference, legs)
        sibling.name = _coupon_name(sibling_type, period, sibling)

        price_legs(legs, odds_provider)
        _supersede_previous(db, sibling)
        db.commit()
        made.append(sibling)
        logger.info(
            "Sheet %s also filled %s (%d legs)",
            coupon.id, sibling_type.code, len(legs),
        )
    return made


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
        # Only now, with rows read and priced, does this upload replace the
        # previous issue of the same window — see _supersede_previous.
        replaced = _supersede_previous(db, coupon) if legs else 0
        db.commit()

        # One sheet, every market on it. The photo was transcribed whole, so the
        # other blocks are already read — building their coupons costs nothing
        # more at the vendor and saves photographing the same sheet six times.
        siblings = _build_sibling_coupons(
            db, coupon, extracted, period, reference, odds_provider,
        )

        if notifier:
            replaced_note = (
                f" Replaced the previous {coupon.period_label or coupon.period} upload."
                if replaced else ""
            )
            sibling_note = (
                f" Same sheet also filled {', '.join(s.name for s in siblings)}."
                if siblings else ""
            )
            notifier.send(
                f":inbox_tray: New upload: **{coupon.name}** — "
                f"{len(legs)} selections saved and priced."
                f"{replaced_note}{sibling_note}"
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
