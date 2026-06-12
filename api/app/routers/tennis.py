from fastapi import APIRouter, Query, Request

from ..config import settings
from ..schemas import ArbOut, TennisFeedItem
from ..tennis.arbs import SPORT_KEYS, compute_arbs

router = APIRouter()


def _sport_keys() -> list[str]:
    """Full AccaBonus list, or the TENNIS_SPORT_KEYS override (quota testing)."""
    if settings.tennis_sport_keys:
        return [k.strip() for k in settings.tennis_sport_keys.split(",") if k.strip()]
    return SPORT_KEYS


def _fetch_events(client) -> list[dict]:
    events: list[dict] = []
    for sport_key in _sport_keys():
        try:
            # Explicit bookmakers: bet365 is UK-region (absent from the default
            # eu region) and naming them costs 1 credit vs region pricing.
            events.extend(client.fetch(sport_key, "h2h", bookmakers="bet365,pinnacle"))
        except Exception:
            continue
    return events


@router.get("/tennis/arbs", response_model=list[ArbOut])
def get_tennis_arbs(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    if min_edge is None:
        min_edge = settings.arb_min_edge
    client = request.app.state.arbs_client
    if client is None:
        return []
    return compute_arbs(_fetch_events(client), min_edge)


@router.get("/tennis/feed", response_model=list[TennisFeedItem])
def get_tennis_feed(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    """Simplified tennis feed: one card per event showing the best-value side."""
    if min_edge is None:
        min_edge = settings.arb_min_edge
    client = request.app.state.arbs_client
    if client is None:
        return []

    arbs = compute_arbs(_fetch_events(client), min_edge)
    feed = []
    for arb in arbs:
        if arb.edge_home >= arb.edge_away:
            pick, odds, value = arb.home_team, arb.bet365_home, arb.edge_home
        else:
            pick, odds, value = arb.away_team, arb.bet365_away, arb.edge_away
        feed.append(TennisFeedItem(
            match=f"{arb.home_team} vs {arb.away_team}",
            pick=pick,
            odds=odds,
            value=value,
            is_arb=arb.is_arb,
        ))
    return feed
