from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, selectinload

from ..coupons import service
# Re-exported: these helpers lived here until Oct 2026.
from ..coupons.service import (  # noqa: F401
    _best_bundle_for_coupon, _best_for_coupon, _bundles_for_coupon, _issue_status,
    _scan_note,
)
from ..database import get_db
from ..models import Coupon, Leg
from ..pricing.legs import price_legs
from ..schemas import (
    BundleOut, CouponOut, CouponTypeOut, CouponPeriodOut, LegOut,
    LegEditRequest, UploadOptionOut,
)
from ..security import require_user

router = APIRouter(prefix="/coupons", tags=["coupons"])


@router.get("/periods", response_model=list[CouponPeriodOut])
def list_coupon_periods(type_code: str | None = None):
    """The windows a coupon type runs, with the dates they resolve to.

    Filtered by type because the two shapes differ: the priced coupons run a
    fixed weekly schedule, while GG issues are midweek/weekend on dates that
    move and are only known once the coupon has been read.
    """
    out = service.coupon_periods_for(type_code, date.today())
    if out is None:
        raise HTTPException(status_code=404, detail=f"Unknown coupon type: {type_code}")
    return out


@router.get("/upload-options", response_model=list[UploadOptionOut])
def list_upload_options():
    """What can be photographed: the two multi-market sheets, and the types
    printed on a sheet of their own. The upload form's one dropdown."""
    return service.upload_options()


@router.get("/types", response_model=list[CouponTypeOut])
def list_coupon_types(db: Session = Depends(get_db)):
    """The coupon types — the app's top-level entities (coupons/service.py)."""
    return service.coupon_types_board(db)


@router.get("/types/{type_code}/selections", response_model=list[LegOut])
def list_type_selections(
    type_code: str,
    coupon_id: int | None = None,
    db: Session = Depends(get_db),
):
    """One coupon type's filtered fixture list, ranked best-value first;
    `coupon_id` narrows it to ONE issue (coupons/service.type_selections)."""
    try:
        return service.type_selections(db, type_code, coupon_id)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/types/{type_code}/bundles", response_model=list[BundleOut])
def list_type_bundles(
    type_code: str,
    coupon_id: int | None = None,
    db: Session = Depends(get_db),
):
    """The ready-made accumulators on a bundle coupon, best value first,
    scoped to one issue with `coupon_id` (coupons/service.type_bundles)."""
    try:
        return service.type_bundles(db, type_code, coupon_id)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except service.Invalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
