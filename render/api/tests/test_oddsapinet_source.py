"""Hills on odds-api.net: the source (board, background pass, freshness,
diagnostics). Hermetic: FakeOan answers from the recorded responses, the
clock is a Clock, and the pass runs in the caller unless a test says not."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.cards import feed
from app.cards.oddsapinet import OddsApiNetHillsSource, SnapshotLines, priced_at
from app.cards.oddsapinet_client import BudgetSpent, RateLimited
from app.main import app
from tests.oan_fixtures import Clock, FakeOan, load, routes

SNAP = {"bookmakers": "williamhill,pinnacle", "price_fields": "odds",
        "types": "total,team total,handicap", "periods": "full time"}
ENG = "/events/1417142635/odds/snapshot"
ARM = "/events/4229698994/odds/snapshot"


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


def paths(fake):
    return [p for p, _ in fake.calls]


def test_the_first_sweep_lists_fetches_and_builds_the_board():
    src, fake, _ = make()
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert fake.calls == [("/events", {"sport": "soccer", "start_from": 1794400800,
                                       "start_to": 1795005600, "limit": 1000}),
                          (ARM, SNAP),
                          (ENG, SNAP)]
    assert [f["id"] for f in src.fixtures] == ["oan:4229698994", "oan:1417142635"]
    assert set(src.boards) == {"oan:4229698994", "oan:1417142635"}
    assert isinstance(src.boards["oan:1417142635"], SnapshotLines)
    s = src.last_sweep
    assert {k: s[k] for k in ("source", "book", "fair_source", "fixtures_in_window", "called",
            "carried_paddy_prices", "carried_pinnacle_prices", "pending", "stale_williamhill",
            "failed", "errors", "next_kickoff")} == {
        "source": "odds-api.net", "book": "williamhill", "fair_source": "odds-api.net:pinnacle",
        "fixtures_in_window": 2, "called": 2, "carried_paddy_prices": 1,
        "carried_pinnacle_prices": 1, "pending": 0, "stale_williamhill": 0, "failed": 0,
        "errors": [], "next_kickoff": "2026-11-12T17:00:00Z"}
    assert (s["window_hours"], s["requests_today"], s["request_budget"]) == (168, 3, 8000)
    assert (src.vendor, src.book, src.label) == ("odds-api.net", "williamhill", "William Hill")


def test_the_listing_follows_the_cursor_three_pages_at_most():
    r = routes()
    first = r["/events"]
    first["next_cursor"] = "c1"

    class Paged(FakeOan):
        def get(self, path, **params):
            body = super().get(path, **params)
            if path == "/events" and params.get("cursor"):
                n = int(params["cursor"][1:])
                body = {"items": [], "next_cursor": f"c{n + 1}"}
            return body

    src, fake, _ = make(Paged(r))
    src.get_events()
    listing = [p for p, _ in fake.calls if p == "/events"]
    assert [c.get("cursor") for p, c in fake.calls if p == "/events"] == [None, "c1", "c2"]
    assert len(listing) == 3


def test_a_board_is_reused_until_the_vendor_would_have_refreshed():
    src, fake, clock = make(); src.get_events(); src.get_events()
    assert len(fake.calls) == 3
    clock.advance(601); src.get_events()
    assert paths(fake)[3:] == ["/events"]
    clock.advance(3000); src.get_events()          # 3601 s: both boards due (ttl 3599)
    assert paths(fake)[4:] == ["/events", ARM, ENG]


def test_only_games_william_hill_prices_are_fetched():
    r = routes()
    r["/events"]["items"].append({
        "event_id": "999", "sport": "soccer", "league": "UEFA Nations League",
        "start_time": 1794515400, "home_team": "Wales", "away_team": "Iceland",
        "bookmakers": ["pinnacle"]})
    src, fake, _ = make(FakeOan(r))
    src.get_events()
    assert "oan:999" in [f["id"] for f in src.fixtures]
    assert "/events/999/odds/snapshot" not in paths(fake)
    assert src.last_sweep["fixtures_in_window"] == 2


def test_a_board_for_a_game_no_longer_listed_is_dropped():
    src, fake, clock = make()
    src.get_events()
    fake.routes["/events"]["items"] = [i for i in fake.routes["/events"]["items"]
                                       if i["event_id"] == "1417142635"]
    clock.advance(601)
    src.get_events()
    assert set(src.boards) == {"oan:1417142635"}


def test_a_game_leaves_the_board_at_kickoff():
    src, fake, clock = make()
    src.get_events()
    clock.advance(111600)                          # 2026-11-12T19:40Z
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert paths(fake)[3:] == ["/events", ENG]     # Armenia has kicked off
    clock.advance(360)                             # 19:46Z, inside the listing cache
    assert src.get_events() == []
    assert len(fake.calls) == 5


def test_the_pass_is_capped_and_nearest_kickoff_goes_first():
    src, fake, _ = make(max_per_pass=1)
    assert src.get_events() == []
    assert paths(fake) == ["/events", ARM]
    assert src.last_sweep["pending"] == 1
    assert feed.diagnose(src)["verdict"].startswith(
        "Still fetching William Hill's prices: 1 match(es)")


def test_one_pass_at_a_time():
    started = []
    src, fake, _ = make(runner=started.append, first_wait=0)
    assert src.get_events() == [] and src.get_events() == []
    assert len(started) == 1
    started[0]()
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]


def test_the_first_request_waits_for_the_first_pass_only():
    import threading
    started = []

    def runner(fn):
        t = threading.Thread(target=fn)
        started.append(t)
        t.start()

    src, fake, clock = make(runner=runner, first_wait=5)
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    clock.advance(3601)
    gate = threading.Event()
    real_get = fake.get

    def slow_get(path, **params):
        if path != "/events":
            gate.wait(5)
        return real_get(path, **params)

    fake.get = slow_get
    # A pass has finished before: the request serves what is held at once.
    import time
    t0 = time.monotonic()
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert time.monotonic() - t0 < 2 and src._refreshing
    gate.set()
    for t in started:
        t.join(5)


def test_a_failed_snapshot_is_named_and_held():
    r = routes()
    r[ENG] = RuntimeError("boom")
    src, fake, clock = make(FakeOan(r))
    src.get_events()
    assert (src.last_sweep["failed"], src.last_sweep["errors"]) == (1, ["1417142635: boom"])
    assert src.last_sweep["pending"] == 0          # held, so not pending
    src.get_events()                               # held: not asked again
    clock.advance(601); src.get_events()           # asked again
    assert paths(fake).count(ENG) == 2


def test_a_spent_budget_ends_the_pass():
    r = routes()
    r[ARM] = BudgetSpent("daily credit budget spent")
    src, fake, _ = make(FakeOan(r))
    src.get_events()
    assert ENG not in paths(fake)
    assert src.last_sweep["errors"] == ["daily credit budget spent"]
    assert src.last_sweep["pending"] == 2          # neither game has a board; neither is held


def test_a_rate_limit_ends_the_pass_and_holds_nothing():
    r = routes()
    r[ARM] = RateLimited("odds-api.net /events/4229698994/odds/snapshot: HTTP 429",
                         float("nan"))
    src, fake, _ = make(FakeOan(r))
    src.get_events()
    assert ENG not in paths(fake)
    assert src.last_sweep["errors"] == ["odds-api.net /events/4229698994/odds/snapshot: HTTP 429"]
    assert src.last_sweep["pending"] == 2


def test_a_failed_listing_keeps_the_last_one_and_is_named():
    src, fake, clock = make()
    src.get_events()
    fake.routes["/events"] = RuntimeError("503 listing")
    clock.advance(601)
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert "listing: 503 listing" in src.last_sweep["errors"]
    assert src.last_sweep["fixtures_in_window"] == 2
    src.get_events()                               # the failure is held like the listing
    assert paths(fake).count("/events") == 2


def test_an_incomplete_snapshot_is_used_and_named():
    r = routes()
    r[ENG]["complete"] = False
    src, _, _ = make(FakeOan(r))
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert "1417142635: snapshot incomplete" in src.last_sweep["errors"]
    assert src.last_sweep["failed"] == 0


def test_a_stale_william_hill_side_is_priced_and_counted():
    r = routes()
    r[ENG]["stale_bookmakers"] = ["williamhill"]
    src, _, _ = make(FakeOan(r))
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert src.last_sweep["stale_williamhill"] == 1


def test_priced_at_is_the_oldest_price_behind_the_board():
    src, _, _ = make(); src.get_events()
    eng_wh = datetime(2026, 11, 11, 11, 26, 38, tzinfo=timezone.utc)
    assert src.priced_at == eng_wh and src.priced_at_for("oan:1417142635") == eng_wh
    assert src.priced_at_for("oan:4229698994") is None
    item = feed.build_feed(src, -0.5)
    # 82: William Hill's 11.5 corners are priced (Task 2b).
    assert [(i.event_id, i.priced_at, len(i.bets)) for i in item] == [("oan:1417142635", eng_wh, 82)]


def test_a_book_that_carried_no_lines_does_not_age_the_board():
    """Armenia's empty snapshot still stamps William Hill; with no lines that
    stamp is behind nothing."""
    old = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
    lines = SnapshotLines(markets={"williamhill": {"x": []}},
                          as_of={"williamhill": datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc),
                                 "pinnacle": old},
                          stale_books=frozenset(), refresh_seconds=None, complete=True)
    assert priced_at(lines) == datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def test_the_hills_routes_serve_the_board():
    src, _, _ = make(); app.state.hills_client = src
    rows = TestClient(app).get("/hills/feed?min_edge=-0.5").json()
    assert [(r["event_id"], r["match"], r["priced_at"]) for r in rows] == [
        ("oan:1417142635", "England vs Croatia", "2026-11-11T11:26:38Z")]
    why = TestClient(app).get("/hills/diagnostics").json()
    assert why["verdict"] == "82 value row(s) found." and why["sweep"]["source"] == "odds-api.net"


def test_diagnose_takes_a_fair_side_from_any_vendor():
    stub = SimpleNamespace(book="williamhill", last_sweep={"source": "odds-api.net",
        "fair_source": "odds-api.net:pinnacle", "fixtures_in_window": 2, "called": 2,
        "carried_paddy_prices": 1, "carried_pinnacle_prices": 0, "errors": []})
    v = feed.diagnose(stub)["verdict"]
    assert "Pinnacle (pinnacle)" in v and "ODDSPAPI_FAIR_BOOK" not in v
    stub.last_sweep.update(carried_paddy_prices=0)
    assert "oddspapi-check" not in feed.diagnose(stub)["verdict"]


def test_an_odds_api_net_error_is_not_read_as_pinnacles():
    stub = SimpleNamespace(book="williamhill", last_sweep={"source": "odds-api.net",
        "fair_source": "odds-api.net:pinnacle", "fixtures_in_window": 2, "called": 2,
        "carried_paddy_prices": 1, "carried_pinnacle_prices": 0,
        "errors": ["4229698994: boom"]})
    v = feed.diagnose(stub)["verdict"]
    assert "boom" not in v and "no corners/cards/goals lines" in v
