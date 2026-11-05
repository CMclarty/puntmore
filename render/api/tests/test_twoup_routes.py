"""2-Up feed off a stubbed OddsPapi client (never live), the /api/2up routes,
the value-feed source, /api/ready's twoup_detail and the scheduled pass."""
import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.security import user_permissions
from app.twoup import decision, ledger
from app.twoup.feed import TwoUpFeed
from app.twoup.model import Params

from tests.twoup_helpers import NOW, FakeOddsPapi, Session, engine, standard_boards


@pytest.fixture(autouse=True)
def world(monkeypatch):
    Base.metadata.create_all(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(settings, "twoup_tournament_ids", "17,18")
    monkeypatch.setattr(settings, "oddspapi_fair_book", "pinnacle")
    yield
    app.dependency_overrides.pop(get_db, None)
    app.state.twoup_feed = None
    Base.metadata.drop_all(bind=engine)


def _feed(boards=None, **kw):
    client = FakeOddsPapi(boards if boards is not None else standard_boards(), **kw)
    return TwoUpFeed(client, params=Params()), client


# --- the feed -----------------------------------------------------------------

def test_feed_prices_both_books_through_resolve_book_and_counts_gaps(caplog):
    feed, client = _feed()
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        rows = feed.candidates(NOW)
    assert {b for _, b in client.calls} == {"pinnacle+30", "bet365"}
    assert len(client.calls) == 2                 # one chunk (17,18) x two books
    assert {(c.event_id, c.side) for c in rows} == {("f1", "home"), ("f1", "away"),
                                                    ("f2", "home"), ("f2", "away")}
    lp = feed.last_pass
    assert (lp["in_horizon"], lp["priced_fixtures"], lp["no_bet365"]) == (3, 2, 1)
    assert [c.selection for c in feed.value(NOW)] == ["Arsenal"]
    assert len([r for r in caplog.records if r.getMessage().startswith("2up feed")]) == 1
    assert feed.status()["degraded"] is False


def test_feed_is_cached_between_calls():
    feed, client = _feed()
    feed.candidates(NOW)
    feed.candidates(NOW)
    feed.value(NOW)
    assert len(client.calls) == 2


def test_fixtures_on_but_no_bet365_is_degraded():
    boards = standard_boards()
    boards["bet365"] = []
    feed, _ = _feed(boards)
    assert feed.candidates(NOW) == []
    st = feed.status()
    assert st["degraded"] and "bet365 1X2 missing" in st["cannot_price"][0]


def test_every_request_failing_is_degraded_not_stale():
    feed, client = _feed()

    def boom(ids, bookmaker="x"):
        raise RuntimeError("HTTP 500")
    client.odds_by_tournaments = boom
    assert feed.candidates(NOW) == []
    assert feed.status()["cannot_price"] == ["ALL — every OddsPapi odds request failed"]


def test_kickoffs_outside_the_horizon_are_not_candidates():
    boards = standard_boards(NOW + timedelta(days=3))
    feed, _ = _feed(boards)
    assert feed.candidates(NOW) == [] and feed.status()["degraded"] is False


# --- routes -------------------------------------------------------------------

def test_feed_route_without_a_client_says_so():
    app.state.twoup_feed = None
    body = TestClient(app).get("/2up/feed").json()
    assert body["available"] is False and body["candidates"] == []


def test_feed_route_lists_priced_sides(monkeypatch):
    feed, _ = _feed()
    feed.candidates(NOW)                       # prime at the fixed clock
    app.state.twoup_feed = feed
    body = TestClient(app).get("/2up/feed").json()
    assert body["available"] and len(body["candidates"]) == 4
    top = body["candidates"][0]
    assert (top["selection"], top["value"], top["market"]) == ("Arsenal", True, "Match Result")
    assert top["edge"] == pytest.approx(top["p_win"] + top["rescue"] - 1 / top["price"], abs=1e-5)
    only = TestClient(app).get("/2up/feed", params={"value_only": True}).json()
    assert [c["selection"] for c in only["candidates"]] == ["Arsenal"]


def _seed_owner():
    s = Session()
    ledger.ensure_clients(s, "owner:owner=acc-1")
    ledger.apply_report(s, "owner", {"kind": "balance", "account_id": "acc-1",
                                     "balance": 400.0, "ts": NOW.isoformat()})
    s.close()


def test_ledger_kill_resume_and_diagnostics_routes():
    _seed_owner()
    feed, _ = _feed()
    feed.candidates(NOW)
    app.state.twoup_feed = feed
    s = Session()
    d = decision.run_all(s, feed, now=NOW, params=Params())
    s.close()
    assert d["clients"]["owner"]["bets"] == 1 and d["clients"]["owner"]["dry_run"] is True

    api = TestClient(app)
    body = api.get("/2up/ledger/owner").json()
    assert body["client"]["dry_run"] is True and body["client"]["pooled_bankroll"] == 400
    [acc] = body["accounts"]
    assert acc["open_exposure"] == body["open_exposure"] > 0
    [bet] = body["bets"]
    assert bet["legs"][0]["selection"] == "Arsenal" and bet["status"] == "queued"
    assert body["balances"][0]["balance"] == 400
    assert api.get("/2up/ledger/nobody").status_code == 404

    assert api.post("/2up/clients/owner/kill").json()["kill_switch"] is True
    assert api.post("/2up/clients/owner/resume").json()["kill_switch"] is False
    assert api.post("/2up/clients/nobody/kill").status_code == 404

    diag = api.get("/2up/diagnostics").json()
    assert diag["rescue_table"]["loaded"] is True and diag["rescue_table"]["buckets"]
    assert diag["last_decision"]["queued"] == 1
    assert diag["settings"]["twoup_kelly_fraction"] == settings.twoup_kelly_fraction
    assert [c["client_id"] for c in diag["clients"]] == ["owner"]


def test_routes_need_the_365_grant():
    app.dependency_overrides[user_permissions] = lambda: "coupons"
    try:
        assert TestClient(app).get("/2up/feed").status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
    app.dependency_overrides[user_permissions] = lambda: "accabonus"
    try:
        assert TestClient(app).get("/2up/feed").status_code == 200
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


def test_value_feed_carries_2up_rows():
    feed, _ = _feed()
    feed.candidates(NOW)
    app.state.twoup_feed = feed
    app.dependency_overrides[user_permissions] = lambda: "value"
    try:
        body = TestClient(app).get("/value/2up").json()
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
    [r] = body["selections"]
    assert (r["source"], r["bookmaker"], r["selection"], r["side"], r["basis"]) == \
        ("2up", "bet365", "Arsenal", "home", "2up")
    assert r["edge"] == pytest.approx(r["odds"] / r["fair"] - 1)


def test_ready_reports_the_2up_feed():
    app.state.twoup_feed = None
    detail = TestClient(app).get("/ready").json()["providers"]["twoup_detail"]
    assert detail["degraded"] is True and detail["cannot_price"]
    feed, _ = _feed()
    feed.candidates(NOW)
    app.state.twoup_feed = feed
    detail = TestClient(app).get("/ready").json()["providers"]["twoup_detail"]
    assert detail["degraded"] is False and detail["last_pass"]["priced_fixtures"] == 2


def test_the_scheduled_pass_does_nothing_outside_the_window():
    feed, client = _feed()
    s = Session()
    late = NOW.replace(hour=23)
    out = decision.run_all(s, feed, now=late, params=Params())
    s.close()
    assert out["skipped"] == "outside betting window" and client.calls == []


def test_scheduler_adds_the_job_only_with_a_feed():
    from app.scheduler import start_scheduler
    without = start_scheduler(odds_provider=None)
    try:
        assert without.get_job("twoup_decision") is None
    finally:
        without.shutdown(wait=False)
    feed, _ = _feed()
    with_feed = start_scheduler(odds_provider=None, twoup_feed=feed)
    try:
        assert with_feed.get_job("twoup_decision") is not None
    finally:
        with_feed.shutdown(wait=False)
