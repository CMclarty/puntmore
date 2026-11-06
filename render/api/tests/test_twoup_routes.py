"""2-Up feed off a stubbed OddsPapi client (never live), the /api/2up routes,
the value-feed source and /api/ready's twoup_detail."""
import logging
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.security import user_permissions
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


def test_every_request_failing_keeps_the_last_good_board_and_says_stale():
    feed, client = _feed()
    good = feed.refresh(NOW)
    assert good

    def boom(ids, bookmaker="x"):
        raise RuntimeError("HTTP 500")
    client.odds_by_tournaments = boom
    feed.refresh(NOW + timedelta(minutes=5))
    at, rows = feed.served()
    assert rows == good and at == NOW
    st = feed.status()
    assert st["stale"] is True and st["degraded"] is True
    assert st["cannot_price"] == ["ALL — every OddsPapi odds request failed"]


def test_every_request_failing_with_no_good_board_serves_nothing():
    feed, client = _feed()
    client.odds_by_tournaments = lambda ids, bookmaker="x": (_ for _ in ()).throw(RuntimeError("HTTP 500"))
    feed.refresh(NOW)
    assert feed.served() == (None, []) and feed.status()["stale"] is False


def test_a_good_pass_after_a_failure_replaces_the_board_and_clears_stale():
    feed, client = _feed()
    real = client.odds_by_tournaments
    client.odds_by_tournaments = lambda ids, bookmaker="x": (_ for _ in ()).throw(RuntimeError("HTTP 500"))
    feed.refresh(NOW)
    client.odds_by_tournaments = real
    later = NOW + timedelta(minutes=5)
    rows = feed.refresh(later)
    assert feed.served() == (later, rows) and rows
    assert feed.status()["stale"] is False and feed.status()["served_at"] == later.isoformat()


def test_a_partly_failed_pass_that_prices_nothing_keeps_the_board_and_says_why():
    feed, client = _feed()
    good = feed.refresh(NOW)
    real = client.odds_by_tournaments

    def half(ids, bookmaker="x"):
        if bookmaker.startswith("pinnacle"):
            raise RuntimeError("HTTP 500")
        return []                              # bet365 answers, with nothing on
    client.odds_by_tournaments = half
    feed.refresh(NOW + timedelta(minutes=5))
    assert feed.served() == (NOW, good)
    st = feed.status()
    assert st["stale"] is True and st["degraded"] is True
    assert st["cannot_price"] == ["ALL — 1 OddsPapi odds request(s) failed and nothing was priced"]
    client.odds_by_tournaments = real


def test_an_honest_empty_pass_replaces_the_board():
    feed, client = _feed()
    assert feed.refresh(NOW)
    client.boards["bet365"] = []               # no error: bet365 simply prices nothing
    feed.refresh(NOW + timedelta(minutes=5))
    assert feed.served() == (NOW + timedelta(minutes=5), [])
    st = feed.status()
    assert st["stale"] is False and st["degraded"] is True


def test_a_served_row_leaves_at_the_min_lead_before_kickoff():
    feed, _ = _feed()
    rows = feed.refresh(NOW)
    ko = rows[0].kickoff
    lead = timedelta(minutes=feed.params.min_lead_minutes)
    assert feed.served(ko - lead - timedelta(minutes=1))[1] == rows
    assert feed.served(ko - lead + timedelta(minutes=1)) == (NOW, [])


def test_kickoffs_outside_the_horizon_are_not_candidates():
    boards = standard_boards(NOW + timedelta(days=3))
    feed, _ = _feed(boards)
    assert feed.candidates(NOW) == [] and feed.status()["degraded"] is False


# --- routes -------------------------------------------------------------------

def test_feed_route_without_a_client_says_so():
    app.state.twoup_feed = None
    body = TestClient(app).get("/2up/feed").json()
    assert body["available"] is False and body["candidates"] == []


def test_feed_route_before_the_first_refresh_is_empty_and_says_so():
    feed, client = _feed()
    app.state.twoup_feed = feed
    body = TestClient(app).get("/2up/feed").json()
    assert body["available"] is True and body["candidates"] == []
    assert body["status"]["not_yet_refreshed"] is True and client.calls == []
    assert body["status"]["killed"] is False


def test_feed_route_serves_the_board_without_fetching():
    feed, client = _feed()
    feed.refresh(NOW)
    app.state.twoup_feed = feed
    n = len(client.calls)
    body = TestClient(app).get("/2up/feed?value_only=true").json()
    assert body["generated_at"].startswith(NOW.strftime("%Y-%m-%dT%H:%M")) and len(client.calls) == n
    assert [c["selection"] for c in body["candidates"]] == ["Arsenal"]
    st = body["status"]
    assert (st["stale"], st["not_yet_refreshed"], st["killed"]) == (False, False, False)
    assert st["served_at"] == NOW.isoformat()


def test_feed_route_serves_the_last_good_board_when_every_request_fails():
    feed, client = _feed()
    feed.refresh(NOW)
    client.odds_by_tournaments = lambda ids, bookmaker="x": (_ for _ in ()).throw(RuntimeError("HTTP 500"))
    feed.refresh(NOW + timedelta(minutes=5))
    app.state.twoup_feed = feed
    body = TestClient(app).get("/2up/feed").json()
    assert len(body["candidates"]) == 4
    assert all(c["priced_at"].startswith(NOW.strftime("%Y-%m-%dT%H:%M")) for c in body["candidates"])
    assert body["status"]["stale"] is True
    assert body["status"]["cannot_price"] == ["ALL — every OddsPapi odds request failed"]


def test_kill_switch_empties_the_feed(monkeypatch):
    feed, _ = _feed()
    feed.refresh(NOW)
    app.state.twoup_feed = feed
    monkeypatch.setattr(settings, "twoup_kill_switch", True)
    body = TestClient(app).get("/2up/feed").json()
    assert body["candidates"] == [] and body["status"]["killed"] is True


def test_kill_switch_empties_the_2up_source_of_the_value_feed(monkeypatch):
    feed, _ = _feed()
    feed.refresh(NOW)
    app.state.twoup_feed = feed
    monkeypatch.setattr(settings, "twoup_kill_switch", True)
    app.dependency_overrides[user_permissions] = lambda: "value"
    try:
        body = TestClient(app).get("/value/2up").json()
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
    assert body["selections"] == []


def test_diagnostics_and_ready_never_fetch():
    feed, client = _feed()
    app.state.twoup_feed = feed
    api = TestClient(app)
    assert api.get("/2up/diagnostics").json()["feed"]["not_yet_refreshed"] is True
    assert api.get("/ready").json()["providers"]["twoup_detail"]["not_yet_refreshed"] is True
    assert client.calls == []


def test_scheduler_adds_the_refresh_job_only_with_a_feed():
    from app.scheduler import start_scheduler
    without = start_scheduler(odds_provider=None)
    try:
        assert without.get_job("twoup_refresh") is None
    finally:
        without.shutdown(wait=False)
    feed, _ = _feed()
    with_feed = start_scheduler(odds_provider=None, twoup_feed=feed)
    try:
        assert with_feed.get_job("twoup_refresh") is not None
    finally:
        with_feed.shutdown(wait=False)


def test_refresh_job_swallows_a_failing_pass(caplog):
    from app.scheduler import refresh_twoup

    class Broken:
        def refresh(self):
            raise RuntimeError("boom")
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        refresh_twoup(Broken())                # logged, never raised into the scheduler
    assert "2up refresh failed" in caplog.text


def test_feed_route_lists_priced_sides(monkeypatch):
    feed, _ = _feed()
    feed.refresh(NOW)                       # prime at the fixed clock
    app.state.twoup_feed = feed
    body = TestClient(app).get("/2up/feed").json()
    assert body["available"] and len(body["candidates"]) == 4
    top = body["candidates"][0]
    assert (top["selection"], top["value"], top["market"]) == ("Arsenal", True, "Match Result")
    assert top["edge"] == pytest.approx(top["p_win"] + top["rescue"] - 1 / top["price"], abs=1e-5)
    only = TestClient(app).get("/2up/feed", params={"value_only": True}).json()
    assert [c["selection"] for c in only["candidates"]] == ["Arsenal"]


def test_rows_carry_the_time_their_prices_were_fetched():
    feed, client = _feed()
    rows = feed.refresh(NOW)
    assert rows and all(c.priced_at == client.fetched_at([17, 18], "bet365") for c in rows)
    app.state.twoup_feed = feed
    body = TestClient(app).get("/2up/feed").json()
    assert body["candidates"][0]["priced_at"].startswith(NOW.strftime("%Y-%m-%d"))


def test_a_rows_priced_at_is_the_staler_of_its_two_books():
    feed, client = _feed()
    stale = NOW - timedelta(minutes=7)
    client.fetched_at = lambda ids, book: stale if book.startswith("pinnacle") else NOW
    assert {c.priced_at for c in feed.candidates(NOW)} == {stale}


def test_diagnostics_route_reports_the_feed_and_the_dials():
    feed, _ = _feed()
    feed.candidates(NOW)
    app.state.twoup_feed = feed
    api = TestClient(app)
    diag = api.get("/2up/diagnostics").json()
    assert diag["rescue_table"]["loaded"] is True and diag["rescue_table"]["buckets"]
    assert diag["global_kill_switch"] is False
    assert diag["settings"]["twoup_min_edge"] == settings.twoup_min_edge
    assert "clients" not in diag and "last_decision" not in diag
    assert api.get("/2up/ledger/owner").status_code == 404
    assert api.post("/2up/clients/owner/kill").status_code in (404, 405)


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
    feed.refresh(NOW)
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
    feed.refresh(NOW)
    app.state.twoup_feed = feed
    detail = TestClient(app).get("/ready").json()["providers"]["twoup_detail"]
    assert detail["degraded"] is False and detail["last_pass"]["priced_fixtures"] == 2


# --- final review fixes (6 Nov 2026) ------------------------------------------

def _value_2up():
    app.dependency_overrides[user_permissions] = lambda: "value"
    try:
        return TestClient(app).get("/value/2up", params={"min_edge": -0.5}).json()
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


def _fail_every_request(client):
    client.odds_by_tournaments = lambda ids, bookmaker="x": (_ for _ in ()).throw(RuntimeError("HTTP 500"))


def test_value_feed_a_stale_board_keeps_its_rows_with_their_own_seen_at_and_says_so():
    feed, client = _feed()
    good = feed.refresh(NOW)
    _fail_every_request(client)
    feed.refresh(NOW + timedelta(minutes=5))
    app.state.twoup_feed = feed
    body = _value_2up()
    assert body["selections"]                       # the last good board, still served
    priced = {c.priced_at for c in good}
    assert {datetime.fromisoformat(r["seen_at"].replace("Z", "+00:00")) for r in body["selections"]} == priced
    assert body["errors"]["2up"].startswith("stale: prices from ")
    assert NOW.strftime("%H:%M UTC") in body["errors"]["2up"]


def test_value_feed_killed_2up_has_no_rows_and_says_so(monkeypatch):
    feed, _ = _feed()
    feed.refresh(NOW)
    app.state.twoup_feed = feed
    monkeypatch.setattr(settings, "twoup_kill_switch", True)
    body = _value_2up()
    assert body["selections"] == []
    assert body["errors"]["2up"] == "killed: TWOUP_KILL_SWITCH is on"


def test_value_feed_2up_before_the_first_refresh_says_so():
    feed, client = _feed()
    app.state.twoup_feed = feed
    body = _value_2up()
    assert body["selections"] == [] and client.calls == []
    assert body["errors"]["2up"] == "not yet refreshed"


def test_value_feed_a_fresh_2up_board_reports_no_error():
    feed, _ = _feed()
    feed.refresh(NOW)
    app.state.twoup_feed = feed
    body = _value_2up()
    assert body["selections"] and "2up" not in body["errors"]


def _malformed(boards):
    """Pinnacle's 1X2 market arrives as a string, not an object: book_1x2 raises."""
    boards["pinnacle+30"][0]["bookmakerOdds"]["pinnacle+30"]["markets"]["101"] = "garbage"
    return boards


def test_a_pass_that_raises_keeps_the_last_good_board_and_says_stale(caplog):
    feed, client = _feed()
    good = feed.refresh(NOW)
    client.boards = _malformed(standard_boards())
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        feed.refresh(NOW + timedelta(minutes=5))        # never raises out
    assert feed.served() == (NOW, good)
    st = feed.status()
    assert st["stale"] is True and st["degraded"] is True and st["not_yet_refreshed"] is False
    assert st["cannot_price"][0].startswith("ALL — the pricing pass raised: ")
    assert st["last_pass"]["errors"][0].startswith("pass failed: ")
    assert st["last_pass"]["priced_fixtures"] == 0
    assert "Traceback" in caplog.text


def test_a_pass_that_raises_with_no_good_board_is_degraded_not_pending():
    feed, _ = _feed(_malformed(standard_boards()))
    assert feed.refresh(NOW) == []
    assert feed.served() == (None, [])
    st = feed.status()
    assert st["degraded"] is True and st["not_yet_refreshed"] is False and st["stale"] is False
    assert st["cannot_price"][0].startswith("ALL — the pricing pass raised: ")


def test_a_failed_chunk_beside_a_priced_one_is_named_and_degraded(monkeypatch):
    monkeypatch.setattr(settings, "oddspapi_tournaments_per_request", 1)   # chunks [17], [18]
    feed, client = _feed()
    real = client.odds_by_tournaments

    def chunk_18_fails(ids, bookmaker="x"):
        if list(ids) == [18]:
            raise RuntimeError("HTTP 503 held")
        return real(ids, bookmaker=bookmaker)
    client.odds_by_tournaments = chunk_18_fails
    rows = feed.refresh(NOW)
    assert {c.event_id for c in rows} == {"f1", "f2"}
    assert feed.served() == (NOW, rows)
    st = feed.status()
    assert st["stale"] is False and st["degraded"] is True
    assert st["cannot_price"] == ["pinnacle+30 chunk [18]: HTTP 503 held",
                                  "bet365 chunk [18]: HTTP 503 held"]


def test_no_configured_tournament_resolving_keeps_the_last_good_board(monkeypatch):
    monkeypatch.setattr(settings, "twoup_tournament_ids", "Premier League")
    feed, client = _feed()
    good = feed.refresh(NOW)
    assert good
    monkeypatch.setattr(settings, "twoup_tournament_ids", "Atlantis Cup")
    calls = len(client.calls)
    feed.refresh(NOW + timedelta(minutes=5))
    assert len(client.calls) == calls              # nothing to ask for, nothing asked
    assert feed.served() == (NOW, good)
    st = feed.status()
    assert st["stale"] is True and st["degraded"] is True
    assert st["cannot_price"] == ["ALL — no configured tournament resolved (Atlantis Cup)"]
    assert "every OddsPapi odds request failed" not in " ".join(st["cannot_price"])
