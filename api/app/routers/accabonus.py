import logging

from fastapi import APIRouter, Query, Request

from ..accabonus.arbs import FALLBACK_SPORT_KEYS, compute_arbs, select_accabonus_keys
from ..config import settings
from ..schemas import AccaBonusFeedItem, ArbOut

logger = logging.getLogger("uvicorn.error")

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
    """Bet365 + Pinnacle h2h for every eligible sport key.

    PINNACLE IS THE MEASURING STICK, NOT A SECOND BOOK TO BET AT. The Acca Boost
    is a bet365 promotion, so bet365 is the only place any of this is placed;
    de-vigged Pinnacle is simply what "fair odds" means, exactly as it is for
    the coupon tracker and the cards tool.

    Reads on `accabonus_cache_ttl` rather than the shared 24-hour default, so
    the refresh rate here can be changed without moving every other consumer.
    """
    events: list[dict] = []
    fetched = skipped = failed = 0

    for sport_key in _sport_keys(client):
        # The same reserve the cards sweep respects. Without it a short TTL
        # here could quietly eat the allowance the coupon fallback and the
        # goalscorer scans depend on.
        remaining = getattr(client, "quota_remaining", None)
        if remaining is not None and remaining <= settings.cards_quota_reserve:
            skipped += 1
            continue
        try:
            # Explicit bookmakers: bet365 is UK-region (absent from the default
            # eu region) and naming them costs 1 credit vs region pricing.
            events.extend(client.fetch(
                sport_key, "h2h", bookmakers="bet365,pinnacle",
                ttl=settings.accabonus_cache_ttl,
            ))
            fetched += 1
        except Exception as exc:
            # Counted and logged: a silently swallowed failure here is what let
            # the cards client return nothing for months.
            failed += 1
            logger.error("accabonus: %s fetch failed: %s", sport_key, exc)

    if skipped:
        logger.error(
            "accabonus: skipped %d sport key(s) — TheOddsApi quota is at or "
            "below the %d reserve.", skipped, settings.cards_quota_reserve,
        )
    log = logger.info if not failed else logger.warning
    log(
        "accabonus pull: %d key(s) read, %d failed, %d skipped, %d event(s), "
        "ttl=%ss",
        fetched, failed, skipped, len(events), settings.accabonus_cache_ttl,
    )
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
