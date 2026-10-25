"""Tests for the cards/corners value helpers and the feed's labels."""
import pytest
from app.cards.value import (
    CardValueRow,
    _devigd,
    _get_bookmaker,
    _get_market,
    _price,
    _market_label,
)


# ---------------------------------------------------------------------------
# _devigd
# ---------------------------------------------------------------------------

def test_devigd_no_vig():
    """Equal prices (50/50) → margin=0, fair equals raw."""
    fair_over, fair_under, margin = _devigd(2.0, 2.0)
    assert abs(margin) < 1e-9
    assert abs(fair_over - 2.0) < 1e-9
    assert abs(fair_under - 2.0) < 1e-9


def test_devigd_typical():
    """Known Pinnacle-style prices → expected de-vigged outputs."""
    # 1/1.90 + 1/1.90 ≈ 1.0526 → margin ≈ 5.26%
    fair_over, fair_under, margin = _devigd(1.90, 1.90)
    assert abs(margin - (1 / 1.90 + 1 / 1.90 - 1)) < 1e-6
    assert abs(fair_over - 2.0) < 0.001
    assert abs(fair_under - 2.0) < 0.001


def test_devigd_asymmetric():
    """Asymmetric lines de-vig correctly (power method)."""
    import math

    pin_over, pin_under = 1.87, 2.05
    fair_over, fair_under, margin = _devigd(pin_over, pin_under)
    total_impl = 1 / pin_over + 1 / pin_under
    # Margin is the raw overround, independent of de-vig method
    assert abs(margin - (total_impl - 1)) < 1e-9
    # Fair probabilities sum to exactly 1
    assert abs(1 / fair_over + 1 / fair_under - 1.0) < 1e-9
    # Power method: both fair prices are price**k for the SAME exponent k
    k_over = math.log(fair_over) / math.log(pin_over)
    k_under = math.log(fair_under) / math.log(pin_under)
    assert abs(k_over - k_under) < 1e-6
    assert k_over > 1  # positive vig → k above 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_market_label_over():
    assert _market_label("alternate_totals", "Over", 3.5) == "O3.5 (alternate_totals)"


def test_market_label_under_whole():
    assert _market_label("alternate_totals", "Under", 10.0) == "U10 (alternate_totals)"


# ---------------------------------------------------------------------------
# the feed's labels (the pricing itself: test_cards_sources.py)
# ---------------------------------------------------------------------------

PIN_CORNERS = {8.5: (1.44, 2.75), 9.5: (1.74, 2.10), 10.5: (2.20, 1.68), 11.5: (2.95, 1.42)}
PIN_CARDS = {2.5: (1.60, 2.30), 3.5: (2.10, 1.72), 4.5: (3.00, 1.40), 5.5: (4.50, 1.20)}


def _mk(key, lines):
    outcomes = []
    for line, (over, under) in sorted(lines.items()):
        outcomes.append({"name": "Over", "point": line, "price": over})
        outcomes.append({"name": "Under", "point": line, "price": under})
    return {"key": key, "outcomes": outcomes}


def test_feed_labels_a_team_market_with_the_team_name():
    """A team line names the team, since the match isn't the subject; the
    away side's market names the away team."""
    from app.routers.cards import build_feed

    class _Client:
        def get_events(self):
            return [{
                "id": "evtT", "home_team": "Arsenal", "away_team": "Chelsea",
                "commence_time": "2026-06-10T15:00:00Z", "sport_key": "soccer_epl",
                "bookmakers": [
                    {"key": "pinnacle", "markets": [
                        _mk("alternate_totals_corners", PIN_CORNERS),
                        _mk("alternate_totals_cards", PIN_CARDS),
                        _mk("team_corners_home", {5.5: (1.90, 1.90)}),
                        _mk("team_cards_away", {2.5: (1.90, 1.90)}),
                    ]},
                    {"key": "paddypower", "markets": [
                        _mk("team_corners_home", {5.5: (2.10, 1.80)}),
                        _mk("team_cards_away", {2.5: (2.10, 1.80)}),
                    ]},
                ],
            }]

    bets = [b for item in build_feed(_Client(), min_edge=-0.5) for b in item.bets]
    corners = next(b for b in bets if b.market == "team_corners" and b.side == "Over")
    assert corners.pick == "Arsenal Over 5.5 corners"
    cards = next(b for b in bets if b.market == "team_cards" and b.side == "Over")
    assert cards.pick == "Chelsea Over 2.5 cards"  # away side → away team
