"""Which vendor Hills prices on: ODDSAPINET_API_KEY puts it on odds-api.net,
else it stays on OddsPapi (whose subscription keeps Paddy, 2-Up and the
coupon tracker); /api/ready names it, and the OddsPapi check on Hills says
where Hills' prices come from. Hermetic: no key, no network."""
import pytest
from fastapi.testclient import TestClient

from app.cards.oddsapinet import OddsApiNetHillsSource
from app.cards.oddspapi import OddsPapiClient, OddsPapiPaddySource
from app.hills.vendor import build_hills_source
from app.main import app
from tests.oan_fixtures import Clock, FakeOan, routes


@pytest.fixture(autouse=True)
def _no_hills_client():
    yield
    app.state.hills_client = None


def make(fake=None, **kw):
    fake = fake or FakeOan(routes())
    clock = Clock()
    opts = dict(runner=lambda fn: fn(), now=clock.now, monotonic=clock.mono, window_hours=168)
    opts.update(kw)
    return OddsApiNetHillsSource(fake, **opts), fake, clock


def _hills_detail():
    # /api/ready under the asgi mount; the handler is app.main's /ready.
    return TestClient(app).get("/ready").json()["providers"]["hills_detail"]


def test_the_odds_api_net_key_chooses_odds_api_net():
    src = build_hills_source("k-test", None)
    assert isinstance(src, OddsApiNetHillsSource)
    assert src._client.vendor == "odds-api.net" and src._client.daily_budget == 8000


def test_the_key_wins_over_an_oddspapi_client():
    assert isinstance(build_hills_source("k-test", OddsPapiClient("x")), OddsApiNetHillsSource)


def test_without_it_hills_stays_on_oddspapi():
    src = build_hills_source("", OddsPapiClient("x"))
    assert isinstance(src, OddsPapiPaddySource)
    assert (src.book, src.extra_tournaments, src.vendor) == ("williamhill", (19, 21), "oddspapi")


def test_with_neither_there_is_no_source():
    assert build_hills_source("", None) is None


def test_ready_names_the_hills_vendor():
    src, _, _ = make()
    src.get_events()
    app.state.hills_client = src
    assert _hills_detail() == {"provider": "odds-api.net", "degraded": False, "requests_today": 3}
    app.state.hills_client = None
    assert _hills_detail() == {"provider": None, "degraded": True, "requests_today": None}


def test_ready_calls_hills_degraded_when_every_snapshot_failed():
    fake = FakeOan(routes())
    for path in list(fake.routes):
        if path.endswith("/odds/snapshot"):
            fake.routes[path] = RuntimeError("boom")
    src, _, _ = make(fake)
    src.get_events()
    app.state.hills_client = src
    assert _hills_detail()["degraded"] is True


def test_the_oddspapi_check_on_hills_says_where_hills_prices_from():
    src, _, _ = make()
    app.state.hills_client = src
    assert TestClient(app).get("/hills/oddspapi-check").json() == {
        "error": "Hills prices from odds-api.net, not OddsPapi: see /hills/diagnostics."}


def test_the_oddspapi_check_without_oddspapi_still_says_so():
    app.state.hills_client = None
    assert TestClient(app).get("/hills/oddspapi-check").json() == {
        "error": "OddsPapi is not configured — set ODDSPAPI_API_KEY."}


def test_ready_shows_the_count_kept_across_a_restart():
    """The count is stored (final review, C): a rebuilt source starts from it."""
    from datetime import datetime, timezone

    class MemStore:
        saved = None

        def load(self):
            return self.saved

        def save(self, utc_day, count):
            self.saved = (utc_day, count)

    store = MemStore()
    src = build_hills_source("k-test", None, usage_store=store)
    app.state.hills_client = src
    store.saved = (datetime.now(timezone.utc).date().isoformat(), 412)
    app.state.hills_client = build_hills_source("k-test", None, usage_store=store)
    assert _hills_detail()["requests_today"] == 412
