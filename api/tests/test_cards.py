"""Tests for cards/corners value-bet logic."""
import pytest
from app.cards.value import (
    CardValueRow,
    _devigd,
    _get_bookmaker,
    _get_market,
    _price,
    _market_label,
    compute_value_bets,
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
    """Asymmetric lines de-vig correctly."""
    pin_over, pin_under = 1.87, 2.05
    fair_over, fair_under, margin = _devigd(pin_over, pin_under)
    total_impl = 1 / pin_over + 1 / pin_under
    assert abs(fair_over - pin_over * total_impl) < 1e-9
    assert abs(fair_under - pin_under * total_impl) < 1e-9
    assert abs(margin - (total_impl - 1)) < 1e-9


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_market_label_over():
    assert _market_label("alternate_totals", "Over", 3.5) == "O3.5 (alternate_totals)"


def test_market_label_under_whole():
    assert _market_label("alternate_totals", "Under", 10.0) == "U10 (alternate_totals)"


# ---------------------------------------------------------------------------
# compute_value_bets
# ---------------------------------------------------------------------------

def _make_event(pin_over, pin_under, paddy_over, paddy_under, line=3.5):
    """Build a minimal event dict with both Pinnacle and Paddy Power."""
    def outcomes(over_price, under_price):
        return [
            {"name": "Over",  "point": line, "price": over_price},
            {"name": "Under", "point": line, "price": under_price},
        ]

    return {
        "id": "evt1",
        "home_team": "Team A",
        "away_team": "Team B",
        "commence_time": "2026-06-10T15:00:00Z",
        "sport_key": "soccer_epl",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [{"key": "alternate_totals", "outcomes": outcomes(pin_over, pin_under)}],
            },
            {
                "key": "paddypower",
                "markets": [{"key": "alternate_totals", "outcomes": outcomes(paddy_over, paddy_under)}],
            },
        ],
    }


def test_compute_value_bets_returns_value_rows():
    """Paddy Power over-priced vs fair → row returned."""
    # Pinnacle: 1.90/1.90 → fair ≈ 2.0/2.0; Paddy: 2.10 over → edge ≈ +5%
    events = [_make_event(1.90, 1.90, 2.10, 1.80)]
    rows = compute_value_bets(events, min_edge=-0.10)
    assert any(r.side == "Over" and r.edge > 0 for r in rows)


def test_compute_value_bets_filters_by_min_edge():
    """Rows below min_edge are excluded."""
    events = [_make_event(1.90, 1.90, 1.85, 1.85)]  # Paddy below fair
    rows = compute_value_bets(events, min_edge=0.0)
    assert all(r.edge >= 0.0 for r in rows)


def test_compute_value_bets_sorts_best_edge_first():
    """Output is sorted descending by edge."""
    event1 = _make_event(1.90, 1.90, 2.10, 1.80, line=3.5)
    event2 = {**_make_event(1.90, 1.90, 2.30, 1.70, line=4.5), "id": "evt2"}
    rows = compute_value_bets([event1, event2], min_edge=-0.50)
    edges = [r.edge for r in rows]
    assert edges == sorted(edges, reverse=True)


def test_compute_value_bets_missing_paddypower_skipped():
    """Event with no Paddy Power bookmaker produces no rows."""
    event = {
        "id": "evt1",
        "home_team": "A", "away_team": "B",
        "commence_time": "2026-06-10T15:00:00Z",
        "sport_key": "soccer_epl",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [{"key": "alternate_totals", "outcomes": [
                    {"name": "Over", "point": 3.5, "price": 1.9},
                    {"name": "Under", "point": 3.5, "price": 1.9},
                ]}],
            }
        ],
    }
    rows = compute_value_bets([event], min_edge=-0.50)
    assert rows == []


def test_compute_value_bets_missing_pinnacle_skipped():
    """Event with no Pinnacle bookmaker produces no rows."""
    event = {
        "id": "evt1",
        "home_team": "A", "away_team": "B",
        "commence_time": "2026-06-10T15:00:00Z",
        "sport_key": "soccer_epl",
        "bookmakers": [
            {
                "key": "paddypower",
                "markets": [{"key": "alternate_totals", "outcomes": [
                    {"name": "Over", "point": 3.5, "price": 2.1},
                    {"name": "Under", "point": 3.5, "price": 1.8},
                ]}],
            }
        ],
    }
    rows = compute_value_bets([event], min_edge=-0.50)
    assert rows == []


def test_compute_value_bets_empty_events():
    assert compute_value_bets([], min_edge=-0.05) == []
