"""Shop route logic that needs no FastAPI — routers/shop.py calls it.

The route's target arithmetic and the per-user visit queries, moved out of the
router (Oct 2026). Behaviour unchanged.
"""
from sqlalchemy.orm import Session

from ..models import ShopVisit
from ..schemas import ShopModelOut

# The route's target arithmetic:
#   £500/shop × 5 shops/day × 4 days/week × 40 weeks = £400,000 of pipeline,
# at an assumed 20% ROI. WEEK_SIZE is 5 × 4 — a "week" is a unit of twenty
# VISITS, not a calendar week, so a quiet week doesn't reset the count.
# Served to the client (GET /shop) so these five numbers are defined once.
SHOPS_PER_DAY = 5
DAYS_PER_WEEK = 4
WEEK_SIZE = SHOPS_PER_DAY * DAYS_PER_WEEK
WEEKS = 40
GOAL = WEEK_SIZE * WEEKS
VALUE_PER_SHOP = 500.0
ROI_RATE = 0.2

ROUTE_MODEL = ShopModelOut(
    shops_per_day=SHOPS_PER_DAY,
    days_per_week=DAYS_PER_WEEK,
    week_size=WEEK_SIZE,
    weeks=WEEKS,
    goal=GOAL,
    value_per_shop=VALUE_PER_SHOP,
    roi_rate=ROI_RATE,
)


def _visits(db: Session, user: str):
    """This user's visits, oldest first — the order the counters accumulate in."""
    return (
        db.query(ShopVisit)
        .filter(ShopVisit.user == user)
        .order_by(ShopVisit.created_at.asc(), ShopVisit.id.asc())
        .all()
    )


def visit_count(db: Session, user: str) -> int:
    return db.query(ShopVisit).filter(ShopVisit.user == user).count()


def log_visit(db: Session, user: str) -> ShopVisit | None:
    """Add one stop; None once the route is complete (the cap is enforced here,
    not only by the disabled button)."""
    if visit_count(db, user) >= GOAL:
        return None
    visit = ShopVisit(user=user)
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


def delete_visit(db: Session, user: str, visit_id: int) -> bool:
    """Undo one tap, owner-scoped. False when it is not this user's."""
    visit = (
        db.query(ShopVisit)
        .filter(ShopVisit.id == visit_id, ShopVisit.user == user)
        .first()
    )
    if visit is None:
        return False
    db.delete(visit)
    db.commit()
    return True


def reset_route(db: Session, user: str) -> None:
    db.query(ShopVisit).filter(ShopVisit.user == user).delete()
    db.commit()
