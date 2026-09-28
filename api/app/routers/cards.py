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
    # Paddy Power and Pinnacle from OddsPapi, on one fixture id; bettingiscool
    # only for an event that arrives without Pinnacle. Joined before valuing,
    # so value.py stays vendor-agnostic.
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

    oddspapi_fair = str(sweep.get("fair_source", "")).startswith("oddspapi")
    if client is None:
        verdict = (
            "No cards client — ODDSPAPI_API_KEY is not set, so Paddy Power's "
            "prices are never fetched."
        )
    elif not sweep:
        verdict = "No sweep has run yet. Open the feed or wait for the 5-minute tick."
    elif sweep.get("error"):
        verdict = f"The last sweep failed before fetching odds: {sweep['error']}"
    elif sweep.get("fixtures_in_window", 0) == 0:
        nxt = sweep.get("next_kickoff")
        missing = sweep.get("unresolved_tournaments") or []
        verdict = (
            f"No fixture kicks off within {sweep.get('window_hours')}h, so nothing "
            f"was priced. These markets are only posted near kickoff — this is "
            f"the normal state between matches, not a failure. "
            + (f"Next listed kickoff: {nxt}. " if nxt else
               f"No upcoming fixture is listed in ANY of the "
               f"{sweep.get('tournaments')} covered competitions. ")
            + (f"{len(missing)} configured competition(s) not found on OddsPapi: "
               f"{', '.join(missing)}." if missing else "")
        ).strip()
    elif sweep.get("carried_paddy_prices", 0) == 0:
        verdict = (
            f"{sweep.get('fixtures_in_window')} fixture(s) in range and NONE "
            f"carried Paddy Power corners/cards prices. Paddy Power is not "
            f"posting these markets on these fixtures yet."
        )
    elif oddspapi_fair and sweep.get("carried_pinnacle_prices", 0) == 0:
        verdict = (
            f"Paddy Power prices {sweep.get('carried_paddy_prices')} fixture(s) "
            f"but Pinnacle posts corners/cards on none of them yet, so there is "
            f"no fair price to measure against. Pinnacle posts these late — "
            f"normal until close to kickoff."
        )
    elif oddspapi_fair:
        verdict = None
    elif not pinnacle or not (hasattr(pinnacle, "board") and hasattr(pinnacle, "odds_for")):
        verdict = (
            "The coupon odds provider is not bettingiscool, so there is no "
            "Pinnacle corners board to price against. Set BETTINGISCOOL_API_KEY."
        )
    else:
        verdict = None
    if verdict is None:
        verdict = _pricing_verdict(fair if not oddspapi_fair else None, priced)

    return {
        "verdict": verdict,
        "sweep": sweep,
        "fair_side": fair,
        "pricing": priced,
        "using_ladder_model": settings.cards_use_ladder_model,
    }


def _pricing_verdict(fair: dict | None, priced: dict) -> str:
    """Past the sweep: the bettingiscool join (when that is the fair side),
    then the ladder fit, then the value guards."""
    if fair is not None:
        if fair.get("derived_fixtures", 0) == 0:
            return (
                f"bettingiscool's board ({fair.get('board_fixtures', 0)} fixtures) "
                f"carries no derived corners/cards events, so there is no fair "
                f"price to measure Paddy Power against."
            )
        if fair.get("matched", 0) == 0:
            return (
                f"{fair.get('derived_fixtures')} derived fixture(s) on the board "
                f"but none matched a Paddy Power fixture by name."
            )
        if fair.get("no_totals", 0) and not priced.get("ladders_fitted"):
            return (
                f"{fair.get('no_totals')} matched fixture(s) carry no totals, so "
                f"no ladder could be built."
            )
    if priced.get("ladders_fitted", 0) == 0:
        return (
            f"No ladder was fittable — {priced.get('ladders_refused', 0)} refused. "
            f"A fixture needs at least 2 two-sided Pinnacle rungs."
        )
    if priced.get("rows", 0) == 0:
        return (
            f"WORKING: {priced.get('ladders_fitted')} ladder(s) fitted and "
            f"{priced.get('priceable_paddy_lines', 0)} Paddy Power line(s) priced, "
            f"but none cleared the value guards. That is the tool doing its job — "
            f"most lines hold no edge."
        )
    return f"{priced.get('rows')} value row(s) found."


@router.get("/cards/oddspapi-check")
def oddspapi_check(request: Request, book: str | None = None, tournament: int = 17,
                   find: str = "nations,qualif,world cup,euro"):
    """What OddsPapi's live catalogue and lists actually say — read ONCE after
    switching, before trusting a price. Costs up to 3 requests (market
    catalogue, participants, tournaments), each cached for a day after.

    Answers the three things that could not be verified from where the adapter
    was written: which market ids are Paddy's corners/cards and at what lines,
    whether team names resolve, and whether the configured tournament ids are
    the competitions they are meant to be.
    """
    from ..cards.oddspapi import (
        OddsPapiPaddySource, _as_list, _first, _tournament_entries,
        catalogue_index, corner_card_markets, coverage, participant_names,
        resolve_tournaments, tournament_names,
    )
    cards = request.app.state.cards_client
    if not isinstance(cards, OddsPapiPaddySource):
        return {"error": "OddsPapi is not configured — set ODDSPAPI_API_KEY."}
    client = cards._client
    out: dict = {}
    try:
        cat = client.catalogue()
        found = corner_card_markets(cat)
        out["catalogue_entries"] = len(_as_list(cat))
        out["corner_card_markets"] = {
            mid: {"market": k, "line": line, "outcomes": sides}
            for mid, (k, line, sides) in sorted(found.items(), key=lambda x: (x[1][0], x[1][1]))
        }
        # The raw entries behind any three ids, so the field names can be read.
        out["sample_raw_entries"] = [
            m for m in _as_list(cat)
            if str(_first(m, "marketId", "id", "_id")) in ("10803", "10807", "10811")
        ][:3]
    except Exception as exc:
        out["catalogue_error"] = str(exc)
    try:
        out["team_names_known"] = len(participant_names(client.participants()))
    except Exception as exc:
        out["participants_error"] = str(exc)
    try:
        known = tournament_names(client.tournaments())
        ids, unresolved = resolve_tournaments(_tournament_entries(), known)
        out["tournaments"] = {str(i): known.get(i, "NOT IN ODDSPAPI'S LIST") for i in ids}
        out["unresolved_tournaments"] = unresolved
        # Competitions whose name contains any of `find` — for locating an id.
        words = [w.strip().lower() for w in find.split(",") if w.strip()]
        out["tournament_search"] = {
            str(i): n for i, n in sorted(known.items())
            if any(w in n.lower() for w in words)
        }
    except Exception as exc:
        out["tournaments_error"] = str(exc)
    if book:
        # ONE request: this book's whole board for one competition, summarised
        # as two-sided lines per fixture by market type.
        try:
            out["coverage"] = coverage(
                client.odds_by_tournaments([tournament], bookmaker=book),
                catalogue_index(client.catalogue()), book,
            )
            out["coverage"]["tournament"] = tournament
        except Exception as exc:
            out["coverage_error"] = str(exc)
    out["requests_today"] = client.requests_today
    out["last_sweep"] = cards.last_sweep
    return out


@router.get("/cards/diagnostics")
def get_cards_diagnostics(request: Request):
    """One request that says why the screen looks the way it does."""
    return diagnose(
        request.app.state.cards_client,
        getattr(request.app.state, "odds_provider", None),
    )
