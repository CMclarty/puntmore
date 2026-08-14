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
from ..coupon_grid import (
    audit as audit_grid,
    consensus_blocks,
    drop_duplicate_fixtures,
    normalise_board_label,
    priced_blocks,
    reconcile,
    resolve_heading,
)
from ..coupon_images import delete_coupon_image
from ..coupon_types import COUPON_TYPES
from ..images import prepare_for_ocr
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


def _pair_for(row, ctype) -> tuple[float | None, float | None]:
    """The (home, away) prices this coupon TYPE takes from a transcribed row.

    A sheet prints several look-alike blocks side by side and OCR transcribes
    them all, keyed by heading. Choosing between them is a string match done
    HERE — the model is never asked to find one block and ignore the rest,
    which is a guess it can get wrong invisibly.

    The heading is resolved through `resolve_heading`, which tolerates a
    mistranscribed LETTER but never a mistranscribed digit — the six blocks on a
    Hill sheet differ from one another by one digit, so a fuzzy match loose
    enough to survive a typo is also loose enough to file a pair under the
    neighbouring market (see coupon_grid.headings_match).

    Falls back to the row's flat pair when the response carried a single block
    (an older shape, or a coupon photographed as one market) — but on a
    TWO-SIDED coupon only when that pair came in POSITIONALLY. A pair the model
    labelled itself ("home_odds"/"away_odds") is its judgement about which team
    each price belongs to, and a model asked that question is a model that can
    answer wrong; it did, and that is how away prices ended up filed against
    home teams. Refused here, so the leg goes unpriced instead.
    """
    if row.prices:
        key = resolve_heading(ctype.board_label, row.prices)
        # No entry for this type's heading = that block is not on this row.
        # Nothing is taken from a neighbouring block to stand in for it.
        return row.prices.get(key, (None, None)) if key else (None, None)
    if ctype.two_sided and not row.sides_positional:
        return None, None
    return row.home_odds, row.away_odds


async def _read_capped(file: UploadFile) -> bytes:
    """The upload's bytes, refusing anything over the size cap.

    `await file.read()` with no argument pulls the WHOLE body into memory
    whatever its size, which on a 512 MB instance is one large POST away from
    killing the process — and the body is only the start of it, since the image
    is then decoded and base64-encoded for the OCR call.

    Read in chunks and stop at the limit, so an oversized upload is refused
    having allocated the cap rather than the file. A phone photo is 2-6 MB; the
    default ceiling is several times that, because a resized upload is a worse
    outcome than a rejected one (OCR accuracy on small print depends on the full
    resolution).
    """
    cap = settings.max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413,
                detail=f"Image is larger than {settings.max_upload_mb} MB",
            )
        chunks.append(chunk)
    if not total:
        raise HTTPException(status_code=400, detail="Empty upload")
    return b"".join(chunks)


def solve_grid(extracted):
    """Reconcile a read against the rectangle the sheet is, and measure it.

    Called once per photo, BEFORE any legs are built, and it mutates `extracted`
    so every consumer downstream — this coupon, its siblings, the re-scan — sees
    the same repaired rows. Three passes, in this order:

      1. Drop repeated fixtures. One line per match is a property of the sheet,
         so a repeat is the read losing its place, never a coupon printing a
         match twice.
      2. Derive the sheet's column set from the rows themselves, then fold
         near-miss headings onto it. A single mistranscribed letter used to cost
         that row its whole block, silently, leaving the row priced one market
         short with nothing to say so.
      3. Count the cells: read, unreadable, missing.

    Returns the GridAudit. It does NOT change what gets priced — an unreadable
    cell still yields no leg, because an unpriced leg beats a wrongly priced one
    — it makes the loss countable, which it was not before.
    """
    extracted.rows, dupes = drop_duplicate_fixtures(extracted.rows)
    blocks = consensus_blocks(extracted.rows)
    repaired = reconcile(extracted.rows, blocks)
    scan = audit_grid(
        extracted.rows, blocks,
        malformed=getattr(extracted, "malformed_pairs", 0),
        duplicates=dupes,
    )
    if repaired:
        logger.info("Grid: repaired %d mistranscribed block heading(s)", repaired)
    log = logger.info if scan.clean else logger.warning
    log("Grid scan: %s", scan.summary())
    return scan


def _types_on_sheet(extracted, period: str) -> list:
    """Every coupon type whose block was transcribed off this photo.

    One sheet carries six markets, so one photo fills six lists. A type only
    qualifies if it runs the period being uploaded — the window is a property
    of the sheet, and a type that is not issued for it has no business being
    created from it.

    A block counts only once it has at least ONE readable price across the whole
    sheet. Since an unreadable block is now reported in place rather than
    omitted (the sheet is a rectangle — see coupon_grid), a column nobody could
    read anywhere would otherwise mint a coupon of fixtures with no odds, which
    reads as a market that priced nothing rather than as a photo that failed.
    """
    blocks = consensus_blocks(extracted.rows)
    seen = priced_blocks(extracted.rows, blocks)
    return [
        t for t in COUPON_TYPES
        if t.board_label
        and resolve_heading(t.board_label, seen)
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
                    # `row.market` is IGNORED here, deliberately. It is one
                    # value for the whole row, so using it gave BOTH legs of a
                    # two-sided fixture the same pick text with different odds —
                    # the away price filed under the home team's selection. The
                    # team is known from split_sides, which splits the fixture
                    # left-to-right exactly as the price pair is ordered, so on
                    # a two-sided grid it is the only thing that can name a side.
                    pick=_selection_for(ctype, team),
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


def _apply_coupon_window(coupon, extracted, ctype, period, reference, legs) -> list[str]:
    """Set the coupon's date window and payout ladder; report rejected rungs.

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
    #
    # But it is checked first. The ladder is what a combination is BET on, so a
    # single misread rung doesn't nudge a price — it invents value that isn't
    # there, on the one figure that matters (see coupon_types.sanitise_ladder).
    notes: list[str] = []
    if extracted.payout_ladder:
        ladder, notes = coupon_types.sanitise_ladder(
            extracted.payout_ladder, ctype.default_ladder
        )
    else:
        ladder = ctype.default_ladder
    if ladder and ctype.pays_ladder:
        coupon.payout_ladder = json.dumps({str(k): v for k, v in ladder.items()})
        if not extracted.payout_ladder:
            logger.warning(
                "Coupon %s: payout ladder unreadable, using the configured "
                "default. Verify it against the printed coupon.", coupon.id,
            )
        for note in notes:
            logger.warning("Coupon %s: ladder rung rejected — %s", coupon.id, note)
    return notes


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

    # Normalise before anything else: apply the EXIF orientation tag to the
    # pixels (phone cameras record rotation as metadata, so a portrait shot
    # otherwise reaches the OCR provider sideways) and cap the long edge at the
    # vision API's own maximum resolution. What is STORED is what is sent, so a
    # later re-scan reads the same image and costs the same nothing.
    image_bytes, media_type = prepare_for_ocr(await _read_capped(file), media_type)

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

        # Does the sheet say it is the coupon that was selected? Checked BEFORE
        # anything is built from it: a wrong-type upload produces a coupon that
        # looks entirely normal — real fixtures, real prices — priced against
        # another coupon's ladder, which is how an 8000/1 rung reached a board
        # paying 300/1. Nothing else downstream can tell.
        wrong_sheet = coupon_types.title_mismatch(ctype, extracted.title)
        if wrong_sheet:
            logger.error("Coupon %s: %s — not saved", coupon.id, wrong_sheet)
            coupon.status = "failed"
            coupon.scan_audit = json.dumps({
                "clean": False,
                "summary": f"Wrong sheet for this coupon type — {wrong_sheet}",
                "title_mismatch": wrong_sheet,
            })
            db.commit()
            return

        scan = solve_grid(extracted)
        legs = _build_legs(coupon.id, extracted, ctype, reference)
        db.add_all(legs)
        db.flush()
        ladder_notes = _apply_coupon_window(coupon, extracted, ctype, period, reference, legs)
        # The ladder is read off the same photo as the grid, so a rejected
        # rung belongs in the same verdict — it is the sheet's headline figure.
        audit = scan.as_dict()
        if ladder_notes:
            audit["ladder_notes"] = ladder_notes
            audit["clean"] = False
            audit["summary"] += f", {len(ladder_notes)} bad ladder rung(s)"
        coupon.scan_audit = json.dumps(audit)
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

    # Same header check as the upload. A re-scan reads the stored image, so if
    # the wrong sheet was filed under this type it is still the wrong sheet —
    # and re-scanning must not be a way to launder it past the check.
    wrong_sheet = coupon_types.title_mismatch(ctype, extracted.title)
    if wrong_sheet:
        raise HTTPException(status_code=409, detail=wrong_sheet.capitalize())

    # A re-scan is a fresh read of the same sheet, so it gets a fresh verdict —
    # replacing the old one, which described a scan that no longer stands.
    coupon.scan_audit = json.dumps(solve_grid(extracted).as_dict())

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
