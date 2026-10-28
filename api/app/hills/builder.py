"""BB Boost: the bet builders that can be made on Hills' target match.

OddsPapi carries no bet builder prices, so this is deliberately an imperfect
tool (Callum): it lists the combinations and their FAIR price; the page takes
William Hill's builder price off the app on a wheel and values it with the
boost (on winnings: a 25% boost pays 1 + (price - 1) * 1.25).

The legs are every line William Hill quotes on the match, priced as the Paddy
pipeline prices them (cards/feed.price_events at no value floor). A builder
takes at most ONE leg per stat — corners (match or team), cards (match or
team), goals, saves — and the fair price multiplies the legs' fairs, treating
different stats as independent. Two lines of one stat are strongly correlated
(Over 9.5 corners and Arsenal Over 4.5 corners mostly win together), which a
product would misprice badly, so those are never combined. Across stats the
correlation is weaker but real: the fair price here is a guide, not a quote.

`singles` is the product of William Hill's single prices on the same legs. A
builder price is usually cut below it; ranking by singles / fair puts the
legs William Hill prices most generously first.
"""
from itertools import combinations, product
from math import prod

from ..cards.feed import describe, market_name, price_events
from ..cards.oddspapi import WILLIAM_HILL

FAMILY = {"corners": "corners", "team_corners": "corners",
          "cards": "cards", "team_cards": "cards", "goals": "goals", "saves": "saves"}
MIN_LEGS, MAX_LEGS = 2, 4
PER_STAT = 12            # legs kept per stat (best value first): 12^4 ≈ 21k four-leg builders
LIMIT = 200              # combinations returned


def legs_for(events: list[dict], fixture_id: str) -> list:
    """William Hill's priced lines on the target fixture (every one, no floor)."""
    event = next((e for e in events if str(e.get("id")) == str(fixture_id)), None)
    if event is None:
        return []
    return price_events([event], -1.0, WILLIAM_HILL)


def combos(rows, min_fair: float = 1.0, limit: int = LIMIT) -> list[dict]:
    """Every 2–4 leg builder with one leg per stat and fair >= min_fair, the
    best singles/fair first."""
    by_stat: dict[str, list] = {}
    for r in rows:
        stat = FAMILY.get(market_name(r.market_key))
        if stat:
            by_stat.setdefault(stat, []).append(r)
    for stat, legs in by_stat.items():
        legs.sort(key=lambda r: -r.edge)
        del legs[PER_STAT:]
    out = []
    stats = sorted(by_stat)
    for n in range(MIN_LEGS, min(MAX_LEGS, len(stats)) + 1):
        for chosen in combinations(stats, n):
            for legs in product(*(by_stat[s] for s in chosen)):
                fair = prod(r.fair_odds for r in legs)
                if fair < min_fair:
                    continue
                singles = prod(r.paddy_odds for r in legs)
                out.append((singles / fair, fair, singles, legs))
    out.sort(key=lambda c: -c[0])
    return [{
        "legs": [{"pick": describe(r), "market": market_name(r.market_key),
                  "odds": r.paddy_odds, "fair": round(r.fair_odds, 3)} for r in legs],
        "fair": round(fair, 2),
        "singles": round(singles, 2),
    } for _, fair, singles, legs in out[:limit]]
