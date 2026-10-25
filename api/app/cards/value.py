"""De-vig + value computation for cards/corners O/U markets.

De-vig (same shared power-method de-vig as accabonus/arbs.py — see
odds/utils.py::devigd):
    find k so that (1/pin_over)^k + (1/pin_under)^k = 1
    fair_over  = pin_over  ** k
    fair_under = pin_under ** k
    margin     = (1/pin_over + 1/pin_under) - 1   (raw overround)
    edge       = paddy_price / fair_price - 1
"""
from dataclasses import dataclass

from ..odds.utils import devigd as _devigd_shared
from ..pricing.counts.lines import is_half_line


@dataclass
class CardValueRow:
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    sport_key: str
    market_key: str       # raw API key, e.g. "alternate_totals"
    market_label: str     # human label, e.g. "O3.5 (alternate_totals)"
    line: float
    side: str             # "Over" | "Under"
    paddy_odds: float
    pinnacle_odds: float  # matched path: raw Pinnacle price before de-vig; ladder
                          # rows carry the model's fair here, saves rows Bookie
                          # Bashing's (no raw quote exists on those paths)
    fair_odds: float      # de-vigged Pinnacle
    pinnacle_margin: float
    edge: float           # paddy_odds / fair_odds - 1


def _get_bookmaker(event: dict, bm_key: str) -> dict | None:
    for bm in event.get("bookmakers", []):
        if bm["key"] == bm_key:
            return bm
    return None


def _get_market(bm: dict, market_key: str) -> list[dict]:
    for m in bm.get("markets", []):
        if m["key"] == market_key:
            return m.get("outcomes", [])
    return []


def _price(outcomes: list[dict], side: str, line: float) -> float | None:
    """Return price for (Over|Under, line) from a market's outcomes list."""
    for o in outcomes:
        if o.get("name", "").lower() != side.lower():
            continue
        try:
            if abs(float(o.get("point", -1)) - line) < 0.05:
                return float(o["price"])
        except (ValueError, TypeError):
            continue
    return None


def _pinnacle_lines(outcomes: list[dict]) -> set[float]:
    """Collect all unique line values present in a Pinnacle market."""
    lines = set()
    for o in outcomes:
        try:
            lines.add(float(o["point"]))
        except (KeyError, ValueError, TypeError):
            continue
    return lines


def _devigd(pin_over: float, pin_under: float) -> tuple[float, float, float]:
    return _devigd_shared(pin_over, pin_under)


def _market_label(market_key: str, side: str, line: float) -> str:
    prefix = "O" if side == "Over" else "U"
    line_str = str(int(line)) if line == int(line) else str(line)
    return f"{prefix}{line_str} ({market_key})"


def compute_value_bets(
    events: list[dict],
    min_edge: float,
    market_keys: list[str] | None = None,
) -> list[CardValueRow]:
    """Return CardValueRows where edge >= min_edge, sorted best-edge-first."""
    from .client import CARDS_MARKET_KEYS
    if market_keys is None:
        from .oddspapi import (TEAM_CARDS_AWAY, TEAM_CARDS_HOME,
                               TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME)
        market_keys = [*CARDS_MARKET_KEYS, TEAM_CORNERS_HOME, TEAM_CORNERS_AWAY,
                       TEAM_CARDS_HOME, TEAM_CARDS_AWAY]

    rows: list[CardValueRow] = []

    for event in events:
        pin = _get_bookmaker(event, "pinnacle")
        paddy = _get_bookmaker(event, "paddypower")
        if pin is None or paddy is None:
            continue

        sport_key = event.get("sport_key", "")

        for mkey in market_keys:
            pin_outcomes = _get_market(pin, mkey)
            paddy_outcomes = _get_market(paddy, mkey)
            if not pin_outcomes or not paddy_outcomes:
                continue

            for line in _pinnacle_lines(pin_outcomes):
                if not is_half_line(line):      # pushes are never bet
                    continue
                pin_over = _price(pin_outcomes, "Over", line)
                pin_under = _price(pin_outcomes, "Under", line)
                if pin_over is None or pin_under is None:
                    continue

                fair_over, fair_under, margin = _devigd(pin_over, pin_under)

                for side, pin_raw, fair in (
                    ("Over",  pin_over,  fair_over),
                    ("Under", pin_under, fair_under),
                ):
                    paddy_price = _price(paddy_outcomes, side, line)
                    if paddy_price is None:
                        continue

                    edge = round((paddy_price / fair) - 1, 4)
                    if edge < min_edge:
                        continue

                    rows.append(CardValueRow(
                        event_id=event["id"],
                        home_team=event.get("home_team", ""),
                        away_team=event.get("away_team", ""),
                        commence_time=event.get("commence_time", ""),
                        sport_key=sport_key,
                        market_key=mkey,
                        market_label=_market_label(mkey, side, line),
                        line=line,
                        side=side,
                        paddy_odds=round(paddy_price, 3),
                        pinnacle_odds=round(pin_raw, 3),
                        fair_odds=round(fair, 3),
                        pinnacle_margin=round(margin, 4),
                        edge=edge,
                    ))

    rows.sort(key=lambda r: r.edge, reverse=True)
    return rows
