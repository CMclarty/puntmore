"""Team to have the MOST corners / cards — a fair three-way price from Pinnacle.

Neither book exposes "most corners" as a market on OddsPapi (only Corners /
Bookings HANDICAP), so there is no Paddy Power price to value against here —
the shop terminal has one. What the tool can give is the FAIR price to read it
against: P(home > away), P(level), P(away > home) from the two teams' count
distributions, each from Pinnacle's own team line (cards/laddered.py::team_dist,
the hybrid fit where Pinnacle posts a single rung), taken as independent.

Independence is the assumption to remember: corners are not perfectly
independent between the two sides (possession swings), so the level price in
particular is approximate. Pinnacle's corners handicap, where posted, is the
natural check on P(home - away > L) and is the next thing to wire in.
"""
from dataclasses import dataclass

from .laddered import team_dist
from .oddspapi import (TEAM_CARDS_AWAY, TEAM_CARDS_HOME,
                       TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME)
from .value import _get_bookmaker

MOST_CORNERS = "most_corners"
MOST_CARDS = "most_cards"
_PAIRS = {
    MOST_CORNERS: (TEAM_CORNERS_HOME, TEAM_CORNERS_AWAY),
    MOST_CARDS: (TEAM_CARDS_HOME, TEAM_CARDS_AWAY),
}


@dataclass
class FairThreeWay:
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    sport_key: str
    market_key: str       # most_corners | most_cards
    p_home: float
    p_tie: float
    p_away: float
    basis: str            # ladder | hybrid — the weaker of the two team fits

    def fair(self, outcome: str) -> float | None:
        p = {"home": self.p_home, "tie": self.p_tie, "away": self.p_away}[outcome]
        return round(1.0 / p, 2) if p > 1e-9 else None


def three_way(home, away) -> tuple[float, float, float]:
    """(P(home > away), P(equal), P(away > home)) for two independent counts."""
    g = min(home.grid_max, away.grid_max)
    p_home = sum(home.pmf(h) * away.cdf(h - 1) for h in range(1, g + 1))
    p_tie = sum(home.pmf(k) * away.pmf(k) for k in range(0, g + 1))
    p_away = max(0.0, 1.0 - p_home - p_tie)
    return p_home, p_tie, p_away


def most_fairs(events: list[dict]) -> list[FairThreeWay]:
    """One FairThreeWay per fixture and market where Pinnacle prices BOTH teams."""
    out: list[FairThreeWay] = []
    for event in events:
        pin = _get_bookmaker(event, "pinnacle")
        if pin is None:
            continue
        sport_key = event.get("sport_key", "")
        for market_key, (home_key, away_key) in _PAIRS.items():
            home = team_dist(event, pin, home_key, sport_key)
            away = team_dist(event, pin, away_key, sport_key)
            if home is None or away is None:
                continue
            (home_dist, home_basis), (away_dist, away_basis) = home, away
            p_home, p_tie, p_away = three_way(home_dist, away_dist)
            out.append(FairThreeWay(
                event_id=event["id"],
                home_team=event.get("home_team", ""),
                away_team=event.get("away_team", ""),
                commence_time=event.get("commence_time", ""),
                sport_key=sport_key,
                market_key=market_key,
                p_home=p_home, p_tie=p_tie, p_away=p_away,
                basis="hybrid" if "hybrid" in (home_basis, away_basis) else "ladder",
            ))
    return out
