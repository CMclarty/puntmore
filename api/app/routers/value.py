"""GET /api/value — what value is on offer right now, across every tool.

Grant `value` (or "*") sees every source; any tool grant sees that tool's
rows only. The logic is app/value/service.py; this router only resolves who
is asking and reads the query. Read-only: nothing here places a bet.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ValueFeedOut
from ..security import enabled_tools, user_permissions
from ..value import service

router = APIRouter(prefix="/value", tags=["value"])


def _feed(request: Request, db: Session, sources, min_edge, kickoff_within_hours, bookmaker):
    now = datetime.now(timezone.utc)
    rows, errors = service.collect(
        sources, db=db,
        cards_client=getattr(request.app.state, "cards_client", None),
        arbs_client=getattr(request.app.state, "arbs_client", None),
        min_edge=min_edge, kickoff_within_hours=kickoff_within_hours,
        bookmaker=bookmaker, now=now,
    )
    return ValueFeedOut(generated_at=now, sources=[s.name for s in sources],
                        errors=errors, selections=rows)


MIN_EDGE = Query(default=None, ge=-0.5, le=10,
                 description="Rows with edge >= this. Omitted: edge > 0.")
WITHIN = Query(default=None, gt=0, le=24 * 14,
               description="Only selections kicking off within this many hours.")
BOOKMAKER = Query(default=None, max_length=40,
                  description="paddypower | bet365 | williamhill")


@router.get("", response_model=ValueFeedOut)
def get_value(
    request: Request,
    min_edge: float | None = MIN_EDGE,
    kickoff_within_hours: float | None = WITHIN,
    bookmaker: str | None = BOOKMAKER,
    perms: str = Depends(user_permissions),
    db: Session = Depends(get_db),
):
    """Every value selection this caller may see, best edge first."""
    sources = service.visible_sources(perms)
    if not sources:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return _feed(request, db, sources, min_edge, kickoff_within_hours, bookmaker)


@router.get("/{source}", response_model=ValueFeedOut)
def get_value_for(
    source: str,
    request: Request,
    min_edge: float | None = MIN_EDGE,
    kickoff_within_hours: float | None = WITHIN,
    bookmaker: str | None = BOOKMAKER,
    perms: str = Depends(user_permissions),
    db: Session = Depends(get_db),
):
    """One source's value selections (paddy | 365 | scorers | coupon)."""
    if source not in service.BY_NAME or service.BY_NAME[source].tool not in enabled_tools():
        raise HTTPException(status_code=404, detail=f"Unknown source: {source}")
    allowed = [s for s in service.visible_sources(perms) if s.name == source]
    if not allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return _feed(request, db, allowed, min_edge, kickoff_within_hours, bookmaker)
