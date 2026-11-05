"""Whole-app state snapshot for machine consumers (dashboards, bots).

Unlike the other data routers this is NOT session-guarded — it self-guards
with a static API key (STATE_API_KEY in .env, sent as X-API-Key). The key
check uses a constant-time comparison; a blank key disables the endpoint
entirely rather than allowing unauthenticated access.

Feed data reads through the shared odds cache (same as the UI endpoints),
so polling this endpoint never increases TheOddsApi call frequency.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..database import get_db
from ..models import Coupon
from ..schemas import CouponStateOut, StateOut
from ..security import require_key_grant
from ..time_utils import utcnow
from . import accabonus, cards

router = APIRouter()


# The `state` grant: STATE_API_KEY, or an API client granted `state`.
require_api_key = require_key_grant("state", "state_api_key")


@router.get("/state", response_model=StateOut, dependencies=[Depends(require_api_key)])
def get_state(request: Request, db: Session = Depends(get_db)):
    coupons = (
        db.query(Coupon)
        .options(selectinload(Coupon.legs))
        .order_by(Coupon.match_day.desc(), Coupon.created_at.desc())
        .all()
    )
    coupon_items = []
    for c in coupons:
        item = CouponStateOut.model_validate(c)
        item.legs = sorted(item.legs, key=lambda l: (l.value is None, -(l.value or 0)))
        coupon_items.append(item)

    return StateOut(
        generated_at=utcnow(),
        coupons=coupon_items,
        accabonus=accabonus.build_feed(
            request.app.state.arbs_client, settings.arb_min_edge
        ),
        cards=cards.build_feed(request.app.state.cards_client, settings.cards_min_edge),
    )
