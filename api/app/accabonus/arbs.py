"""AccaBonus value/arb finder — de-vigged Pinnacle as fair-line reference.

For each event that has both Pinnacle and Bet365 h2h prices:

1. Remove Pinnacle's margin (vig) to get fair odds across all outcomes
   (2-way normally; 3-way generically when both books price a draw, e.g.
   MMA fight-outcome), using the power method (odds/utils.py::devigd_multi):
     find k so that sum((1/pin_i)^k) = 1, then fair_i = pin_i^k
   (removes more vig from longshots than favourites; margin reported is
   still the raw overround, e.g. 0.025 for 2.5% vig)

2. Measure Bet365's edge vs fair on each outcome:
     edge_i = bet365_i / fair_i - 1                    (positive = value at Bet365)

3. Filter: include rows where best_edge >= min_edge
     min_edge = -0.05  → show everything within 5% of fair value
     min_edge =  0.00  → only rows where Bet365 is at or above fair
     min_edge =  0.02  → Bet365 must beat fair by 2%+

NOTE ON PINNACLE'S ROLE. It is the MEASURING STICK, not a second book to bet
at. The Acca Boost is a bet365 promotion, so bet365 is the only place any of
this is placed; de-vigged Pinnacle is simply what "fair odds" means here, as it
is for the coupon tracker and the cards tool.

Which is why there is no cross-book arb flag. There used to be one — best price
per outcome across both books, sum(1/best) < 1 — and it rendered as the most
prominent badge on the row. It described a bet that cannot be placed: taking it
needs the Pinnacle leg, and this tool exists because the value is at bet365.
A confident badge for an unplaceable bet is worse than no badge.
"""
import re
from dataclasses import dataclass

from ..odds.utils import devigd_multi

# AccaBonus tracker — scans the competitions eligible for Bet365's Acca Boost
# (per the published offer terms, verified June 2026):
#   Tennis        — ATP / WTA / Challenger / Davis Cup / BJK Cup → whole group
#   MMA           — any event → whole group
#   Basketball    — NBA, Euroleague, EuroBasket, Olympics, FIBA World Cup only
#   Am. Football  — NFL only
#   Baseball      — MLB, Japan NPB, Korea KBO only
#   Ice Hockey    — NHL, World Championships, Olympics only
# Cricket and rugby are NOT eligible. Soccer IS eligible for the boost but is
# deliberately excluded here (user decision, June 2026): football is covered
# by the coupon tracker and cards/corners tools — don't re-add it. Pre-season/
# exhibition/All-Star games are excluded by the terms, so *_preseason keys are
# dropped.
#
# TheOddsApi sport keys rotate (per-tournament tennis keys, seasonal leagues),
# so the live list is discovered from the free /v4/sports endpoint
# (TheOddsApiClient.list_sports) and filtered here. Outright/futures keys are
# skipped — only h2h match odds are usable for the de-vig.
ACCABONUS_FULL_GROUPS = {
    "Tennis",
    "Mixed Martial Arts",
}

# Restricted groups: only these key prefixes qualify for the boost.
ACCABONUS_KEY_PREFIXES = (
    "basketball_nba",
    "basketball_euroleague",
    "basketball_eurobasket",
    "basketball_fiba",
    "basketball_olympics",
    "americanfootball_nfl",
    "baseball_mlb",
    "baseball_npb",
    "baseball_kbo",
    "icehockey_nhl",
    "icehockey_world_championship",
    "icehockey_olympics",
)

_OUTRIGHT_RE = re.compile(r"winner|outright", re.IGNORECASE)
# Pre-season/exhibition/All-Star games don't qualify for the boost.
_EXCLUDED_RE = re.compile(r"preseason|pre_season|all_star|exhibition", re.IGNORECASE)

# Used when sport discovery fails (network error). Unknown/off-season keys
# 404, which is free, so over-listing is safe.
FALLBACK_SPORT_KEYS = [
    "tennis_atp",
    "tennis_wta",
    "mma_mixed_martial_arts",
    "basketball_nba",
    "basketball_euroleague",
    "americanfootball_nfl",
    "baseball_mlb",
    "icehockey_nhl",
]


def _key_eligible(key: str, group: str) -> bool:
    if _OUTRIGHT_RE.search(key) or _EXCLUDED_RE.search(key):
        return False
    if group in ACCABONUS_FULL_GROUPS:
        return True
    return key.startswith(ACCABONUS_KEY_PREFIXES)


def select_accabonus_keys(sports: list[dict]) -> list[str]:
    """Filter a /v4/sports listing to active Acca Boost-eligible league keys."""
    keys = [
        s["key"]
        for s in sports
        if s.get("active", True)
        and not s.get("has_outrights", False)
        and _key_eligible(s.get("key", ""), s.get("group", ""))
    ]
    return keys or FALLBACK_SPORT_KEYS


@dataclass
class ArbRow:
    event_id: str
    sport_title: str        # e.g. "EPL", "ATP French Open" — shown on the feed card
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
    best_edge: float        # max edge across all outcomes (incl. draw)
    # Only when both books price a draw (e.g. MMA) — None on 2-way markets:
    bet365_draw: float | None = None
    pinnacle_draw: float | None = None
    fair_draw: float | None = None
    edge_draw: float | None = None


def _get_bm_h2h(event: dict, bm_key: str) -> dict[str, float] | None:
    """Return {outcome_name: price} for bookmaker bm_key's h2h market.
    Outcomes are keyed by name ("Draw" included on 3-way markets). Returns
    None unless both home and away prices are present.
    """
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    for bm in event.get("bookmakers", []):
        if bm["key"] != bm_key:
            continue
        prices: dict[str, float] = {}
        for market in bm.get("markets", []):
            if market["key"] != "h2h":
                continue
            for outcome in market["outcomes"]:
                prices[outcome["name"]] = outcome["price"]
        if home_team in prices and away_team in prices:
            return prices
    return None


def compute_arbs(events: list[dict], min_edge: float) -> list[ArbRow]:
    """Return ArbRows where best_edge >= min_edge, sorted best-edge-first."""
    rows = []
    for event in events:
        b365 = _get_bm_h2h(event, "bet365")
        pin = _get_bm_h2h(event, "pinnacle")
        if b365 is None or pin is None:
            continue

        home, away = event["home_team"], event["away_team"]
        # Draw leg participates only when both books price it.
        outcomes = [home, away]
        if "Draw" in b365 and "Draw" in pin:
            outcomes.append("Draw")

        fair, margin = devigd_multi([pin[o] for o in outcomes])
        fair_by_outcome = dict(zip(outcomes, fair))
        edges = {o: (b365[o] / fair_by_outcome[o]) - 1 for o in outcomes}
        best_edge = max(edges.values())

        if best_edge < min_edge:
            continue

        has_draw = "Draw" in fair_by_outcome
        rows.append(ArbRow(
            event_id=event["id"],
            sport_title=event.get("sport_title", ""),
            home_team=home,
            away_team=away,
            commence_time=event["commence_time"],
            bet365_home=b365[home],
            bet365_away=b365[away],
            pinnacle_home=pin[home],
            pinnacle_away=pin[away],
            fair_home=round(fair_by_outcome[home], 3),
            fair_away=round(fair_by_outcome[away], 3),
            pinnacle_margin=round(margin, 4),
            edge_home=round(edges[home], 4),
            edge_away=round(edges[away], 4),
            best_edge=round(best_edge, 4),
            bet365_draw=b365["Draw"] if has_draw else None,
            pinnacle_draw=pin["Draw"] if has_draw else None,
            fair_draw=round(fair_by_outcome["Draw"], 3) if has_draw else None,
            edge_draw=round(edges["Draw"], 4) if has_draw else None,
        ))

    rows.sort(key=lambda r: r.best_edge, reverse=True)
    return rows
