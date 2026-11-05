from datetime import datetime, timedelta, timezone

import pytest

from puntmore_home.scraper.schedule import is_due, parse_every
from puntmore_home.scraper.sources import Schedule, Source


def src(**sched):
    return Source(name="x", parser="x", schedule=Schedule(**sched))


def test_parse_every():
    assert parse_every("30m") == timedelta(minutes=30)
    assert parse_every("2h") == timedelta(hours=2)
    with pytest.raises(ValueError):
        parse_every("soon")


def test_every_due():
    now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    s = src(every="2h")
    assert is_due(s, None, now)
    assert not is_due(s, now - timedelta(hours=1), now)
    assert is_due(s, now - timedelta(hours=2), now)


def test_at_due_local_time():
    s = src(at=["09:00"])
    before = datetime(2026, 11, 5, 8, 59, tzinfo=timezone.utc)   # November: London == UTC
    after = datetime(2026, 11, 5, 9, 1, tzinfo=timezone.utc)
    assert not is_due(s, after - timedelta(days=1), before)
    assert is_due(s, after - timedelta(days=1), after)
    assert not is_due(s, after, after + timedelta(minutes=30))


def test_no_schedule_runs_once():
    now = datetime.now(timezone.utc)
    assert is_due(src(), None, now)
    assert not is_due(src(), now, now)
