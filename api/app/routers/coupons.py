from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, selectinload
from ..database import get_db
from ..models import Coupon, Leg
from ..schemas import CouponOut, LegOut, LegEditRequest
from ..pricing.legs import price_legs
from ..security import require_user

router = APIRouter(prefix="/coupons", tags=["coupons"])


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
