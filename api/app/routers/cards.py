from fastapi import APIRouter, Query, Request

from ..config import settings
from ..schemas import CardFeedItem, CardFeedBet, CardFeedFair
from ..cards.laddered import compute_laddered_value
from ..cards.most import most_fairs, most_value
from ..cards.saves import saves_value


def price_events(events, min_edge):
    """Value rows from joined events: every line Paddy Power quotes, priced off
    Pinnacle's fitted ladder (or the team split, or Pinnacle's own rung where
    nothing fits — cards/laddered.py). One entry point so the router, the feed
    and the scheduler cannot diverge."""
    rows = compute_laddered_value(events, min_edge)
    # Saves are priced off Bookie Bashing's fair figure, not Pinnacle — a
    # separate path, joined here so every consumer sees them.
    rows += saves_value(events, min_edge)
    # "Most corners / cards": Paddy's price comes from the desktop scraper, the
    # fair from the team split (cards/most.py).
    rows += most_value(events, min_edge)
    rows.sort(key=lambda r: r.edge, reverse=True)
    return rows

def market_name(market_key: str) -> str:
    """corners | cards | goals | saves | team_corners | team_cards, from
    whichever key a vendor used."""
    if market_key.startswith("most_"):
        return market_key
    if market_key.startswith("team_"):
        return "team_corners" if "corners" in market_key else "team_cards"
    for word in ("corners", "cards", "goals", "saves"):
        if word in market_key:
            return word
    return market_key


def describe(bet) -> str:
    """The pick as the page and the Discord alert print it — "Over 9.5 corners",
    or "Arsenal Over 4.5 corners" for a team line, since there the match isn't
    the subject. One decimal always: every line is an x.5 (pushes are never bet).
    """
    if bet.market_key.startswith("most_"):
        # A three-way: side is Home | Tie | Away, there is no line.
        who = {"Home": bet.home_team, "Away": bet.away_team}.get(bet.side, "Tie")
        return f"{who} most {bet.market_key.split('_', 1)[1]}"
    if bet.market_key.startswith("team_"):
        team = bet.home_team if bet.market_key.endswith("_home") else bet.away_team
        kind = "corners" if "corners" in bet.market_key else "cards"
        return f"{team} {bet.side} {bet.line:.1f} {kind}"
    return f"{bet.side} {bet.line:.1f} {market_name(bet.market_key)}"

router = APIRouter()


def build_feed(client, min_edge: float) -> list[CardFeedItem]:
    """Cards/corners feed grouped by match, sorted by best value. Shared with /state."""
    if client is None:
        return []

    events = client.get_events()
    bets = price_events(events, min_edge)

    groups: dict[str, dict] = {}

    def group(row):
        return groups.setdefault(row.event_id, {
            "event_id": row.event_id,
            "match": f"{row.home_team} vs {row.away_team}",
            "commence_time": row.commence_time,
            "sport_key": row.sport_key,
            "bets": [], "fairs": [],
        })

    for bet in bets:
        group(bet)["bets"].append(CardFeedBet(
            pick=describe(bet),
            odds=bet.paddy_odds,
            fair=bet.fair_odds,
            value=bet.edge,
            market=market_name(bet.market_key),
            line=bet.line,
            side=bet.side,
            basis=bet.basis,
        ))

    # Team to have most corners / cards (cards/most.py): where Paddy's price has
    # arrived from the desktop scraper the three-way is already in `bets`;
    # otherwise it rides along as a FAIR-only row, read against the terminal.
    priced = {(b.event_id, b.market_key) for b in bets if b.market_key.startswith("most_")}
    for fair in most_fairs(events):
        if (fair.event_id, fair.market_key) in priced:
            continue
        kind = fair.market_key.split("_", 1)[1]
        for outcome, who in (("home", fair.home_team), ("tie", "Tie"), ("away", fair.away_team)):
            price = fair.fair(outcome)
            if price is not None:
                group(fair)["fairs"].append(CardFeedFair(
                    pick=f"{who} most {kind}", market=fair.market_key,
                    fair=price, basis=fair.basis,
                ))

    result = []
    for g in groups.values():
        g["bets"].sort(key=lambda b: -b.value)
        result.append(CardFeedItem(
            event_id=g["event_id"],
            match=g["match"],
            commence_time=g["commence_time"],
            sport_key=g["sport_key"],
            # A match with only fair three-ways has no value to rank by.
            best_value=max((b.value for b in g["bets"]), default=-1.0),
            bets=g["bets"],
            fairs=g["fairs"],
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
    return build_feed(request.app.state.cards_client, min_edge)


def diagnose(client) -> dict:
    """Why the cards screen is showing what it is showing.

    An empty screen has at least six distinct causes and they look identical
    from the outside — no fixtures near kickoff, Paddy Power not posting the
    market, no Pinnacle side to price against, ladders too thin to fit, or
    simply nothing clearing the value guards. The last of those is a SUCCESS
    and must not be mistaken for a broken tool.

    The counts come from whatever the last sweep actually did, so this reports
    the real pipeline rather than re-running it.
    """
    from ..cards import laddered

    sweep = dict(getattr(client, "last_sweep", {}) or {})
    # The fair side comes from OddsPapi on the same fixture id as Paddy's.
    oddspapi_fair = str(sweep.get("fair_source", "")).startswith("oddspapi")
    priced = dict(getattr(laddered, "LAST_RUN", {}) or {})

    if client is None:
        verdict = (
            "No cards client — ODDSPAPI_API_KEY is not set, so Paddy Power's "
            "prices are never fetched."
        )
    elif not sweep:
        verdict = "No sweep has run yet. Open the feed or wait for the 2-minute tick."
    elif sweep.get("fixtures_in_window", 0) == 0:
        nxt = sweep.get("next_kickoff")
        verdict = (
            f"No fixture kicks off within {sweep.get('window_hours')}h, so nothing "
            f"was fetched. These markets are only posted near kickoff — this is "
            f"the normal state between matches, not a failure. "
            + (f"Next listed kickoff: {nxt}. " if nxt else
               "No upcoming fixture is listed in any covered competition. ")
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
    elif not oddspapi_fair and not priced:
        # No fair side on the sweep AND nothing was ever priced: the legacy
        # TheOddsApi path, or OddsPapi with the fair book unset.
        verdict = (
            "No Pinnacle side to price against. Set ODDSPAPI_FAIR_BOOK — the fair "
            "side comes from OddsPapi on the same fixture id as Paddy Power's."
        )
    else:
        verdict = None          # fair side fine — fall through to the pricing checks
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
        "pricing": priced,
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
    return diagnose(request.app.state.cards_client)
