"""/api/props — Paddy Power's player props against Pinnacle (`props` grant).

Thin: the logic is app/props/. Two read-only routes, the feed and its
diagnostics, off the boards the Paddy sweep holds (`app.state.cards_client`).
Nothing here places a bet.
"""
from fastapi import APIRouter, Query, Request

from ..props import service
from ..schemas import PropsDiagnosticsOut, PropsFeedItem

router = APIRouter(prefix="/props", tags=["props"])


def _source(request: Request):
    return getattr(request.app.state, "cards_client", None)


@router.get("/feed", response_model=list[PropsFeedItem])
def get_feed(request: Request, min_edge: float = Query(default=0.0, ge=-0.5, le=0.5)):
    """Every fixture still to kick off with a Paddy player price at or above
    `min_edge` of Pinnacle's fair, soonest first, its bets best value first."""
    return service.build_feed(_source(request), min_edge)


@router.get("/diagnostics", response_model=PropsDiagnosticsOut)
def get_diagnostics(request: Request):
    """Per stat: Paddy's selections, Pinnacle's two-sided ones, how many matched
    and why the rest did not, with a one-line verdict."""
    return service.diagnose(_source(request))
