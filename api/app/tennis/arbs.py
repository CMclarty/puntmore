"""Tennis value/arb finder — de-vigged Pinnacle as fair-line reference.

For each tennis event that has both Pinnacle and Bet365 h2h prices:

1. Remove Pinnacle's margin (vig) to get fair odds:
     total_impl  = 1/pin_home + 1/pin_away       (e.g. 1.025 for 2.5% vig)
     fair_home   = pin_home  * total_impl          (scales odds down to fair)
     fair_away   = pin_away  * total_impl

2. Measure Bet365's edge vs fair:
     edge_home = bet365_home / fair_home - 1      (positive = value at Bet365)
     edge_away = bet365_away / fair_away - 1

3. Filter: include rows where best_edge >= min_edge
     min_edge = -0.05  → show everything within 5% of fair value
     min_edge =  0.00  → only rows where Bet365 is at or above fair
     min_edge =  0.02  → Bet365 must beat fair by 2%+

4. is_arb: true when a guaranteed cross-book profit exists
     i.e. 1/bet365_home + 1/pin_away < 1.0  OR
          1/bet365_away + 1/pin_home < 1.0
"""
from dataclasses import dataclass

from ..odds.utils import devigd as _devigd_shared

# All sports covered by Bet365's AccaBonus feature, excluding soccer.
# Pinnacle must offer h2h odds for a sport to be usable — if it doesn't,
# every event from that sport key will be silently skipped by compute_arbs.
# Off-season sport keys return 404 (free, no quota cost) so it's safe to
# include all year-round even when most are inactive.
SPORT_KEYS = [
    # Tennis
    "tennis_atp",
    "tennis_wta",
    "tennis_atp_french_open",
    "tennis_wta_french_open",
    "tennis_grand_slam_winner",
    # Basketball
    "basketball_nba",
    "basketball_euroleague",
    # Baseball
    "baseball_mlb",
    # American Football
    "americanfootball_nfl",
    # Ice Hockey
    "icehockey_nhl",
    # Cricket
    "cricket_test_match",
    "cricket_odi",
    "cricket_t20",
    # Rugby
    "rugbyleague_nrl",
    "rugbyunion_premiership",
]


@dataclass
class ArbRow:
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    bet365_home: float
    bet365_away: float
    pinnacle_home: float
    pinnacle_away: float
    fair_home: float        # de-vigged Pinnacle home odds
    fair_away: float        # de-vigged Pinnacle away odds
    pinnacle_margin: float  # Pinnacle's vig (e.g. 0.025 = 2.5%)
    edge_home: float        # bet365_home/fair_home - 1 (positive = value)
    edge_away: float        # bet365_away/fair_away - 1
    best_edge: float        # max(edge_home, edge_away)
    is_arb: bool            # guaranteed cross-book profit possible


def _get_bm_h2h(event: dict, bm_key: str) -> tuple[float, float] | None:
    """Return (home_price, away_price) for bookmaker bm_key, or None if unavailable."""
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    for bm in event.get("bookmakers", []):
        if bm["key"] != bm_key:
            continue
        home_price = away_price = None
        for market in bm.get("markets", []):
            if market["key"] != "h2h":
                continue
            for outcome in market["outcomes"]:
                if outcome["name"] == home_team:
                    home_price = outcome["price"]
                elif outcome["name"] == away_team:
                    away_price = outcome["price"]
        if home_price is not None and away_price is not None:
            return (home_price, away_price)
    return None


def _devigd(pin_home: float, pin_away: float) -> tuple[float, float, float]:
    return _devigd_shared(pin_home, pin_away)


def compute_arbs(events: list[dict], min_edge: float) -> list[ArbRow]:
    """Return ArbRows where best_edge >= min_edge, sorted best-edge-first."""
    rows = []
    for event in events:
        b365 = _get_bm_h2h(event, "bet365")
        pin = _get_bm_h2h(event, "pinnacle")
        if b365 is None or pin is None:
            continue

        b365_home, b365_away = b365
        pin_home, pin_away = pin

        fair_home, fair_away, margin = _devigd(pin_home, pin_away)

        edge_home = (b365_home / fair_home) - 1
        edge_away = (b365_away / fair_away) - 1
        best_edge = max(edge_home, edge_away)

        if best_edge < min_edge:
            continue

        is_arb = (
            (1 / b365_home) + (1 / pin_away) < 1.0
            or (1 / b365_away) + (1 / pin_home) < 1.0
        )

        rows.append(ArbRow(
            event_id=event["id"],
            home_team=event["home_team"],
            away_team=event["away_team"],
            commence_time=event["commence_time"],
            bet365_home=b365_home,
            bet365_away=b365_away,
            pinnacle_home=pin_home,
            pinnacle_away=pin_away,
            fair_home=round(fair_home, 3),
            fair_away=round(fair_away, 3),
            pinnacle_margin=round(margin, 4),
            edge_home=round(edge_home, 4),
            edge_away=round(edge_away, 4),
            best_edge=round(best_edge, 4),
            is_arb=is_arb,
        ))

    rows.sort(key=lambda r: r.best_edge, reverse=True)
    return rows
