"""Shop visit countdown — a per-user tally of stops on a field route.

Router-gated by require_tool("shop") (main.py) and every row is scoped to the
session user, so a user only ever sees and edits their own visits. Like the P&L
tracker the API is thin: it stores visits and serves the route's constants, and
the frontend derives the counters, the progress bars and the money figures.

The tally lives here rather than in the browser so it follows the user across
devices — the phone in the field and the desk machine are the same route.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ShopVisit
from ..schemas import ShopModelOut, ShopStateOut, ShopVisitOut
from ..security import require_user

router = APIRouter(prefix="/shop", tags=["shop"])

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


@router.get("", response_model=ShopStateOut)
def get_route(
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Everything the page needs in one round trip: the model and the visits.

    The full list rather than a count: it is bounded by GOAL (800 small rows),
    and it gives the client an id per visit so undo deletes a specific row
    instead of racing on "whichever is newest".
    """
    return ShopStateOut(model=ROUTE_MODEL, visits=_visits(db, user))


@router.post("/visits", response_model=ShopVisitOut)
def log_visit(
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Log one stop. Refused once the route is complete.

    The cap is enforced here and not only by the disabled button, so a stale
    tab or a double-tap in flight can't push the count past the goal and leave
    the page rendering a negative "left of 800".
    """
    if db.query(ShopVisit).filter(ShopVisit.user == user).count() >= GOAL:
        raise HTTPException(status_code=409, detail="The route is already complete")
    visit = ShopVisit(user=user)
    db.add(visit)
    db.commit()
    db.refresh(visit)
    return visit


@router.delete("/visits/{visit_id}", status_code=204)
def delete_visit(
    visit_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Undo one tap. Scoped to the owner, so an id from another user's route
    is a 404 rather than a delete."""
    visit = (
        db.query(ShopVisit)
        .filter(ShopVisit.id == visit_id, ShopVisit.user == user)
        .first()
    )
    if visit is None:
        raise HTTPException(status_code=404, detail="Visit not found")
    db.delete(visit)
    db.commit()


@router.delete("/visits", status_code=204)
def reset_route(
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Clear the whole route. The UI confirms first; this is the destructive
    half of that dialog and it only ever touches the calling user's rows."""
    db.query(ShopVisit).filter(ShopVisit.user == user).delete()
    db.commit()
