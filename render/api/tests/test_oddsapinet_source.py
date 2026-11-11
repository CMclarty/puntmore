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


# --- the warmish board: slow background pre-loads (Task 5b) -----------------

def _warm_routes():
    """Four William Hill games: two far out (24 h, 30 h), two near (2 h, 3 h)."""
    from datetime import timedelta
    from tests.oan_fixtures import ARMENIA_EMPTY, NOW
    games = {"901": 24, "902": 30, "903": 2, "904": 3}
    items = [{"event_id": eid, "league": "UEFA Nations League", "home_team": f"Home {eid}",
              "away_team": f"Away {eid}", "sport": "soccer", "event_state": "scheduled",
              "start_time": int((NOW + timedelta(hours=h)).timestamp()),
              "bookmakers": ["williamhill", "pinnacle"]} for eid, h in games.items()]
    r = {"/events": {"items": items, "next_cursor": None}}
    for eid in games:
        r[f"/events/{eid}/odds/snapshot"] = dict(ARMENIA_EMPTY, event_id=eid)
    return r


def snapshots(fake):
    return [p for p in paths(fake) if p.endswith("/odds/snapshot")]


def test_warm_fetches_a_missing_board_and_a_stale_far_one_but_not_a_fresh_one():
    src, fake, clock = make(FakeOan(_warm_routes()))
    assert src.warm() == {"due": 4, "fetched": 4, "skipped": None}   # no boards yet
    ages = {"901": 3 * 3600, "902": 3600, "903": 20 * 60, "904": 10 * 60}
    for eid, age in ages.items():
        src._fetched["oan:" + eid] = clock.mono() - age
    fake.calls.clear()
    assert src.warm() == {"due": 2, "fetched": 2, "skipped": None}
    # Near (2 h to kick-off, 20 min old) first, then far (3 h old).
    assert snapshots(fake) == ["/events/903/odds/snapshot", "/events/901/odds/snapshot"]
    assert src.last_sweep["errors"] == [] and src.last_sweep["called"] == 4


def test_warm_is_capped_at_max_per_pass_and_holds_still_apply():
    src, fake, clock = make(FakeOan(_warm_routes()), max_per_pass=2)
    src._held["oan:903"] = clock.mono() + 600
    assert src.warm() == {"due": 3, "fetched": 2, "skipped": None}
    assert snapshots(fake) == ["/events/904/odds/snapshot", "/events/901/odds/snapshot"]


def test_warm_stops_at_the_reserve():
    fake = FakeOan(_warm_routes())
    fake.requests_today = fake.daily_budget - 1999
    src, fake, _ = make(fake)
    assert src.warm() == {"due": 0, "fetched": 0, "skipped": "reserve"}
    assert fake.calls == []


def test_warm_never_spends_into_the_reserve():
    fake = FakeOan(_warm_routes())
    fake.requests_today = fake.daily_budget - 2003      # 2,003 left; the listing costs 1
    src, fake, _ = make(fake)
    assert src.warm() == {"due": 4, "fetched": 2, "skipped": None}
    assert fake.budget_left() == 2000
    assert src.warm()["skipped"] == "reserve"


def test_warm_skips_while_a_page_pass_runs():
    src, fake, _ = make(FakeOan(_warm_routes()))
    src._refreshing = True
    assert src.warm() == {"due": 0, "fetched": 0, "skipped": "busy"}
    assert fake.calls == []


def test_a_page_request_during_a_warm_starts_no_second_pass():
    started = []
    src, fake, _ = make(FakeOan(_warm_routes()), runner=started.append, first_wait=0)
    real_get = fake.get

    def get(path, **params):
        if path.endswith("/odds/snapshot") and not started:
            src.get_events()                       # a page load mid-warm
        return real_get(path, **params)

    fake.get = get
    assert src.warm()["fetched"] == 4
    assert started == [] and not src._refreshing


def test_a_spent_budget_ends_the_warm_and_is_named():
    r = _warm_routes()
    r["/events/903/odds/snapshot"] = BudgetSpent("daily credit budget spent")
    src, fake, _ = make(FakeOan(r))
    assert src.warm() == {"due": 4, "fetched": 0, "skipped": None}
    assert snapshots(fake) == ["/events/903/odds/snapshot"]
    assert src.last_sweep["errors"] == ["daily credit budget spent"]
    assert not src._refreshing


def test_a_warmed_board_is_served_without_a_page_fetch():
    src, fake, _ = make()
    src.warm()
    assert {"oan:1417142635", "oan:4229698994"} <= set(src.boards)
    assert src.priced_at is not None
    fake.calls.clear()
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert snapshots(fake) == []


# --- the page's spend: near reuse and the settling floor (final review, B) ----

def _near_routes(refresh=60):
    """Two William Hill games inside the near window (2 h, 3 h), whose
    snapshots name the vendor's 60 s refresh."""
    r = _warm_routes()
    r["/events"]["items"] = [i for i in r["/events"]["items"] if i["event_id"] in ("903", "904")]
    for eid in ("901", "902"):
        del r[f"/events/{eid}/odds/snapshot"]
    for eid in ("903", "904"):
        r[f"/events/{eid}/odds/snapshot"]["target_refresh_interval_seconds"] = refresh
    return r


def test_the_new_page_settings_default_to_300():
    from app.config import Settings
    assert Settings.model_fields["oddsapinet_page_near_ttl_seconds"].default == 300
    assert Settings.model_fields["oddsapinet_settle_floor"].default == 300


def test_the_page_reuses_a_near_board_for_the_near_ttl():
    src, fake, clock = make(FakeOan(_near_routes()))
    src.get_events()
    assert len(snapshots(fake)) == 2
    clock.advance(299); src.get_events()
    assert len(snapshots(fake)) == 2               # 299 s: reused, not the vendor's 120 s
    clock.advance(2); src.get_events()
    assert len(snapshots(fake)) == 4               # 301 s: fetched again


def test_a_far_board_keeps_the_vendors_refresh():
    src, fake, clock = make()                      # England, 31 h out, refresh 3,599 s
    src.get_events()
    clock.advance(3000); src.get_events()
    assert paths(fake).count(ENG) == 1
    clock.advance(601); src.get_events()
    assert paths(fake).count(ENG) == 2


def test_the_page_pass_stops_at_the_floor_and_serves_the_boards_held():
    fake = FakeOan()
    fake.requests_today = fake.daily_budget - 302   # the listing and ONE snapshot reach 300
    src, fake, clock = make(fake)
    assert src.get_events() == []
    assert paths(fake) == ["/events", ARM]
    assert src.last_sweep["budget_spent"] is True and src.last_sweep["credit_floor"] == 300
    assert src.last_sweep["pending"] == 1
    assert fake.budget_left() == 300


def test_at_the_floor_a_page_load_sends_nothing_and_keeps_its_boards():
    src, fake, clock = make()
    src.get_events()
    fake.requests_today = fake.daily_budget - 300
    fake.calls.clear()
    clock.advance(3601)                            # listing and both boards due
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert fake.calls == []
    assert src.last_sweep["budget_spent"] is True


def test_warm_stops_at_the_floor_whatever_its_reserve(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "oddsapinet_warm_reserve", 0)
    fake = FakeOan(_warm_routes())
    fake.requests_today = fake.daily_budget - 302
    src, fake, _ = make(fake)
    assert src.warm()["fetched"] == 1              # the listing, one snapshot, then 300 left
    assert fake.budget_left() == 300
    fake.calls.clear()
    assert src.warm()["skipped"] == "reserve" and fake.calls == []


def test_a_legs_fetch_still_spends_below_the_floor():
    fake = FakeOan()
    fake.requests_today = fake.daily_budget - 10
    src, fake, _ = make(fake)
    legs = src.hills_legs("oan:1417142635", "England", "Croatia")
    assert legs and paths(fake) == [ENG]


def test_the_verdict_names_a_spent_budget_and_the_reset_before_still_fetching():
    started = []
    src, fake, _ = make(runner=started.append, first_wait=0)
    src.get_events()                               # listed; the pass has not run
    assert feed.diagnose(src)["verdict"].startswith("Still fetching")
    fake.requests_today = fake.daily_budget
    fake.calls.clear()
    src.get_events()
    assert fake.calls == [] and src.last_sweep["pending"] == 2
    assert feed.diagnose(src)["verdict"] == (
        "Today's odds-api.net credits are spent; prices resume at 00:00 UTC.")
    fake.requests_today = fake.daily_budget - 300
    src.get_events()
    assert feed.diagnose(src)["verdict"] == (
        "Today's odds-api.net credits for the board are spent (the last 300 are kept "
        "for legs and settling); prices resume at 00:00 UTC.")


def test_a_budget_spent_mid_pass_gives_the_spent_verdict():
    r = routes()
    r[ARM] = BudgetSpent("daily credit budget spent")

    class Spent(FakeOan):
        def get(self, path, **params):
            if path == ARM:
                self.requests_today = self.daily_budget
            return super().get(path, **params)

    src, fake, _ = make(Spent(r))
    src.get_events()
    assert feed.diagnose(src)["verdict"].startswith("Today's odds-api.net credits are spent")


# --- never busy for ever (final review, E) ------------------------------------

def _raise_once(src, name):
    real = getattr(src, name)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(f"{name} broke")
        return real()

    setattr(src, name, flaky)


@pytest.mark.parametrize("name", ["_summarise", "_prune"])
def test_a_pass_whose_summary_raises_does_not_leave_the_source_busy(name):
    src, fake, _ = make()
    src._refresh_listing()
    src._refreshing = True
    _raise_once(src, name)
    src._pass()                                    # logged, not raised
    assert not src._refreshing and src._pass_done.is_set()
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]


def test_a_page_load_whose_summary_raises_starts_no_stuck_pass():
    started = []
    src, fake, _ = make(runner=started.append, first_wait=0)
    _raise_once(src, "_summarise")
    with pytest.raises(RuntimeError):
        src.get_events()
    assert not src._refreshing
    src.get_events()
    assert len(started) == 1


# --- an incomplete snapshot is reused 120 s at most (final review, G) ---------

@pytest.mark.parametrize("mark", [{"complete": False}, {"next_cursor": "c2"}])
def test_an_incomplete_board_is_reused_for_120_s_only(mark):
    r = routes()
    r[ENG].update(mark)                            # far out: the vendor says 3,599 s
    src, fake, clock = make(FakeOan(r))
    src.get_events()
    assert "1417142635: snapshot incomplete" in src.last_sweep["errors"]
    clock.advance(119); src.get_events()
    assert paths(fake).count(ENG) == 1
    clock.advance(2); src.get_events()
    assert paths(fake).count(ENG) == 2


def test_an_incomplete_target_board_is_fetched_again_after_120_s():
    r = routes()
    r[ENG]["complete"] = False
    src, fake, clock = make(FakeOan(r))
    src.hills_legs("oan:1417142635", "England", "Croatia")
    clock.advance(119); src.hills_legs("oan:1417142635", "England", "Croatia")
    assert paths(fake).count(ENG) == 1
    clock.advance(2); src.hills_legs("oan:1417142635", "England", "Croatia")
    assert paths(fake).count(ENG) == 2


def test_warm_fetches_an_incomplete_board_again_after_120_s():
    r = _warm_routes()
    r["/events/901/odds/snapshot"]["complete"] = False          # 24 h out
    src, fake, clock = make(FakeOan(r))
    src.warm()
    clock.advance(121)
    fake.calls.clear()
    assert src.warm()["fetched"] == 1
    assert snapshots(fake) == ["/events/901/odds/snapshot"]


# --- a 429 holds every fetch until Retry-After (final review, H) --------------

def _limited(retry_after):
    return RateLimited("odds-api.net /events/4229698994/odds/snapshot: HTTP 429", retry_after)


def test_a_page_load_inside_the_429_hold_sends_nothing_and_after_it_does():
    r = routes()
    r[ARM] = _limited(120.0)
    src, fake, clock = make(FakeOan(r))
    src.get_events()
    assert paths(fake) == ["/events", ARM]
    fake.routes[ARM] = routes()[ARM]
    clock.advance(119); src.get_events()
    assert paths(fake) == ["/events", ARM]          # held: no request, so none billed
    assert src.last_sweep["pending"] == 2
    clock.advance(2); src.get_events()
    assert paths(fake)[2:] == [ARM, ENG]


@pytest.mark.parametrize("retry_after", [None, float("nan"), float("inf"), -5.0])
def test_a_missing_or_unusable_retry_after_holds_60_s(retry_after):
    r = routes()
    r[ARM] = _limited(retry_after)
    src, fake, clock = make(FakeOan(r))
    src.get_events()
    fake.routes[ARM] = routes()[ARM]
    clock.advance(59); src.get_events()
    assert len(fake.calls) == 2
    clock.advance(2); src.get_events()
    assert len(fake.calls) == 4


def test_the_429_hold_is_capped_at_600_s():
    r = routes()
    r[ARM] = _limited(86400.0)
    src, fake, clock = make(FakeOan(r))
    src.get_events()
    fake.routes[ARM] = routes()[ARM]
    clock.advance(599); src.get_events()
    assert len(fake.calls) == 2
    clock.advance(2); src.get_events()
    assert ARM in paths(fake)[2:]


def test_the_hold_covers_the_target_the_warm_job_and_results():
    src, fake, clock = make()
    src.get_events()
    fake.routes[ENG] = _limited(300.0)
    clock.advance(3601)                            # England's board is due
    assert src.hills_legs("oan:1417142635", "England", "Croatia")   # the held board's legs
    fake.calls.clear()
    fake.routes[ENG] = routes()[ENG]
    assert src.hills_legs("oan:1417142635", "England", "Croatia")
    assert src.warm() == {"due": 0, "fetched": 0, "skipped": "held"}
    with pytest.raises(RateLimited):
        src.leg_results("oan:1595650036", [("alternate_totals_corners:9.5", "Under")])
    src.get_events()
    assert fake.calls == []
    clock.advance(301)
    src.hills_legs("oan:1417142635", "England", "Croatia")
    assert paths(fake) == [ENG]


def test_a_429_on_the_listing_holds_the_snapshots_too():
    src, fake, clock = make()
    fake.routes["/events"] = RateLimited("odds-api.net /events: HTTP 429", 30.0)
    src.get_events()
    assert paths(fake) == ["/events"]
    assert src.hills_legs("oan:1417142635", "England", "Croatia") == []
    assert paths(fake) == ["/events"]


# --- the first listing (final review, I) ---------------------------------------

def test_a_failed_first_listing_is_asked_again_after_60_s():
    r = routes()
    r["/events"] = RuntimeError("503 listing")
    src, fake, clock = make(FakeOan(r))
    assert src.get_events() == []
    clock.advance(59); src.get_events()
    assert paths(fake) == ["/events"]
    fake.routes["/events"] = routes()["/events"]
    clock.advance(2)
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert paths(fake)[:2] == ["/events", "/events"]


def test_after_one_good_listing_a_failed_one_is_held_600_s():
    src, fake, clock = make()
    src.get_events()
    fake.routes["/events"] = RuntimeError("503 listing")
    clock.advance(601); src.get_events()           # fails
    clock.advance(599); src.get_events()
    assert paths(fake).count("/events") == 2
    clock.advance(2); src.get_events()
    assert paths(fake).count("/events") == 3
