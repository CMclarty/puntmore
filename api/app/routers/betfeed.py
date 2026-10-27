"""GET /api/value-bets and GET /api/results — the bet feed for a coordinator
(Daedalus, the 2-Up coordinator). Logic in app/betfeed/service.py.

KEY-ONLY, like /state: FEED_API_KEY (X-API-Key or Bearer), or an API client
granted `feed`. A session does not open these. Unset key and no client → 503.

Read-only to the caller. The value-bets read records what it served (the
fixture/selection ledger results are keyed by) — internal bookkeeping no
caller input can steer. It reads through the 365 page's odds cache.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..betfeed import service
from ..database import get_db
from ..schemas import FeedResultOut, FeedValueBetOut
from ..security import enabled_tools, require_key_grant

router = APIRouter(tags=["feed"])

require_feed_key = require_key_grant("feed", "feed_api_key")


@router.get("/value-bets", response_model=list[FeedValueBetOut],
            dependencies=[Depends(require_feed_key)])
def get_value_bets(
    request: Request,
    sport: str = Query(default="football", max_length=40,
                       description='football | tennis | … | "all"'),
    min_edge: float | None = Query(
        default=None, ge=-0.5, le=10,
        description="Rows with edge >= this (fraction, 0.03 = 3%). Omitted: edge > 0."),
    db: Session = Depends(get_db),
):
    """bet365 selections priced above de-vigged Pinnacle, best value first."""
    # The 365 feed spends TheOddsApi credits on a cold cache. While the tool is
    # switched off nothing else pulls it, so this must not start to.
    if "accabonus" not in enabled_tools():
        raise HTTPException(status_code=503, detail="365 feed is off (ENABLE_ACCABONUS)")
    client = getattr(request.app.state, "arbs_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="No odds client (ODDS_API_KEY unset)")
    try:
        bets = service.value_bets(client, sport=sport.strip().lower(), min_edge=min_edge)
    except Exception as exc:      # a vendor failure is not "no value": say so
        raise HTTPException(status_code=503,
                            detail=f"365 feed failed: {type(exc).__name__}") from exc
    service.record_served(db, bets)
    return bets


@router.get("/results", response_model=list[FeedResultOut],
            dependencies=[Depends(require_feed_key)])
def get_results(
    since: datetime | None = Query(
        default=None,
        description="ISO 8601. Only results recorded strictly after this (UTC if no offset)."),
    db: Session = Depends(get_db),
):
    """Final results for fixtures the bet feed has served, oldest first."""
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return service.results(db, since)
