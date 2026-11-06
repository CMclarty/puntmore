"""2-Up model: pure functions — de-vig, rescue table, edge, filters."""
import json
from datetime import datetime, timezone

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


def test_filters_name_their_reason():
    p = Params(min_edge=0.01, min_price=1.5, max_price=5)
    assert model.passes_filters(cand(price=1.4, p_win=0.75), p) == "price outside band"
    assert model.passes_filters(cand(price=2.0, p_win=0.49, rescue=0.015), p) == "edge below floor"
    assert model.passes_filters(cand(price=2.0, p_win=0.5, rescue=0.02), p) is None


def test_params_come_from_settings(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "twoup_min_edge", 0.01)
    monkeypatch.setattr(settings, "twoup_min_lead_minutes", 30)
    p = Params.from_settings()
    assert (p.min_edge, p.min_price, p.max_price, p.min_lead_minutes) == (0.01, 1.25, 8.0, 30)
    assert not hasattr(p, "kelly_fraction")
