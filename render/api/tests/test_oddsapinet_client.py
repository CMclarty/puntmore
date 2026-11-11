"""The odds-api.net HTTP client (cards/oddsapinet_client.py): the key in the
header, every request counted against a daily credit cap, half a second
between requests, a 429 raised and never retried. Hermetic: an
httpx.MockTransport stands in for the network; the clock, the sleep and the
UTC day are injected."""
from datetime import date
import logging

import httpx
import pytest

from app.cards.oddsapinet_client import BudgetSpent, OddsApiNetClient, RateLimited

KEY = "k-test"


def _client(handler, **kw):
    """A client on a MockTransport whose handler records each request."""
    seen: list[httpx.Request] = []

    def record(req):
        seen.append(req)
        return handler(req)

    kw.setdefault("sleep", lambda s: None)
    return seen, OddsApiNetClient(KEY, transport=httpx.MockTransport(record), **kw)


def _ok(req):
    return httpx.Response(200, json={"items": []})


def test_the_key_goes_in_the_header_never_the_url():
    seen, c = _client(lambda req: httpx.Response(200, json={"items": []}))
    assert c.get("/events", sport="soccer", limit=1000) == {"items": []}
    assert seen[0].headers["X-API-Key"] == "k-test"
    assert str(seen[0].url) == "https://api.odds-api.net/v1/events?sport=soccer&limit=1000"
    assert "k-test" not in str(seen[0].url)


def test_every_request_is_counted_failures_too():
    codes = iter([500, 200])
    seen, c = _client(lambda req: httpx.Response(next(codes), json={}))
    with pytest.raises(httpx.HTTPStatusError):
        c.get("/events")
    assert c.requests_today == 1
    c.get("/events")
    assert c.requests_today == 2


def test_a_spent_budget_sends_nothing():
    seen, c = _client(_ok, daily_budget=2)
    c.get("/a")
    c.get("/b")
    with pytest.raises(BudgetSpent):
        c.get("/c")
    assert len(seen) == 2 and c.budget_left() == 0


def test_the_count_starts_again_each_utc_day():
    days = iter([date(2026, 11, 11), date(2026, 11, 11), date(2026, 11, 12)])
    last = [date(2026, 11, 11)]

    def today():
        last[0] = next(days, last[0])
        return last[0]

    seen, c = _client(_ok, today=today)
    c.get("/a")
    c.get("/b")
    assert c.requests_today == 2
    c.get("/c")                      # the first call on the 12th
    assert c.requests_today == 1


def test_requests_are_spaced_half_a_second():
    slept: list[float] = []
    seen, c = _client(_ok, monotonic=lambda: 100.0, sleep=slept.append)
    c.get("/a")
    c.get("/b")
    assert slept == [pytest.approx(0.5)]


def test_a_429_raises_rate_limited_and_is_not_retried():
    seen, c = _client(lambda req: httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(RateLimited) as e:
        c.get("/events")
    assert e.value.retry_after == 7.0 and len(seen) == 1


def test_the_key_is_never_in_an_error_or_a_log(caplog):
    caplog.set_level(logging.DEBUG)
    seen, c = _client(lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError) as e:
        c.get("/events/1/odds/snapshot", bookmakers="williamhill")
    assert "k-test" not in str(e.value) and "k-test" not in caplog.text


def test_the_default_daily_cap_is_250k_a_month_over_31_days():
    from app.cards.oddsapinet_client import OddsApiNetClient
    from app.config import Settings
    assert Settings.model_fields["oddsapinet_daily_credit_budget"].default == 8000
    assert OddsApiNetClient("k-test").daily_budget == 8000


# --- the day's count survives a restart (final review, C) ----------------------

class MemStore:
    """The store's two calls, in memory."""

    def __init__(self, saved=None):
        self.saved = saved
        self.saves = 0

    def load(self):
        return self.saved

    def save(self, utc_day, count):
        self.saved = (utc_day, count)
        self.saves += 1


def _today():
    return date(2026, 11, 11)


def test_a_second_client_on_the_same_store_starts_from_the_first_ones_count():
    store = MemStore()
    _, first = _client(_ok, store=store, today=_today)
    first.get("/a"); first.get("/b"); first.get("/c")
    assert store.saved == ("2026-11-11", 3) and store.saves == 3
    _, second = _client(_ok, store=store, today=_today)
    assert second.requests_today == 3 and second.budget_left() == 7997
    second.get("/d")
    assert store.saved == ("2026-11-11", 4)


def test_a_count_stored_yesterday_starts_at_zero():
    _, c = _client(_ok, store=MemStore(("2026-11-10", 7000)), today=_today)
    assert c.requests_today == 0
    c.get("/a")
    assert c.requests_today == 1


def test_the_cap_holds_across_a_restart():
    store = MemStore()
    _, first = _client(_ok, store=store, today=_today, daily_budget=2)
    first.get("/a"); first.get("/b")
    seen, second = _client(_ok, store=store, today=_today, daily_budget=2)
    with pytest.raises(BudgetSpent):
        second.get("/c")
    assert seen == []


def test_a_store_that_fails_never_stops_a_fetch(caplog):
    class Broken:
        def load(self):
            raise OSError("disk gone")

        def save(self, utc_day, count):
            raise OSError("disk gone")

    seen, c = _client(_ok, store=Broken(), today=_today)
    assert c.get("/a") == {"items": []} and c.get("/b") == {"items": []}
    assert c.requests_today == 2 and len(seen) == 2
    assert "disk gone" in caplog.text


def test_the_sql_store_round_trips_one_row():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.cards.oddsapinet_usage import SqlUsageStore
    from app.database import Base
    from app.models import OddsApiNetUsage

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    store = SqlUsageStore(sessionmaker(bind=engine))
    assert store.load() is None
    store.save("2026-11-11", 5)
    store.save("2026-11-11", 6)
    assert store.load() == ("2026-11-11", 6)
    with sessionmaker(bind=engine)() as db:
        assert db.query(OddsApiNetUsage).count() == 1
    _, c = _client(_ok, store=store, today=_today)
    assert c.requests_today == 6
