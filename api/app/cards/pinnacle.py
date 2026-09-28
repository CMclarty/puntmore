"""Pinnacle fair odds for corners and cards, off the bettingiscool board.

THE FALLBACK FAIR SIDE. By default Pinnacle's corners/cards come from OddsPapi
on the same fixture id as Paddy Power's (cards/oddspapi.py). This module is
used only for an event that arrives without a Pinnacle book, or for every
event when ODDSPAPI_FAIR_BOOK is blank.

WHAT THIS ASSUMES, AND IT IS NOT YET CONFIRMED. The bettingiscool board is
known to CARRY derived-market fixtures — the coupon provider filters them out
by name so they cannot be matched to a coupon leg (EXCLUDED_LEAGUE_RE) — but
whether those fixtures carry priceable totals has not been verified against the
live API. Until it is, this module reports
what it found rather than failing quietly: if the board has no derived fixtures,
or they carry no totals, `fair_markets` returns nothing and says so ONCE per
sweep, naming the cause. A corners tool that shows an empty feed because its
fair side is missing must not look like a tool that found no value.

RAW PRICES ARE RETURNED, NOT FAIR ONES. De-vigging lives in cards/value.py and
must stay there: it already holds both sides of every line, and a second de-vig
path here would either drift from it or — worse — be applied on top of it, since
value.py de-vigs whatever Pinnacle prices it is handed. Same rule as
pricing/legs.py being one implementation. This module's job is to FIND the
prices; valuing them is not its business.
"""
import logging
import re

from ..normalise import team_similarity

logger = logging.getLogger("uvicorn.error")

# Counts from the most recent call, for /cards/diagnostics.
LAST_RUN: dict = {}

# How the board labels a derived fixture. Matched on the league name AND the
# runner names, because the vendor marks them in both places.
CORNERS_RE = re.compile(r"\bcorners?\b", re.IGNORECASE)
CARDS_RE = re.compile(r"\b(cards?|bookings?)\b", re.IGNORECASE)

# The market key each derived board maps onto, so the rest of the tool speaks
# TheOddsApi's vocabulary and `value.py` needs no changes.
CORNERS_MARKET = "alternate_totals_corners"
CARDS_MARKET = "alternate_totals_cards"

FULL_MATCH_PERIOD = 0
# Same bar as coupon fixture matching: both sides must stand up independently.
MATCH_THRESHOLD = 0.72


def _strip_marker(name: str) -> str:
    """"Arsenal (Corners)" -> "Arsenal", so it can be matched to the real tie."""
    return re.sub(r"\s*\((?:corners?|cards?|bookings?)\)\s*", " ", name or "",
                  flags=re.IGNORECASE).strip()


def _kind(row: dict) -> str | None:
    """Which derived market a board row is, or None if it is a normal fixture."""
    blob = " ".join(str(row.get(k, "")) for k in
                    ("league_name", "runner_home", "runner_away"))
    # Cards first: "bookings" and "cards" never co-occur with corners on one
    # fixture, but testing the narrower pattern first keeps it deterministic.
    if CARDS_RE.search(blob):
        return CARDS_MARKET
    if CORNERS_RE.search(blob):
        return CORNERS_MARKET
    return None


def _pair_score(a_sides, b_sides) -> float:
    """The WEAKER of the two side comparisons — both teams must look right."""
    if not a_sides or not b_sides:
        return 0.0
    return min(team_similarity(a_sides[0], b_sides[0]),
               team_similarity(a_sides[1], b_sides[1]))


def _totals(rows: list[dict]) -> dict[float, tuple[float, float]]:
    """Full-match totals as {line: (over, under)} raw prices.

    Index convention is the provider's: 1 = over, 2 = under.
    """
    out: dict[float, tuple[float, float]] = {}
    for r in rows:
        if r.get("period") != FULL_MATCH_PERIOD or r.get("market") != "totals":
            continue
        try:
            line = float(r.get("line"))
            over, under = float(r["odds1"]), float(r["odds2"])
        except (TypeError, ValueError, KeyError):
            continue
        if over > 1.0 and under > 1.0:
            out[line] = (over, under)
    return out


def pinnacle_books(provider, fixtures: list[dict]) -> dict[str, dict]:
    """Pinnacle corners/cards prices per fixture, keyed by TheOddsApi event id.

    `fixtures` are TheOddsApi fixtures ({id, home_team, away_team}) — the ones
    Paddy Power is being priced for. Each is matched to the board's derived
    event by name, on the same per-side comparison coupon matching uses.

    The value is a TheOddsApi-shaped BOOKMAKER dict, so it can be appended to
    the Paddy Power event and read by `value.py` with no changes there. Prices
    are RAW — value.py de-vigs.
    """
    if provider is None or not fixtures:
        return {}

    # Index the board's derived fixtures once, by market kind.
    derived: dict[str, list[tuple[tuple[str, str], dict]]] = {}
    board = provider.board()
    for row in board:
        kind = _kind(row)
        if kind is None:
            continue
        sides = (_strip_marker(row.get("runner_home", "")),
                 _strip_marker(row.get("runner_away", "")))
        if sides[0] and sides[1]:
            derived.setdefault(kind, []).append((sides, row))

    if not derived:
        LAST_RUN.update({
            "board_fixtures": len(board), "derived_fixtures": 0,
            "matched": 0, "unmatched": 0, "no_totals": 0,
        })
        logger.warning(
            "cards: the bettingiscool board (%d fixtures) carries NO derived "
            "corners/cards events, so there is no fair price to measure Paddy "
            "Power against and the feed will be empty. This is a data-source "
            "question, not a value one. The default fair side is OddsPapi (cards/oddspapi.py).",
            len(board),
        )
        return {}

    out: dict[str, dict] = {}
    matched = unmatched = no_totals = 0
    for fixture in fixtures:
        want = (fixture.get("home_team", ""), fixture.get("away_team", ""))
        for market_key, candidates in derived.items():
            best, best_score = None, 0.0
            for sides, row in candidates:
                score = _pair_score(want, sides)
                if score > best_score:
                    best, best_score = row, score
            if best is None or best_score < MATCH_THRESHOLD:
                unmatched += 1
                continue
            matched += 1
            lines = _totals(provider.odds_for(best.get("event_id")))
            if not lines:
                no_totals += 1
                continue
            outcomes = []
            for line, (over, under) in sorted(lines.items()):
                # Both sides of every line, always: value.py de-vigs the pair,
                # and a one-sided line cannot be de-vigged at all.
                outcomes.append({"name": "Over", "point": line, "price": over})
                outcomes.append({"name": "Under", "point": line, "price": under})
            book = out.setdefault(
                fixture["id"], {"key": "pinnacle", "markets": []}
            )
            book["markets"].append({"key": market_key, "outcomes": outcomes})

    LAST_RUN.update({
        "board_fixtures": len(board),
        "derived_fixtures": sum(len(v) for v in derived.values()),
        "matched": matched, "unmatched": unmatched, "no_totals": no_totals,
    })
    logger.info(
        "cards fair side: %d fixture-market(s) matched, %d unmatched, "
        "%d matched but carrying no totals",
        matched, unmatched, no_totals,
    )
    return out
