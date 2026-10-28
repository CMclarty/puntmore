"""The legs a BB Boost bet is logged from: William Hill's own lines on the
target match, by OddsPapi market and outcome id.

Those ids are what /settlements answers in, so a logged bet settles itself
by lookup (hills/settle.py) — no team names to match, no stats to read.
Only full-match corners, cards, goals and saves totals and the team corners /
cards lines are offered, and only half lines (a builder leg never pushes).
"""
from ..cards.feed import market_name
from ..cards.oddspapi import (CARDS, CORNERS, GOALS, SAVES, TEAM_CARDS_AWAY,
                              TEAM_CARDS_HOME, TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME,
                              _price)
from ..pricing.counts.lines import is_half_line

OFFERED = (CORNERS, CARDS, GOALS, SAVES, TEAM_CORNERS_HOME, TEAM_CORNERS_AWAY,
           TEAM_CARDS_HOME, TEAM_CARDS_AWAY)


def _label(key: str, side: str, line: float, home: str, away: str) -> str:
    kind = market_name(key)
    if kind.startswith("team_"):
        team = home if key.endswith("_home") else away
        return f"{team} {side} {line:.1f} {kind.split('_', 1)[1]}"
    return f"{side} {line:.1f} {kind}"


def legs_for(source, fixture_id: str, home: str, away: str) -> list[dict]:
    """[{market_id, outcome_id, key, market, side, line, label}] for every line
    William Hill quotes on the fixture in the source's last sweep."""
    fx = (getattr(source, "boards", None) or {}).get(str(fixture_id))
    if not fx:
        return []
    odds = fx.get("bookmakerOdds") or {}
    book_slug = getattr(source, "priced_book", source.book)
    book = odds.get(book_slug) or odds.get(book_slug.split("+")[0]) or {}
    out = []
    for mid, market in (book.get("markets") or {}).items():
        known = source.market_index.get(str(mid))
        if known is None or market.get("marketActive") is False:
            continue
        key, line, sides = known
        if key not in OFFERED or line is None or not is_half_line(line):
            continue
        for oid, outcome in (market.get("outcomes") or {}).items():
            side = sides.get(str(oid))
            if side not in ("Over", "Under") or not _price(outcome):
                continue
            out.append({"market_id": str(mid), "outcome_id": str(oid), "key": key,
                        "market": market_name(key), "side": side, "line": line,
                        "label": _label(key, side, line, home, away)})
    out.sort(key=lambda leg: (OFFERED.index(leg["key"]), leg["line"], leg["side"]))
    return out


def by_id(legs: list[dict]) -> dict[tuple[str, str], dict]:
    return {(leg["market_id"], leg["outcome_id"]): leg for leg in legs}
