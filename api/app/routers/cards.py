from fastapi import APIRouter, Query, Request

from ..config import settings
from ..schemas import CardValueOut, CardFeedItem, CardFeedBet
from ..cards.value import compute_value_bets
from ..cards.laddered import compute_laddered_value
from ..cards.client import merge_pinnacle


def price_events(events, min_edge):
    """Value rows from joined events, by whichever pricing path is configured.

    The laddered path prices every line Paddy Power quotes off Pinnacle's fitted
    ladder; the matched-line path compares only lines both books picked. One
    entry point so the router, the feed and the scheduler cannot diverge on
    which is in use.
    """
    if settings.cards_use_ladder_model:
        return compute_laddered_value(events, min_edge)
    return compute_value_bets(events, min_edge)

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
    # Paddy Power from TheOddsApi, Pinnacle from bettingiscool — joined into
    # one event before valuing, so value.py stays vendor-agnostic.
    events = merge_pinnacle(
        client.get_events(), getattr(request.app.state, "odds_provider", None)
    )
    return price_events(events, min_edge)


def build_feed(client, min_edge: float, pinnacle=None) -> list[CardFeedItem]:
    """Cards/corners feed grouped by match, sorted by best value. Shared with /state."""
    if client is None:
        return []

    bets = price_events(merge_pinnacle(client.get_events(), pinnacle), min_edge)

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


@router.get("/cards/feed", response_model=list[CardFeedItem])
def get_cards_feed(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    if min_edge is None:
        min_edge = settings.cards_min_edge
    return build_feed(
        request.app.state.cards_client, min_edge,
        getattr(request.app.state, "odds_provider", None),
    )


def diagnose(client, pinnacle) -> dict:
    """Why the cards screen is showing what it is showing.

    An empty screen has at least six distinct causes and they look identical
    from the outside — no fixtures near kickoff, Paddy Power not posting the
    market, bettingiscool carrying no corners board, ladders too thin to fit,
    or simply nothing clearing the value guards. The last of those is a SUCCESS
    and must not be mistaken for a broken tool.

    The counts come from whatever the last sweep actually did, so this reports
    the real pipeline rather than re-running it.
    """
    from ..cards import laddered, pinnacle as pinnacle_src

    sweep = dict(getattr(client, "last_sweep", {}) or {})
    fair = dict(getattr(pinnacle_src, "LAST_RUN", {}) or {})
    priced = dict(getattr(laddered, "LAST_RUN", {}) or {})

    if client is None:
        verdict = (
            "No cards client — ODDS_API_KEY is not set, so Paddy Power's prices "
            "are never fetched."
        )
    elif not sweep:
        verdict = "No sweep has run yet. Open the feed or wait for the 5-minute tick."
    elif sweep.get("fixtures_in_window", 0) == 0:
        nxt = sweep.get("next_kickoff")
        inactive = sweep.get("inactive_keys") or []
        verdict = (
            f"No fixture kicks off within {sweep.get('window_hours')}h, so nothing "
            f"was fetched. These markets are only posted near kickoff — this is "
            f"the normal state between matches, not a failure. "
            + (f"Next listed kickoff: {nxt}. " if nxt else
               f"No upcoming fixture is listed in ANY of the "
               f"{sweep.get('sport_keys')} covered competitions. ")
            + (f"{len(inactive)} configured key(s) not currently listed by "
               f"TheOddsApi (out of season, or misspelled): "
               f"{', '.join(inactive)}." if inactive else "")
        ).strip()
    elif sweep.get("carried_paddy_prices", 0) == 0:
        verdict = (
            f"{sweep.get('called')} fixture(s) called and NONE carried Paddy Power "
            f"corners/cards prices. Paddy Power is not posting these markets on "
            f"these fixtures yet."
        )
    elif not pinnacle or not (hasattr(pinnacle, "board") and hasattr(pinnacle, "odds_for")):
        verdict = (
            "The coupon odds provider is not bettingiscool, so there is no "
            "Pinnacle corners board to price against. Set BETTINGISCOOL_API_KEY."
        )
    elif fair.get("derived_fixtures", 0) == 0:
        verdict = (
            f"bettingiscool's board ({fair.get('board_fixtures', 0)} fixtures) "
            f"carries no derived corners/cards events, so there is no fair price "
            f"to measure Paddy Power against."
        )
    elif fair.get("matched", 0) == 0:
        verdict = (
            f"{fair.get('derived_fixtures')} derived fixture(s) on the board but "
            f"none matched a Paddy Power fixture by name."
        )
    elif fair.get("no_totals", 0) and not priced.get("ladders_fitted"):
        verdict = (
            f"{fair.get('no_totals')} matched fixture(s) carry no totals, so no "
            f"ladder could be built."
        )
    elif priced.get("ladders_fitted", 0) == 0:
        verdict = (
            f"No ladder was fittable — {priced.get('ladders_refused', 0)} refused. "
            f"A fixture needs at least 2 two-sided Pinnacle rungs."
        )
    elif priced.get("rows", 0) == 0:
        verdict = (
            f"WORKING: {priced.get('ladders_fitted')} ladder(s) fitted and "
            f"{priced.get('priceable_paddy_lines', 0)} Paddy Power line(s) priced, "
            f"but none cleared the value guards. That is the tool doing its job — "
            f"most lines hold no edge."
        )
    else:
        verdict = f"{priced.get('rows')} value row(s) found."

    return {
        "verdict": verdict,
        "sweep": sweep,
        "fair_side": fair,
        "pricing": priced,
        "using_ladder_model": settings.cards_use_ladder_model,
    }


@router.get("/cards/diagnostics")
def get_cards_diagnostics(request: Request):
    """One request that says why the screen looks the way it does."""
    return diagnose(
        request.app.state.cards_client,
        getattr(request.app.state, "odds_provider", None),
    )
