"""2-Up model: pure functions — de-vig, rescue table, edge, Kelly, rounding,
account-preservation limits."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.odds.utils import devigd_multi
from app.twoup import model
from app.twoup.model import Params

from tests.twoup_helpers import cand


# --- fair probabilities -------------------------------------------------------

def test_fair_1x2_is_the_power_devig_and_sums_to_one():
    p = model.fair_1x2(1.90, 3.70, 4.40)
    assert abs(sum(p) - 1.0) < 1e-9
    fair, _ = devigd_multi([1.90, 3.70, 4.40])
    assert p == pytest.approx([1 / f for f in fair])
    assert p[0] > p[2] > 0 and p[0] < 1 / 1.90      # vig removed from the favourite


def test_fair_1x2_refuses_an_incomplete_market():
    assert model.fair_1x2(1.90, None, 4.40) is None
    assert model.fair_1x2(1.90, 1.0, 4.40) is None


# --- rescue table -------------------------------------------------------------

def test_the_shipped_table_loads_and_covers_zero_to_one():
    t = model.load_rescue_table()
    assert t.loaded and t.buckets
    assert t.buckets[0].lo == 0.0 and t.buckets[-1].hi == 1.0
    for a, b in zip(t.buckets, t.buckets[1:]):
        assert a.hi == b.lo                          # contiguous
    assert all(0 < b.rescue < 0.05 for b in t.buckets)
    assert t.rate(1.0) == t.buckets[-1].rescue       # top edge is inclusive


def test_rate_is_bucketed_with_a_documented_default():
    t = model.parse_rescue_table({"default": 0.011, "buckets": [
        {"lo": 0.2, "hi": 0.5, "rescue": 0.01}, {"lo": 0.5, "hi": 0.8, "rescue": 0.02}]})
    assert t.rate(0.2) == 0.01 and t.rate(0.4999) == 0.01
    assert t.rate(0.5) == 0.02 and t.rate(0.8) == 0.02
    assert t.rate(0.1) == 0.011 and t.rate(0.9) == 0.011   # outside every bucket


def test_a_bad_table_is_rejected_and_a_missing_file_falls_back_visibly(tmp_path):
    with pytest.raises(ValueError):
        model.parse_rescue_table({"buckets": [{"lo": 0.0, "hi": 0.5, "rescue": 0.01},
                                              {"lo": 0.4, "hi": 0.9, "rescue": 0.01}]})
    with pytest.raises(ValueError):
        model.parse_rescue_table({"buckets": [{"lo": 0.0, "hi": 0.5, "rescue": 1.2}]})
    bad = tmp_path / "t.json"
    bad.write_text("{not json")
    t = model.load_rescue_table(bad)
    assert t.loaded is False and t.rate(0.5) == model.FALLBACK_RESCUE
    assert model.load_rescue_table(tmp_path / "missing.json").loaded is False


def test_table_round_trips_through_json(tmp_path):
    t = model.load_rescue_table()
    p = tmp_path / "copy.json"
    p.write_text(json.dumps(t.to_json(notes="x")))
    assert model.load_rescue_table(p).buckets == t.buckets


# --- edge ---------------------------------------------------------------------

def test_edge_is_win_plus_rescue_minus_implied():
    assert model.edge(0.5, 0.02, 2.0) == pytest.approx(0.02)
    c = cand(price=2.0, p_win=0.5, rescue=0.02)
    assert c.payout_prob == pytest.approx(0.52) and c.ev == pytest.approx(0.04)


def test_price_fixture_prices_both_sides_never_the_draw():
    t = model.load_rescue_table()
    rows = model.price_fixture(event_id="f1", home="A", away="B",
                               kickoff=datetime(2026, 11, 5, 15, tzinfo=timezone.utc),
                               pinnacle=(1.90, 3.70, 4.40), bet365=(2.05, 3.50, 4.00), table=t)
    assert [r.side for r in rows] == ["home", "away"]
    home = rows[0]
    assert home.rescue == t.rate(home.p_win)
    assert home.edge == pytest.approx(home.p_win + home.rescue - 1 / 2.05)
    assert home.edge > 0 > rows[1].edge
    assert model.price_fixture(event_id="f", home="A", away="B", kickoff=home.kickoff,
                               pinnacle=(1.9, None, 4.4), bet365=(2, 3, 4), table=t) == []


# --- Kelly --------------------------------------------------------------------

def test_kelly_is_zero_without_edge_and_fractional_and_capped():
    assert model.kelly_fraction(0.45, 2.0) == 0.0
    assert model.kelly_fraction(0.55, 2.0) == pytest.approx(0.10)
    c = cand(price=2.0, p_win=0.53, rescue=0.02)       # full Kelly 0.10
    p = Params(kelly_fraction=0.25, max_selection_fraction=1.0)
    assert model.kelly_stake(c, 1000, p) == pytest.approx(25.0)
    assert model.kelly_stake(c, 1000, p.but(max_selection_fraction=0.01)) == pytest.approx(10.0)
    assert model.kelly_stake(c, 0, p) == 0.0


# --- rounding -----------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    (0.4, 0), (0.6, 1), (1.4, 1), (1.6, 2), (3.4, 2), (3.6, 5), (7.4, 5), (7.6, 10),
    (14, 10), (16, 20), (34, 20), (36, 50), (140, 100), (160, 200), (1600, 2000),
])
def test_round_stake_to_the_nearest_1_2_5_rung(raw, want):
    assert model.round_stake(raw) == want


def test_round_stake_never_exceeds_its_cap_and_has_no_floor():
    assert model.round_stake(18, cap=15) == 10
    assert model.round_stake(18, cap=0.9) == 0
    assert model.round_down_stake(7) == 5 and model.round_down_stake(0.9) == 0
    assert model.stake_ladder(100) == [1, 2, 5, 10, 20, 50, 100]


# --- account preservation ----------------------------------------------------

def test_stake_ceiling_from_balance_with_a_hard_cap_and_no_floor():
    p = Params(stake_hard_cap=200, stake_balance_fraction=0.1)
    assert model.stake_ceiling(500, p) == 50
    assert model.stake_ceiling(10_000, p) == 200
    assert model.stake_ceiling(0, p) == 0


def test_growth_cap_reads_trailing_stakes_only():
    p = Params(stake_growth_cap=0.25, growth_min_history=5, initial_stake_cap=10,
               trailing_stakes=4)
    assert model.growth_cap([20, 20], p) == 10                 # too little history
    p = p.but(growth_min_history=3)
    assert model.growth_cap([100, 20, 20, 20, 20], p) == pytest.approx(25)   # last 4 only
    assert model.account_stake_cap(100, [20, 20, 20, 20], p.but(stake_balance_fraction=0.1)) == 10


def test_betting_window_is_0730_to_2200_london():
    tz = ZoneInfo("Europe/London")
    p = Params()
    assert not model.in_window(datetime(2026, 11, 5, 7, 29, tzinfo=tz), p)
    assert model.in_window(datetime(2026, 11, 5, 7, 30, tzinfo=tz), p)
    assert model.in_window(datetime(2026, 11, 5, 21, 59, tzinfo=tz), p)
    assert not model.in_window(datetime(2026, 11, 5, 22, 0, tzinfo=tz), p)


def test_filters_name_their_reason():
    p = Params(min_edge=0.01, min_price=1.5, max_price=5)
    assert model.passes_filters(cand(price=1.4, p_win=0.75), p) == "price outside band"
    assert model.passes_filters(cand(price=2.0, p_win=0.49, rescue=0.015), p) == "edge below floor"
    assert model.passes_filters(cand(price=2.0, p_win=0.5, rescue=0.02), p) is None


def test_params_come_from_settings(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "twoup_kelly_fraction", 0.1)
    monkeypatch.setattr(settings, "twoup_window_start", "08:15")
    p = Params.from_settings()
    assert p.kelly_fraction == 0.1 and p.window_start.hour == 8 and p.window_start.minute == 15
