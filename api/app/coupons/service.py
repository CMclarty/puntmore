"""Coupon tracker logic that needs no FastAPI — routers/coupons.py calls it."""
from datetime import datetime

from sqlalchemy.orm import Session, joinedload

from .. import coupon_types
from ..models import Coupon, Leg


def live_value_legs(db: Session, now: datetime) -> list[Leg]:
    """Priced selections still to kick off that carry a per-row edge.

    Only types that print a price on each selection: a ladder or bundle
    coupon's value belongs to the COMBINATION, never to a row, so it has no
    selection to list. A leg with no kickoff is not live (the tracker's rule:
    an unread time must not hold a coupon open).
    """
    priced_types = [t.code for t in coupon_types.COUPON_TYPES if t.prices_each_selection]
    return (
        db.query(Leg)
        .join(Coupon, Leg.coupon_id == Coupon.id)
        .options(joinedload(Leg.coupon))
        .filter(
            Coupon.type_code.in_(priced_types),
            Leg.odds.isnot(None), Leg.fair_odds.isnot(None), Leg.value.isnot(None),
            Leg.bundle.is_(None),
            Leg.kickoff.isnot(None), Leg.kickoff > now,
        )
        .all()
    )
