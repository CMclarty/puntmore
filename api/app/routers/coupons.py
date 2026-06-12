from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, selectinload
from ..database import get_db
from ..models import Coupon
from ..schemas import CouponOut, LegOut
from ..providers.base import MatchQuery
from ..comparison import compute_value
from ..time_utils import utcnow

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

    queries = [
        MatchQuery(match_id=leg.id, match_name=leg.match_name, market=leg.pick)
        for leg in coupon.legs
    ]
    odds_provider = request.app.state.odds_provider
    results = odds_provider.get_fair_odds(queries)
    result_map = {r.match_id: r for r in results}
    now = utcnow()

    for leg in coupon.legs:
        result = result_map.get(leg.id)
        if result and result.fair_odds is not None:
            leg.fair_odds = result.fair_odds
            leg.value = compute_value(leg.odds, result.fair_odds)
            leg.fair_odds_updated_at = now

    db.commit()
    db.refresh(coupon)
    return sorted(coupon.legs, key=lambda l: (l.value is None, -(l.value or 0)))
