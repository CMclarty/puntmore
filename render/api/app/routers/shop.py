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
from ..schemas import ShopStateOut, ShopVisitOut
from ..shop import service
from ..shop.service import (  # noqa: F401  (re-exported: they lived here)
    DAYS_PER_WEEK, GOAL, ROI_RATE, ROUTE_MODEL, SHOPS_PER_DAY, VALUE_PER_SHOP,
    WEEK_SIZE, WEEKS, _visits,
)
from ..security import require_user

router = APIRouter(prefix="/shop", tags=["shop"])

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
    visit = service.log_visit(db, user)
    if visit is None:
        raise HTTPException(status_code=409, detail="The route is already complete")
    return visit


@router.delete("/visits/{visit_id}", status_code=204)
def delete_visit(
    visit_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Undo one tap. Scoped to the owner, so an id from another user's route
    is a 404 rather than a delete."""
    if not service.delete_visit(db, user, visit_id):
        raise HTTPException(status_code=404, detail="Visit not found")


@router.delete("/visits", status_code=204)
def reset_route(
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Clear the whole route. The UI confirms first; this is the destructive
    half of that dialog and it only ever touches the calling user's rows."""
    service.reset_route(db, user)
