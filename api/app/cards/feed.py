"""The Paddy (cards & corners) value feed — importable without FastAPI.

Moved out of routers/cards.py (Oct 2026) so the router, /state, the scheduler,
and the cross-tool value feed (app/value/) all call one
implementation. Same behaviour as before the move.
"""
from ..schemas import CardFeedItem, CardFeedBet, CardFeedFair
from .laddered import compute_laddered_value
from .most import most_fairs, most_value
from .oddspapi import BOOK_LABELS, PADDY_POWER
from .saves import saves_value


def price_events(events, min_edge, book=PADDY_POWER):
    """Value rows from joined events: every line `book` (Paddy Power, or
    William Hill for Hills) quotes, priced off Pinnacle's fitted ladder (or the
    team split, or Pinnacle's own rung where nothing fits — cards/laddered.py).
    One entry point so the router, the feed and the scheduler cannot diverge."""
    rows = compute_laddered_value(events, min_edge, book)
    # Saves are priced off Bookie Bashing's fair figure, not Pinnacle — a
    # separate path, joined here so every consumer sees them.
    rows += saves_value(events, min_edge, book=book)
    # "Most corners / cards": Paddy's price comes from the desktop scraper, the
    # fair from the team split (cards/most.py). Paddy's only: the scraper
    # reads paddypower.com, so its prices must never sit on another book.
    if book == PADDY_POWER:
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


def build_feed(client, min_edge: float) -> list[CardFeedItem]:
    """Cards/corners feed grouped by match, sorted by best value. Shared with /state."""
    if client is None:
        return []

    book = getattr(client, "book", PADDY_POWER)
    events = client.get_events()
    bets = price_events(events, min_edge, book)

    groups: dict[str, dict] = {}

    def group(row):
        return groups.setdefault(row.event_id, {
            "event_id": row.event_id,
            "match": f"{row.home_team} vs {row.away_team}",
            "home": row.home_team,
            "away": row.away_team,
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
    # Paddy's only, like the priced rows: they are read against Paddy's terminal.
    priced = {(b.event_id, b.market_key) for b in bets if b.market_key.startswith("most_")}
    for fair in (most_fairs(events) if book == PADDY_POWER else []):
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
            home=g["home"],
            away=g["away"],
            commence_time=g["commence_time"],
            sport_key=g["sport_key"],
            # A match with only fair three-ways has no value to rank by.
            best_value=max((b.value for b in g["bets"]), default=-1.0),
            bets=g["bets"],
            fairs=g["fairs"],
        ))

    result.sort(key=lambda x: -x.best_value)
    return result


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
    from . import laddered

    sweep = dict(getattr(client, "last_sweep", {}) or {})
    # The fair side comes from OddsPapi on the same fixture id as the book's.
    oddspapi_fair = str(sweep.get("fair_source", "")).startswith("oddspapi")
    book = getattr(client, "book", PADDY_POWER)
    name = BOOK_LABELS.get(book, book)
    priced = dict(laddered.LAST_RUNS.get(book) or {})

    if client is None:
        verdict = (
            "No cards client — ODDSPAPI_API_KEY is not set, so the bookmaker's "
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
    elif sweep.get("carried_paddy_prices", 0) == 0 and any(
            e.startswith(book) and "FIXTURE_NOT_FOUND" in e for e in sweep.get("errors") or []):
        # OddsPapi answers "no fixtures" for the book across whole competitions:
        # it has no odds of any kind for it, not even the match result. Their
        # feed is empty, which is not the book declining to post the markets.
        verdict = (
            f"OddsPapi has no {name} odds at all on these competitions, not even "
            f"match result, though it lists {sweep.get('called')} fixture(s) "
            f"from other books. That's OddsPapi's {name} feed being empty, not "
            f"{name} skipping corners/cards. Raise it with OddsPapi (the plan "
            f"does include {book})."
        )
    elif sweep.get("carried_paddy_prices", 0) == 0:
        verdict = (
            f"{sweep.get('called')} fixture(s) called and NONE carried {name} "
            f"corners/cards prices. {name} is not posting these markets on "
            f"these fixtures yet."
            + ("" if book == PADDY_POWER else
               f" If that never changes, check the OddsPapi plan covers {book}: "
               f"/oddspapi-check?book={book}.")
        )
    elif oddspapi_fair and sweep.get("carried_pinnacle_prices", 0) == 0:
        errs = [e for e in sweep.get("errors") or [] if not e.startswith(book)]
        verdict = (
            f"{name} priced {sweep.get('carried_paddy_prices')} fixture(s), but "
            f"Pinnacle ({sweep.get('fair_source', '').split(':', 1)[-1]}) came back "
            + (f"with an error: {errs[0][:240]}" if errs else
               "with no corners/cards/goals lines on any of them — nothing to price against.")
        )
    elif not oddspapi_fair and not priced:
        # No fair side on the sweep AND nothing was ever priced: the legacy
        # TheOddsApi path, or OddsPapi with the fair book unset.
        verdict = (
            "No Pinnacle side to price against. Set ODDSPAPI_FAIR_BOOK — the fair "
            f"side comes from OddsPapi on the same fixture id as {name}'s."
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
            f"{priced.get('priceable_paddy_lines', 0)} {name} line(s) priced, "
            f"but none cleared the value guards. That is the tool doing its job — "
            f"most lines hold no edge."
        )
    elif verdict is None:
        verdict = f"{priced.get('rows')} value row(s) found."

    # The scheduler's sweep is Paddy's; another book's page sweeps on load.
    from ..scheduler import LAST_CARDS_ERROR as _last
    LAST_CARDS_ERROR = _last if book == PADDY_POWER else {}
    if LAST_CARDS_ERROR:
        verdict = f"The last sweep FAILED before pricing: {LAST_CARDS_ERROR['error']}"
    return {
        "verdict": verdict,
        "sweep_error": dict(LAST_CARDS_ERROR) or None,
        "sweep": sweep,
        "pricing": priced,
    }
