import json
import math

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func
from sqlalchemy.orm import Session, selectinload
from ..database import get_db
from ..models import Coupon, Leg
from datetime import date, timedelta

from .. import coupon_types, coupon_periods
from ..coupon_periods import COUPON_PERIODS
from ..coupon_types import COUPON_TYPES
from ..schemas import (
    BundleOut, CouponOut, CouponTypeOut, CouponPeriodOut, LegOut,
    LegEditRequest, TypePeriodOut, UploadOptionOut,
)
from ..config import settings
from ..pricing.legs import price_legs
from ..time_utils import utcnow
from ..security import require_user

router = APIRouter(prefix="/coupons", tags=["coupons"])


@router.get("/periods", response_model=list[CouponPeriodOut])
def list_coupon_periods(type_code: str | None = None):
    """The windows a coupon type runs, with the dates they resolve to.

    Filtered by type because the two shapes differ: the priced coupons run a
    fixed weekly schedule, while GG issues are midweek/weekend on dates that
    move and are only known once the coupon has been read.
    """
    today = date.today()
    if type_code is not None:
        # A sheet code is accepted too: the upload form offers sheets.
        ctype, _ = coupon_types.resolve_upload(type_code)
        if ctype is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown coupon type: {type_code}"
            )
        codes = ctype.period_codes
    else:
        codes = tuple(p.code for p in COUPON_PERIODS)

    variable = any(coupon_periods.get(c).variable for c in codes)
    suggested = (
        coupon_periods.suggest_variable(today) if variable
        else coupon_periods.suggest(today)
    )

    out = []
    for code in codes:
        p = coupon_periods.get(code)
        window = coupon_periods.date_range(code, today)
        out.append(CouponPeriodOut(
            code=p.code, label=p.label,
            starts_on=window[0] if window else None,
            ends_on=window[1] if window else None,
            variable=p.variable,
            suggested=(p.code == suggested),
        ))
    return out


def _issue_status(coupon: Coupon, legs: int, ahead: int, undated: int) -> str:
    """Where an issue stands — decided by its FIXTURES, not by its window.

    The printed window is what the coupon claims; the fixtures are what is left
    to bet, and only the second makes a coupon live. A Betfred weekend GG issue
    prints 7–10 August, so a pure window test called it live all day Monday with
    every match already played — which is what this replaces.

      active    at least one fixture still to kick off
      upcoming  nothing has gone and the window hasn't started
      past      nothing left to bet

    Three states, and no more. There was briefly a fourth for "has selections
    but none of them has a kickoff time", surfaced on the landing page as a
    badge. It was not asked for, it put internal plumbing on screen, and it is
    what filled that page with dead coupons — so a fixture with no kickoff now
    simply isn't a fixture still to come, which is what the rule says on its
    face. `undated` stays as a COUNT for anything that wants it; it decides
    nothing.

    The window is still what separates upcoming from past when there are no
    selections to reason from at all.
    """
    if ahead:
        return "upcoming" if coupon.period_status == "upcoming" else "active"
    if legs:
        # It HAS selections and not one of them is still to come. Finished, and
        # the window does not get a vote — deferring to it here is precisely the
        # bug: a Fri–Mon window reads "active" all Monday with nothing to bet.
        return "past"
    # No selections at all: nothing to reason from, so the window is all there
    # is. A coupon still being read, or one whose scan came back empty.
    return coupon.period_status or "past"


def _scan_note(coupon: Coupon) -> str | None:
    """One line about this coupon's read, or None when there's nothing to say.

    Only surfaced when the grid came back INCOMPLETE. A clean scan needs no
    announcement, and an unmeasured one (a coupon read before the check
    existed) must not be reported as clean — silence covers both, and the two
    are told apart in the database by NULL, not here.
    """
    if not coupon.scan_audit:
        return None
    try:
        scan = json.loads(coupon.scan_audit)
    except (ValueError, TypeError):
        return None
    return None if scan.get("clean") else scan.get("summary")


def _best_for_coupon(db: Session, coupon: Coupon, ctype) -> dict | None:
    """The best ladder rung available on ONE issue.

    The ladder and the fixture list both belong to an issue, so the fair odds
    fed in are only that coupon's — mixing two issues would price a fold whose
    legs are spread across two different coupons and could never be struck.
    """
    fairs = [
        row[0] for row in db.query(Leg.fair_odds)
        .filter(Leg.coupon_id == coupon.id, Leg.fair_odds.isnot(None))
        .all()
    ]
    return coupon_types.best_combination(
        fairs,
        coupon.ladder or ctype.default_ladder,
        # The fold this type is actually bet at, when it has one.
        picks=ctype.display_picks,
    )


def _bundles_for_coupon(db: Session, coupon: Coupon) -> list[dict]:
    """Every ready-made acca on ONE issue, priced and ranked.

    Per issue for the same reason a ladder combination is: a bundle's legs are
    struck on one sheet, so pricing one across two uploads would describe a bet
    nobody could place.
    """
    legs = (
        db.query(Leg)
        .filter(Leg.coupon_id == coupon.id, Leg.bundle.isnot(None))
        .order_by(Leg.rank)
        .all()
    )
    return coupon_types.bundles_on(legs)


def _best_bundle_for_coupon(db: Session, coupon: Coupon) -> dict | None:
    """The single best-value bundle on an issue, for the landing page.

    Drops the legs — the landing page wants the headline figure, and the legs
    are several hundred bytes of JSON per bundle that nothing there reads.
    """
    found = _bundles_for_coupon(db, coupon)
    if not found or found[0]["value"] is None:
        return None
    return {k: v for k, v in found[0].items() if k != "legs"}


@router.get("/upload-options", response_model=list[UploadOptionOut])
def list_upload_options():
    """What can be photographed: the two multi-market sheets, and the types
    printed on a sheet of their own. The upload form's one dropdown."""
    return [
        {"code": code, "label": label, "period_codes": list(periods),
         "types": list(coupon_types.SHEET_BY_CODE[code].types)
                  if code in coupon_types.SHEET_BY_CODE else [code]}
        for code, label, periods in coupon_types.upload_options()
    ]


@router.get("/types", response_model=list[CouponTypeOut])
def list_coupon_types(db: Session = Depends(get_db)):
    """The coupon types — the app's top-level entities.

    Each is its own filtered list of fixtures, not an accumulator. Served from
    one place so the tracker dropdown and the upload form cannot drift.
    """
    counts = dict(
        db.query(Coupon.type_code, func.count(Leg.id))
        .join(Leg, Leg.coupon_id == Coupon.id)
        .group_by(Coupon.type_code)
        .all()
    )

    # Per-issue rollup for the landing page. One query for the whole board
    # rather than one per type, then grouped in Python — the row count here is
    # the number of COUPONS, which is small.
    # Still bettable = kickoff not yet past, on the SAME grace the cleanup uses
    # (scheduler.cleanup_kicked_off_legs), so a row stays live for exactly as
    # long as it stays on the coupon. Two definitions here would show a fixture
    # as gone while its row was still listed, or the reverse.
    live_from = utcnow() - timedelta(minutes=settings.leg_kickoff_grace_minutes)
    per_coupon = (
        db.query(
            Coupon,
            func.count(Leg.id).label("legs"),
            func.count(Leg.fair_odds).label("priced"),
            func.count(case((Leg.kickoff >= live_from, 1))).label("ahead"),
            # `Leg.id.isnot(None)` is load-bearing. This is an OUTER join, so a
            # coupon with NO legs still produces one row with every Leg column
            # NULL — and "kickoff IS NULL" is then TRUE, counting a leg that
            # does not exist. Every empty coupon ever uploaded was classified
            # "undated" and pinned to the landing page for ever because of it.
            func.count(
                case(((Leg.id.isnot(None)) & (Leg.kickoff.is_(None)), 1))
            ).label("undated"),
            func.min(case((Leg.kickoff >= live_from, Leg.kickoff))).label("next_ko"),
        )
        .outerjoin(Leg, Leg.coupon_id == Coupon.id)
        .group_by(Coupon.id)
        .all()
    )
    periods_by_type: dict[str, list[TypePeriodOut]] = {}
    for coupon, legs, priced, ahead, undated, next_ko in per_coupon:
        status = _issue_status(coupon, legs, ahead, undated)
        # Nothing left to bet — the landing page is about what is live or still
        # to come, and a finished issue is history whatever its window says.
        if status == "past":
            continue
        ctype = coupon_types.get(coupon.type_code)
        periods_by_type.setdefault(coupon.type_code, []).append(TypePeriodOut(
            coupon_id=coupon.id,
            period=coupon.period,
            label=coupon.period_label,
            status=status,
            match_day=coupon.match_day,
            period_ends=coupon.period_ends,
            leg_count=legs,
            priced_count=priced,
            live_count=ahead,
            undated_count=undated,
            next_kickoff=next_ko,
            scan_note=_scan_note(coupon),
            # Each issue's own combination — see the schema. Computed here so a
            # midweek issue's value stays readable while a weekend one is up.
            best_combination=(
                _best_for_coupon(db, coupon, ctype)
                if ctype is not None and ctype.pays_ladder else None
            ),
            best_bundle=(
                _best_bundle_for_coupon(db, coupon)
                if ctype is not None and ctype.bundles else None
            ),
        ))
    for rows in periods_by_type.values():
        # Soonest window first, so the set closest to going off leads.
        rows.sort(key=lambda r: (r.match_day, r.coupon_id))

    out = []
    for t in COUPON_TYPES:
        best = None
        best_bundle = None
        if t.bundles:
            latest = (
                db.query(Coupon)
                .filter(Coupon.type_code == t.code)
                .order_by(Coupon.match_day.desc(), Coupon.created_at.desc())
                .first()
            )
            if latest is not None:
                best_bundle = _best_bundle_for_coupon(db, latest)
        if t.pays_ladder:
            # Keyed on pays_ladder, NOT on "prints no prices": a Hills coupon
            # does both, and gating this on the absence of prices would hide the
            # combination value on the one coupon that offers both routes.
            # Read off the MOST RECENT coupon of this type: the ladder and the
            # fixture list both belong to an issue, not to the type.
            latest = (
                db.query(Coupon)
                .filter(Coupon.type_code == t.code)
                .order_by(Coupon.match_day.desc(), Coupon.created_at.desc())
                .first()
            )
            if latest is not None:
                best = _best_for_coupon(db, latest, t)
        out.append(CouponTypeOut(
            code=t.code,
            label=t.label,
            max_fair_odds=t.max_fair_odds,
            selection_count=counts.get(t.code, 0),
            prices_each_selection=t.prices_each_selection,
            pays_ladder=t.pays_ladder,
            bundles=t.bundles,
            period_codes=list(t.period_codes),
            periods=periods_by_type.get(t.code, []),
            best_combination=best,
            best_bundle=best_bundle,
        ))
    return out


@router.get("/types/{type_code}/selections", response_model=list[LegOut])
def list_type_selections(
    type_code: str,
    coupon_id: int | None = None,
    db: Session = Depends(get_db),
):
    """One coupon type's filtered fixture list, ranked best-value first.

    Spans every upload of this type by default — the type is the entity, an
    upload is just how selections got into it. `coupon_id` narrows it to ONE
    issue, which is what makes a second live issue reachable: without it the
    two are one ranked list and the newer one sits below every row of the live
    one.
    """
    coupon_type = coupon_types.get(type_code)
    if coupon_type is None:
        raise HTTPException(status_code=404, detail=f"Unknown coupon type: {type_code}")

    query = (
        db.query(Leg)
        .join(Coupon, Leg.coupon_id == Coupon.id)
        .filter(Coupon.type_code == type_code)
    )
    if coupon_id is not None:
        # 404 rather than an empty list: an issue that belongs to another type
        # (or no longer exists — it may have been superseded) is a stale link,
        # and the page needs to know to go back rather than show "no fixtures".
        issue = db.get(Coupon, coupon_id)
        if issue is None or issue.type_code != type_code:
            raise HTTPException(
                status_code=404, detail=f"No {type_code} coupon with id {coupon_id}"
            )
        query = query.filter(Leg.coupon_id == coupon_id)
    selections = query.all()

    # A type's own filter (GG Bonus keeps only fixtures priced <= 1.80). Rows
    # that never priced are kept regardless — an unpriced row is something to
    # look at, not something to hide.
    if coupon_type.max_fair_odds is not None:
        selections = [
            s for s in selections
            if s.fair_odds is None or s.fair_odds <= coupon_type.max_fair_odds
        ]

    if coupon_type.max_fair_odds is not None:
        # Screens rank by shortest price — likeliest first, which is the point
        # of the screen — rather than by value.
        return sorted(
            selections, key=lambda s: (s.fair_odds is None, s.fair_odds or 0)
        )
    return sorted(selections, key=lambda s: (s.value is None, -(s.value or 0)))


@router.get("/types/{type_code}/bundles", response_model=list[BundleOut])
def list_type_bundles(
    type_code: str,
    coupon_id: int | None = None,
    db: Session = Depends(get_db),
):
    """The ready-made accumulators on a bundle coupon, best value first.

    Bundles, not selections, are what this sheet offers: each is a complete bet
    with its own printed price, so the flat ranked row list the other types use
    would show a hundred legs and none of the bets they belong to.

    Scoped to one issue with `coupon_id`, exactly as `selections` is — a bundle
    is struck on one sheet, so spanning issues would list bets that were never
    printed together.
    """
    coupon_type = coupon_types.get(type_code)
    if coupon_type is None:
        raise HTTPException(status_code=404, detail=f"Unknown coupon type: {type_code}")
    if not coupon_type.bundles:
        raise HTTPException(
            status_code=400, detail=f"{coupon_type.label} is not a bundle coupon"
        )

    query = db.query(Coupon).filter(Coupon.type_code == type_code)
    if coupon_id is not None:
        issue = db.get(Coupon, coupon_id)
        if issue is None or issue.type_code != type_code:
            raise HTTPException(
                status_code=404, detail=f"No {type_code} coupon with id {coupon_id}"
            )
        coupons = [issue]
    else:
        coupons = query.order_by(
            Coupon.match_day.desc(), Coupon.created_at.desc()
        ).all()

    out = []
    for coupon in coupons:
        for bundle in _bundles_for_coupon(db, coupon):
            out.append(BundleOut(coupon_id=coupon.id, **bundle))
    # Best value first; unvaluable bundles last, in printed order.
    return sorted(out, key=lambda b: (b.value is None, -(b.value or 0), b.name))


@router.get("", response_model=list[CouponOut])
def list_coupons(db: Session = Depends(get_db)):
    coupons = (
        db.query(Coupon)
        .options(selectinload(Coupon.legs))
        .order_by(Coupon.match_day.desc(), Coupon.created_at.desc())
        .all()
    )
    return coupons


@router.get("/{coupon_id}/legs", response_model=list[LegOut])
def get_legs(coupon_id: int, db: Session = Depends(get_db)):
    coupon = (
        db.query(Coupon)
        .options(selectinload(Coupon.legs))
        .filter(Coupon.id == coupon_id)
        .first()
    )
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return sorted(coupon.legs, key=lambda l: (l.value is None, -(l.value or 0)))


@router.post("/{coupon_id}/refresh", response_model=list[LegOut])
def refresh_coupon(coupon_id: int, request: Request, db: Session = Depends(get_db)):
    """Refresh fair odds + value for one coupon's legs on demand.

    Reads through the odds cache (same as the 10-min loop) — never increases
    TheOddsApi call frequency.
    """
    coupon = (
        db.query(Coupon)
        .options(selectinload(Coupon.legs))
        .filter(Coupon.id == coupon_id)
        .first()
    )
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")

    price_legs(list(coupon.legs), request.app.state.odds_provider)
    db.commit()
    db.refresh(coupon)
    return sorted(coupon.legs, key=lambda l: (l.value is None, -(l.value or 0)))


@router.patch("/{coupon_id}/legs/{leg_id}", response_model=LegOut)
def edit_leg(
    coupon_id: int,
    leg_id: int,
    body: LegEditRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Correct a leg inline. Reprices it and marks it human-edited.

    `edited` is what protects the correction from the next re-scan, so it is
    set even if the correction doesn't change the price.
    """
    leg = db.get(Leg, leg_id)
    if not leg or leg.coupon_id != coupon_id:
        raise HTTPException(status_code=404, detail="Leg not found")

    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    for field, value in fields.items():
        setattr(leg, field, value)
    leg.edited = True

    # A corrected row may no longer be a value bet at the tier already alerted;
    # clearing lets the loop re-notify on its new merit rather than staying
    # silent because the pre-correction tier is still recorded.
    leg.notified_tier = None

    price_legs([leg], request.app.state.odds_provider)
    db.commit()
    db.refresh(leg)
    return leg
