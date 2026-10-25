"""Team to have the MOST corners / cards — a fair three-way price from Pinnacle.

Neither book exposes "most corners" as a market on OddsPapi (only Corners /
Bookings HANDICAP), so there is no Paddy Power price to value against here —
the shop terminal has one. What the tool can give is the FAIR price to read it
against: P(home > away), P(level), P(away > home) from the team SPLIT of the
fixture's match ladder (pricing/counts/split.py, via cards/laddered.py::
team_split) — one joint law for both sides, with the share pinned by Pinnacle's
two team lines and its handicap, so "level" is priced from the same model as
everything else rather than from two counts assumed independent. A fixture
whose quotes contradict each other on the share is left out.
"""
from dataclasses import dataclass

from .laddered import team_split
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


def most_fairs(events: list[dict]) -> list[FairThreeWay]:
    """One FairThreeWay per fixture and family where the share can be read off
    Pinnacle (a team line or the handicap) and the match ladder has fitted."""
    out: list[FairThreeWay] = []
    for event in events:
        pin = _get_bookmaker(event, "pinnacle")
        if pin is None:
            continue
        sport_key = event.get("sport_key", "")
        for market_key, (home_key, _away_key) in _PAIRS.items():
            split = team_split(event, pin, home_key, sport_key)
            if split is None:
                continue
            _, point, _, share, _ = split
            if share.disagree:
                continue
            p_home, p_tie, p_away = point.three_way()
            out.append(FairThreeWay(
                event_id=event["id"],
                home_team=event.get("home_team", ""),
                away_team=event.get("away_team", ""),
                commence_time=event.get("commence_time", ""),
                sport_key=sport_key,
                market_key=market_key,
                p_home=p_home, p_tie=p_tie, p_away=p_away,
                basis="hybrid",
            ))
    return out
