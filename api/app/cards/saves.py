"""Goalkeeper saves: Paddy Power's line from OddsPapi against a FAIR price
read off Bookie Bashing on Callum's own PC.

WHY TWO PLACES. Pinnacle does not post a saves ladder, so there is nothing to
de-vig here; Bookie Bashing publishes fair saves prices, but behind a login,
so they are read by a script running locally in a logged-in browser
(local/bookiebashing_saves.py) and POSTed to /api/paddy/saves-fair. Nothing
on the server ever holds the Bookie Bashing login.

SCOPE. MATCH totals only (total saves in the game, over/under a line). Per-
goalkeeper saves are a different bet and need the Bookie Bashing page's layout
and OddsPapi's player ids, neither of which has been seen yet.

The fair price is taken as given — it is already de-vigged — so a row's
"pinnacle_odds" and "fair_odds" are both the Bookie Bashing figure and its
margin is 0. Where only one side is sent, the other is its complement.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from ..normalise import team_similarity
from ..ttl_cache import TTLCache
from ..pricing.counts.lines import is_half_line
from .oddspapi import SAVES
from .value import CardValueRow, _get_bookmaker, _get_market, _market_label, _price

# A fair price outlives the game it is for by long enough to settle the
# feed, never longer. 36h covers a price read the day before kickoff.
FAIR_TTL = 36 * 3600
# Hundreds of games a weekend at a handful of lines each; a hard ceiling
# stops a runaway local script filling memory (ttl_cache.py).
FAIR_MAXSIZE = 5000
# Same bar the coupon tracker uses to accept a fuzzy team match.
MIN_TEAM_SIMILARITY = 0.80
# A fair price is for the game on that DAY: never join one to a fixture
# more than this far from the kickoff it was sent with.
KICKOFF_TOLERANCE_HOURS = 12


@dataclass
class FairSaves:
    home: str
    away: str
    kickoff: datetime
    line: float
    over: float      # fair decimal odds
    under: float


STORE = TTLCache(ttl=FAIR_TTL, maxsize=FAIR_MAXSIZE)


def complete(over: float | None, under: float | None) -> tuple[float, float] | None:
    """Both fair prices, the missing one as the complement. None if neither,
    or if the two given are not a fair pair (more than 2% off summing to 1)."""
    if over and under:
        if abs(1 / over + 1 / under - 1) > 0.02:
            return None
        return over, under
    one = over or under
    if not one or one <= 1:
        return None
    other = 1 / (1 - 1 / one)
    return (one, other) if over else (other, one)


def store(entries: list[FairSaves]) -> None:
    for e in entries:
        STORE.set((e.home.lower(), e.away.lower(), e.kickoff.date(), e.line), e)


def _parse_time(raw: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _fair_for(event: dict, entries: list[FairSaves]) -> dict[float, FairSaves]:
    """The fair lines for one fixture: best team match, on the right day."""
    start = _parse_time(event.get("commence_time", ""))
    best: dict[float, tuple[float, FairSaves]] = {}
    for e in entries:
        if start and abs((e.kickoff - start).total_seconds()) > KICKOFF_TOLERANCE_HOURS * 3600:
            continue
        score = min(team_similarity(e.home, event.get("home_team", "")),
                    team_similarity(e.away, event.get("away_team", "")))
        if score < MIN_TEAM_SIMILARITY:
            continue
        if e.line not in best or score > best[e.line][0]:
            best[e.line] = (score, e)
    return {line: e for line, (_, e) in best.items()}


def saves_value(events: list[dict], min_edge: float, entries=None) -> list[CardValueRow]:
    """Value rows for every saves line Paddy prices that has a fair price."""
    entries = STORE.values() if entries is None else entries
    if not entries:
        return []
    rows = []
    for event in events:
        paddy = _get_bookmaker(event, "paddypower")
        if paddy is None:
            continue
        outcomes = _get_market(paddy, SAVES)
        if not outcomes:
            continue
        for line, fair in _fair_for(event, entries).items():
            if not is_half_line(line):      # pushes are never bet
                continue
            for side, fair_odds in (("Over", fair.over), ("Under", fair.under)):
                paddy_price = _price(outcomes, side, line)
                if paddy_price is None:
                    continue
                edge = round(paddy_price / fair_odds - 1, 4)
                if edge < min_edge:
                    continue
                rows.append(CardValueRow(
                    event_id=event["id"],
                    home_team=event.get("home_team", ""),
                    away_team=event.get("away_team", ""),
                    commence_time=event.get("commence_time", ""),
                    sport_key=event.get("sport_key", ""),
                    market_key=SAVES,
                    market_label=_market_label("saves", side, line),
                    line=line,
                    side=side,
                    paddy_odds=round(paddy_price, 3),
                    pinnacle_odds=round(fair_odds, 3),
                    fair_odds=round(fair_odds, 3),
                    pinnacle_margin=0.0,
                    edge=edge,
                    basis="saves",
                ))
    rows.sort(key=lambda r: r.edge, reverse=True)
    return rows
