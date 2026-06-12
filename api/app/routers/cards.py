from fastapi import APIRouter, Query, Request

from ..config import settings
from ..schemas import CardValueOut, CardFeedItem, CardFeedBet
from ..cards.value import compute_value_bets

router = APIRouter()


@router.get("/cards/value-bets", response_model=list[CardValueOut])
def get_card_value_bets(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    if min_edge is None:
        min_edge = settings.cards_min_edge
    client = request.app.state.cards_client
    if client is None:
        return []
    return compute_value_bets(client.get_events(), min_edge)


@router.get("/cards/feed", response_model=list[CardFeedItem])
def get_cards_feed(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    """Cards/corners feed grouped by match, sorted by best value."""
    if min_edge is None:
        min_edge = settings.cards_min_edge
    client = request.app.state.cards_client
    if client is None:
        return []

    bets = compute_value_bets(client.get_events(), min_edge)

    groups: dict[str, dict] = {}
    for bet in bets:
        if bet.event_id not in groups:
            groups[bet.event_id] = {
                "event_id": bet.event_id,
                "match": f"{bet.home_team} vs {bet.away_team}",
                "commence_time": bet.commence_time,
                "bets": [],
            }
        groups[bet.event_id]["bets"].append(CardFeedBet(
            pick=f"{bet.side} {bet.line} ({bet.market_label})",
            odds=bet.paddy_odds,
            value=bet.edge,
        ))

    result = []
    for g in groups.values():
        g["bets"].sort(key=lambda b: -b.value)
        result.append(CardFeedItem(
            event_id=g["event_id"],
            match=g["match"],
            commence_time=g["commence_time"],
            best_value=max(b.value for b in g["bets"]),
            bets=g["bets"],
        ))

    result.sort(key=lambda x: -x.best_value)
    return result
