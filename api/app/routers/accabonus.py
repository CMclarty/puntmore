from fastapi import APIRouter, Query, Request

from ..accabonus.arbs import FALLBACK_SPORT_KEYS, compute_arbs, select_accabonus_keys
from ..config import settings
from ..schemas import AccaBonusFeedItem, ArbOut

router = APIRouter()


def _sport_keys(client) -> list[str]:
    """Active Acca Boost-eligible league keys, discovered from the free sports
    list; or the ACCABONUS_SPORT_KEYS override (quota testing)."""
    if settings.accabonus_sport_keys:
        return [k.strip() for k in settings.accabonus_sport_keys.split(",") if k.strip()]
    try:
        return select_accabonus_keys(client.list_sports())
    except Exception:
        return FALLBACK_SPORT_KEYS


def _fetch_events(client) -> list[dict]:
    events: list[dict] = []
    for sport_key in _sport_keys(client):
        try:
            # Explicit bookmakers: bet365 is UK-region (absent from the default
            # eu region) and naming them costs 1 credit vs region pricing.
            events.extend(client.fetch(sport_key, "h2h", bookmakers="bet365,pinnacle"))
        except Exception:
            continue
    return events


# Legacy /tennis/* aliases kept so PWA clients with a cached pre-rename shell
# don't 404 until their service worker updates. Hidden from the schema.
@router.get("/accabonus/arbs", response_model=list[ArbOut])
@router.get("/tennis/arbs", response_model=list[ArbOut], include_in_schema=False)
def get_accabonus_arbs(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    if min_edge is None:
        min_edge = settings.arb_min_edge
    client = request.app.state.arbs_client
    if client is None:
        return []
    return compute_arbs(_fetch_events(client), min_edge)


def build_feed(client, min_edge: float) -> list[AccaBonusFeedItem]:
    """One card per event showing the best-value side. Shared with /state."""
    if client is None:
        return []
    arbs = compute_arbs(_fetch_events(client), min_edge)
    feed = []
    for arb in arbs:
        picks = [
            (arb.home_team, arb.bet365_home, arb.edge_home),
            (arb.away_team, arb.bet365_away, arb.edge_away),
        ]
        if arb.edge_draw is not None:
            picks.append(("Draw", arb.bet365_draw, arb.edge_draw))
        pick, odds, value = max(picks, key=lambda p: p[2])
        feed.append(AccaBonusFeedItem(
            match=f"{arb.home_team} vs {arb.away_team}",
            sport=arb.sport_title,
            pick=pick,
            odds=odds,
            value=value,
            is_arb=arb.is_arb,
        ))
    return feed


@router.get("/accabonus/feed", response_model=list[AccaBonusFeedItem])
@router.get("/tennis/feed", response_model=list[AccaBonusFeedItem], include_in_schema=False)
def get_accabonus_feed(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    """Simplified AccaBonus feed: one card per event showing the best-value side."""
    if min_edge is None:
        min_edge = settings.arb_min_edge
    return build_feed(request.app.state.arbs_client, min_edge)
