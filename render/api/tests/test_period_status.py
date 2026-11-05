"""Leg.period_status / period_label — the active-vs-next classification that
drives the tracker's period sections."""
from datetime import date, timedelta

from app.models import Coupon, Leg


def _leg(match_day, period_ends, period="MON_WED"):
    leg = Leg(match_name="A v B", pick="x")
    leg.coupon = Coupon(
        type_code="TTS2", period=period,
        match_day=match_day, period_ends=period_ends,
    )
    return leg


def test_active_when_today_inside_window():
    today = date.today()
    leg = _leg(today - timedelta(days=1), today + timedelta(days=1))
    assert leg.period_status == "active"


def test_active_on_window_boundaries():
    today = date.today()
    assert _leg(today, today + timedelta(days=2)).period_status == "active"   # first day
    assert _leg(today - timedelta(days=2), today).period_status == "active"   # last day


def test_upcoming_when_window_not_started():
    today = date.today()
    leg = _leg(today + timedelta(days=1), today + timedelta(days=3))
    assert leg.period_status == "upcoming"


def test_past_when_window_ended():
    today = date.today()
    leg = _leg(today - timedelta(days=3), today - timedelta(days=1))
    assert leg.period_status == "past"


def test_single_day_window_uses_match_day_when_no_end():
    today = date.today()
    assert _leg(today, None).period_status == "active"
    assert _leg(today + timedelta(days=1), None).period_status == "upcoming"


def test_unknown_window_is_none():
    leg = Leg(match_name="A v B", pick="x")
    leg.coupon = Coupon(type_code="TTS2", match_day=None)
    assert leg.period_status is None
    # No coupon at all (shouldn't happen via the API, but must not raise).
    assert Leg(match_name="A", pick="x").period_status is None


def test_period_label_from_code():
    today = date.today()
    assert _leg(today, today, period="MON_WED").period_label == "Mon–Wed"
    assert _leg(today, today, period="SAT").period_label == "Saturday"
