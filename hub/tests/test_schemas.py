"""The placement engine's data shapes (``schemas.py``): a report's kind is checked, and a bet is
built from a dict with its types coerced, the way the Bet Placer will turn a feed row into a bet."""
import pytest

from puntmore_hub.schemas import REPORT_KINDS, Bet, Report


def test_report_kind_validated():
    with pytest.raises(ValueError):
        Report("unknown", {})
    for kind in REPORT_KINDS:
        assert Report(kind, {"bet_id": "b1"}).to_dict()["kind"] == kind


def test_bet_from_dict():
    b = Bet.from_dict({"bet_id": 1, "account_id": "a", "legs": [{"event_id": 9, "market": "m", "selection": "s", "price": "2.0"}],
                       "stake": "5", "expected_price": 2.0})
    assert b.bet_id == "1" and b.legs[0].event_id == "9" and b.stake == 5.0 and b.bet_type == "single"
    assert b.legs[0].price == 2.0 and isinstance(b.legs[0].price, float)
