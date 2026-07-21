import math

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from ..database import get_db
from ..models import Coupon, Leg
from datetime import date

from .. import coupon_types, coupon_periods
from ..coupon_periods import COUPON_PERIODS
from ..coupon_types import COUPON_TYPES
from ..schemas import (
    CouponOut, CouponTypeOut, CouponPeriodOut, LegOut, LegEditRequest,
)
from ..pricing.legs import price_legs
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
        ctype = coupon_types.get(type_code)
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


@router.get("/types", response_model=list[CouponTypeOut])
def list_coupon_types(db: Session = Depends(get_db)):
    """The seven coupon types — the app's top-level entities.

    Each is its own filtered list of fixtures, not an accumulator. Served from
    one place so the tracker dropdown and the upload form cannot drift.
    """
    counts = dict(
        db.query(Coupon.type_code, func.count(Leg.id))
        .join(Leg, Leg.coupon_id == Coupon.id)
        .group_by(Coupon.type_code)
        .all()
    )
    out = []
    for t in COUPON_TYPES:
        best = None
        if not t.prices_each_selection:
            # Ladder coupons have no per-row value, so the useful number is the
            # best combination available. Read off the MOST RECENT coupon of
            # this type: the ladder and the fixture list both belong to an
            # issue, not to the type.
            latest = (
                db.query(Coupon)
                .filter(Coupon.type_code == t.code)
                .order_by(Coupon.match_day.desc(), Coupon.created_at.desc())
                .first()
            )
            if latest is not None:
                fairs = [
                    row[0] for row in db.query(Leg.fair_odds)
                    .filter(Leg.coupon_id == latest.id, Leg.fair_odds.isnot(None))
                    .all()
                ]
                best = coupon_types.best_combination(
                    fairs, latest.ladder or t.default_ladder
                )
        out.append(CouponTypeOut(
            code=t.code,
            label=t.label,
            max_fair_odds=t.max_fair_odds,
            selection_count=counts.get(t.code, 0),
            prices_each_selection=t.prices_each_selection,
            period_codes=list(t.period_codes),
            best_combination=best,
        ))
    return out


@router.get("/types", response_model=list[CouponTypeOut])
def list_coupon_types(db: Session = Depends(get_db)):
    """The seven coupon types — the app's top-level entities.

    Each is its own filtered list of fixtures, not an accumulator. Served from
    one place so the tracker dropdown and the upload form cannot drift.
    """
    counts = dict(
        db.query(Coupon.type_code, func.count(Leg.id))
        .join(Leg, Leg.coupon_id == Coupon.id)
        .group_by(Coupon.type_code)
        .all()
    )
    out = []
    for t in COUPON_TYPES:
        combo_value = combo_fair = None
        if t.flat_payout is not None:
            # Shortest priced selections make the flat return best, so the best
            # combination is simply the N shortest.
            shortest = (
                db.query(Leg.fair_odds)
                .join(Coupon, Leg.coupon_id == Coupon.id)
                .filter(Coupon.type_code == t.code, Leg.fair_odds.isnot(None))
                .order_by(Leg.fair_odds)
                .limit(t.payout_picks)
                .all()
            )
            picks = [row[0] for row in shortest]
            if len(picks) == t.payout_picks:
                combo_value = coupon_types.combination_value(picks, t.flat_payout)
                combo_fair = round(math.prod(picks), 4)
        out.append(CouponTypeOut(
            code=t.code,
            label=t.label,
            max_fair_odds=t.max_fair_odds,
            selection_count=counts.get(t.code, 0),
            flat_payout=t.flat_payout,
            payout_picks=t.payout_picks,
            best_combination_value=combo_value,
            best_combination_fair=combo_fair,
        ))
    return out


@router.get("/types/{type_code}/selections", response_model=list[LegOut])
def list_type_selections(type_code: str, db: Session = Depends(get_db)):
    """One coupon type's filtered fixture list, ranked best-value first.

    Spans every upload of this type — the type is the entity, an upload is just
    how selections got into it.
    """
    coupon_type = coupon_types.get(type_code)
    if coupon_type is None:
        raise HTTPException(status_code=404, detail=f"Unknown coupon type: {type_code}")

    selections = (
        db.query(Leg)
        .join(Coupon, Leg.coupon_id == Coupon.id)
        .filter(Coupon.type_code == type_code)
        .all()
    )

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
