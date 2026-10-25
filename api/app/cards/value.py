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
    # How the fair price was reached: ladder (fitted Pinnacle ladder), hybrid
    # (a team rung + the match ladder's shape), matched (Pinnacle's own rung,
    # de-vigged), saves (Bookie Bashing's figure).
    basis: str = "ladder"


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


def _devigd(pin_over: float, pin_under: float) -> tuple[float, float, float]:
    return _devigd_shared(pin_over, pin_under)


def _market_label(market_key: str, side: str, line: float) -> str:
    prefix = "O" if side == "Over" else "U"
    line_str = str(int(line)) if line == int(line) else str(line)
    return f"{prefix}{line_str} ({market_key})"
