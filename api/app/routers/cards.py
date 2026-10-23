from fastapi import APIRouter, Query, Request

from ..config import settings
from ..schemas import CardValueOut, CardFeedItem, CardFeedBet
from ..cards.value import compute_value_bets
from ..cards.laddered import compute_laddered_value
from ..cards.client import merge_pinnacle
from ..cards.saves import saves_value


def price_events(events, min_edge):
    """Value rows from joined events, by whichever pricing path is configured.

    The laddered path prices every line Paddy Power quotes off Pinnacle's fitted
    ladder; the matched-line path compares only lines both books picked. One
    entry point so the router, the feed and the scheduler cannot diverge on
    which is in use.
    """
    if settings.cards_use_ladder_model:
        rows = compute_laddered_value(events, min_edge)
    else:
        rows = compute_value_bets(events, min_edge)
    # Saves are priced off Bookie Bashing's fair figure, not Pinnacle — a
    # separate path, joined here so every consumer sees them.
    rows += saves_value(events, min_edge)
    rows.sort(key=lambda r: r.edge, reverse=True)
    return rows

def market_name(market_key: str) -> str:
    """corners | cards | goals | saves | team_corners | team_cards, from
    whichever key a vendor used."""
    if market_key.startswith("team_"):
        return "team_corners" if "corners" in market_key else "team_cards"
    for word in ("corners", "cards", "goals", "saves"):
        if word in market_key:
            return word
    return market_key

router = APIRouter()


@router.get("/value-bets", response_model=list[CardValueOut])
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
                "sport_key": bet.sport_key,
                "bets": [],
            }
        market = market_name(bet.market_key)
        # One decimal always: a whole line can PUSH (exactly N), so "O7.0" must
        # not read like a truncated "O7.5" — the x.5 lines never push.
        if bet.market_key.startswith("team_"):
            # One team's line — name the team, since the match isn't the subject.
            team = bet.home_team if bet.market_key.endswith("_home") else bet.away_team
            kind = "corners" if "corners" in bet.market_key else "cards"
            pick = f"{team} {bet.side} {bet.line:.1f} {kind}"
        else:
            pick = f"{bet.side} {bet.line:.1f} {market}"
        groups[bet.event_id]["bets"].append(CardFeedBet(
            pick=pick,
            odds=bet.paddy_odds,
            value=bet.edge,
            market=market,
        ))

    result = []
    for g in groups.values():
        g["bets"].sort(key=lambda b: -b.value)
        result.append(CardFeedItem(
            event_id=g["event_id"],
            match=g["match"],
            commence_time=g["commence_time"],
            sport_key=g["sport_key"],
            best_value=max(b.value for b in g["bets"]),
            bets=g["bets"],
        ))

    result.sort(key=lambda x: -x.best_value)
    return result


@router.get("/feed", response_model=list[CardFeedItem])
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
    # Pinnacle from OddsPapi (same fixture ids) rather than bettingiscool.
    oddspapi_fair = str(sweep.get("fair_source", "")).startswith("oddspapi")
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
    elif oddspapi_fair and sweep.get("carried_pinnacle_prices", 0) == 0:
        errs = [e for e in sweep.get("errors") or [] if not e.startswith("paddypower")]
        verdict = (
            f"Paddy Power priced {sweep.get('carried_paddy_prices')} fixture(s), but "
            f"Pinnacle ({sweep.get('fair_source', '').split(':', 1)[-1]}) came back "
            + (f"with an error: {errs[0][:240]}" if errs else
               "with no corners/cards/goals lines on any of them — nothing to price against.")
        )
    elif oddspapi_fair:
        verdict = None          # fair side fine — fall through to the pricing checks
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
    else:
        verdict = None
    # Both books in hand: what the pricing made of them.
    if verdict is None and priced.get("ladders_fitted", 0) == 0:
        verdict = (
            f"No ladder was fittable — {priced.get('ladders_refused', 0)} refused. "
            f"A fixture needs at least 2 two-sided Pinnacle rungs."
        )
    elif verdict is None and priced.get("rows", 0) == 0:
        verdict = (
            f"WORKING: {priced.get('ladders_fitted')} ladder(s) fitted and "
            f"{priced.get('priceable_paddy_lines', 0)} Paddy Power line(s) priced, "
            f"but none cleared the value guards. That is the tool doing its job — "
            f"most lines hold no edge."
        )
    elif verdict is None:
        verdict = f"{priced.get('rows')} value row(s) found."

    from ..scheduler import LAST_CARDS_ERROR
    if LAST_CARDS_ERROR:
        verdict = f"The last sweep FAILED before pricing: {LAST_CARDS_ERROR['error']}"
    return {
        "verdict": verdict,
        "sweep_error": dict(LAST_CARDS_ERROR) or None,
        "sweep": sweep,
        "fair_side": fair,
        "pricing": priced,
        "using_ladder_model": settings.cards_use_ladder_model,
    }


@router.get("/probe-books")
def probe_books(
    request: Request,
    region: str = "uk",
    sport: str | None = None,
    event_id: str | None = None,
    markets: str | None = None,
):
    """Which bookmakers and markets TheOddsApi returns for ONE match.

    COSTS CREDITS — one per-event call (markets x regions, so ~2), on demand,
    never on a schedule. The sweep names Paddy Power, so an empty answer there
    cannot say whether Paddy's corners are not up YET, are filed under
    different market keys, or are not in this vendor's feed at all. This asks
    by REGION with no bookmaker filter, so the answer is everything the vendor
    carries for these markets on this match.

    Defaults to the soonest fixture the sweep has in range. `markets` overrides
    the configured keys, to try another spelling.
    """
    cards = request.app.state.cards_client
    raw = getattr(cards, "_client", None)
    if cards is None or raw is None:
        return {"error": "ODDS_API_KEY is not set"}
    if not hasattr(cards, "_due_fixtures"):
        return {"error": "Paddy Power's side is OddsPapi now — this probe is "
                         "TheOddsApi's. Use /api/paddy/oddspapi-check."}

    if event_id is None or sport is None:
        due = cards._due_fixtures()          # FREE: the fixture lists only
        if not due:
            return {"error": "no fixture in range to probe — try nearer a match"}
        sport, fixture, _ = due[0]
        event_id = fixture.get("id")
        match = f"{fixture.get('home_team')} vs {fixture.get('away_team')}"
        kickoff = fixture.get("commence_time")
    else:
        match = kickoff = None

    wanted = markets or settings.cards_market_keys
    try:
        # ttl=0: a cached answer would defeat the point.
        event = raw.fetch_event(sport, event_id, wanted, regions=region, ttl=0)
    except Exception as exc:
        return {"error": str(exc), "sport": sport, "event_id": event_id,
                "markets_requested": wanted}

    books: dict[str, list[str]] = {}
    for bm in (event or {}).get("bookmakers", []):
        books[bm.get("key")] = sorted(m.get("key") for m in bm.get("markets", []))
    paddy = books.get("paddypower")
    return {
        "match": match or f"{event.get('home_team')} vs {event.get('away_team')}",
        "kickoff": kickoff or event.get("commence_time"),
        "sport": sport,
        "region_requested": region,
        "markets_requested": wanted,
        "bookmakers": books,
        "paddypower_present": paddy is not None,
        "paddypower_markets": paddy or [],
        "quota_remaining": getattr(raw, "quota_remaining", None),
        "last_call_cost": getattr(raw, "last_call_cost", None),
        "reading": (
            "Paddy Power listed with markets: the feed carries them — the sweep "
            "should price this match. Other books listed but not Paddy Power: "
            "the vendor carries these markets, not from Paddy (yet). NO books at "
            "all: nobody has posted them for this match yet, or the market keys "
            "are wrong — try again nearer kickoff, or another `markets` spelling."
        ),
    }


@router.get("/shadow")
def shadow_report(days: int = Query(default=14, ge=1, le=30)):
    """How wrong the counts model is, MEASURED, by distance beyond Pinnacle's ladder.

    Hidden Pinnacle rungs predicted from the rest (cards/shadow_store.py). The
    numbers to set the beyond-ladder penalty from. Free: no vendor calls.
    """
    from ..database import SessionLocal
    from ..cards import shadow_store
    db = SessionLocal()
    try:
        return shadow_store.summary(db, days)
    finally:
        db.close()


@router.get("/oddspapi-check")
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
        GOALS, SAVES, catalogue_index, corner_card_markets, coverage,
        fixture_lines, match_total_markets, participant_names,
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
        # Saves and match goals: reported, not priced yet. Lines per kind, and
        # the raw catalogue names behind them so a mis-classification shows.
        extra = match_total_markets(cat, kinds=(SAVES, GOALS))
        names = {str(_first(m, "marketId", "id", "_id")): _first(m, "marketName", "name")
                 for m in _as_list(cat)}
        out["other_total_markets"] = {
            kind: {
                "markets": sum(1 for k, _, _ in extra.values() if k == kind),
                "lines": sorted({line for k, line, _ in extra.values() if k == kind}),
                "names": sorted({str(names.get(mid)) for mid, (k, _, _) in extra.items() if k == kind})[:20],
            }
            for kind in (SAVES, GOALS)
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
            board = client.odds_by_tournaments([tournament], bookmaker=client.resolve_book(book))
            index = catalogue_index(client.catalogue())
            out["coverage"] = coverage(board, index, book)
            out["coverage"]["tournament"] = tournament
            # The same board per fixture (no extra request): ids to quote to
            # the vendor when a match is missing a market the site shows.
            out["fixtures"] = fixture_lines(board, index, book,
                                            participant_names(client.participants()))
        except Exception as exc:
            out["coverage_error"] = str(exc)
    try:
        # Unmetered: the plan's allowance and what this period has used.
        acct = client.account()
        # The plan, not the whole record: it carries the API key and email.
        out["account"] = [
            {k: s.get(k) for k in ("plan", "request_limit", "request_count", "valid_until")}
            | {"bookmakers": sorted((s.get("bookmakers") or {}).keys())}
            for s in acct.get("subscriptions") or [] if s.get("is_active")
        ]
    except Exception as exc:
        out["account_error"] = str(exc)
    out["requests_today"] = client.requests_today
    out["last_sweep"] = cards.last_sweep
    return out


@router.get("/diagnostics")
def get_cards_diagnostics(request: Request):
    """One request that says why the screen looks the way it does."""
    return diagnose(
        request.app.state.cards_client,
        getattr(request.app.state, "odds_provider", None),
    )
