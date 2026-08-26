"""Tests for AccaBonus arb/value finder (de-vigged Pinnacle vs Bet365)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.accabonus.arbs import compute_arbs, select_accabonus_keys
from app.odds.utils import devigd, devigd_multi

# --- Test DB setup ---

TEST_DB_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def set_fk_pragma(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)


# --- Synthetic event fixtures ---

def _make_event(
    event_id: str,
    home: str,
    away: str,
    b365_home: float,
    b365_away: float,
    pin_home: float | None = None,
    pin_away: float | None = None,
    b365_draw: float | None = None,
    pin_draw: float | None = None,
    sport_title: str = "ATP Test Open",
) -> dict:
    def outcomes(h, a, d):
        out = [{"name": home, "price": h}, {"name": away, "price": a}]
        if d is not None:
            out.append({"name": "Draw", "price": d})
        return out

    bookmakers = [
        {
            "key": "bet365",
            "markets": [{"key": "h2h", "outcomes": outcomes(b365_home, b365_away, b365_draw)}],
        }
    ]
    if pin_home is not None and pin_away is not None:
        bookmakers.append(
            {
                "key": "pinnacle",
                "markets": [{"key": "h2h", "outcomes": outcomes(pin_home, pin_away, pin_draw)}],
            }
        )
    return {
        "id": event_id,
        "sport_title": sport_title,
        "home_team": home,
        "away_team": away,
        "commence_time": "2024-06-01T10:00:00Z",
        "bookmakers": bookmakers,
    }


# --- Unit tests: de-vig ---

def test_devigd_symmetric():
    # Even-money market with 2% vig: 1/1.96 + 1/1.96 = 1.0204
    fair_h, fair_a, margin = devigd(1.96, 1.96)
    assert abs(fair_h - 2.0) < 0.01
    assert abs(fair_a - 2.0) < 0.01
    assert abs(margin - 0.0204) < 0.001


def test_devigd_multi_matches_two_way():
    fair, margin = devigd_multi([1.50, 2.80])
    fair_h, fair_a, margin2 = devigd(1.50, 2.80)
    assert fair == [pytest.approx(fair_h), pytest.approx(fair_a)]
    assert margin == pytest.approx(margin2)


def test_devigd_multi_three_way():
    # Symmetric 3-way with vig: fair probs must sum to exactly 1
    fair, margin = devigd_multi([2.90, 3.30, 2.50])
    assert abs(sum(1 / f for f in fair) - 1.0) < 1e-9
    assert margin > 0


def test_power_method_loads_vig_onto_longshot():
    # vs the multiplicative method, the power method removes MORE vig from the
    # longshot (longer fair odds) and LESS from the favourite (shorter fair).
    fav, dog = 1.25, 4.20
    (fair_fav, fair_dog), _ = devigd_multi([fav, dog])
    total_impl = 1 / fav + 1 / dog
    mult_fav, mult_dog = fav * total_impl, dog * total_impl
    assert fair_fav < mult_fav
    assert fair_dog > mult_dog
    # And both still lengthen vs the raw vigged prices
    assert fair_fav > fav
    assert fair_dog > dog


# --- Unit tests: league selection (bet365 Acca Boost eligibility) ---

def _sport(key, group, active=True, has_outrights=False):
    return {"key": key, "group": group, "active": active, "has_outrights": has_outrights}


def test_selection_includes_eligible_groups_and_leagues():
    sports = [
        _sport("tennis_atp_french_open", "Tennis"),
        _sport("mma_mixed_martial_arts", "Mixed Martial Arts"),
        _sport("basketball_nba", "Basketball"),
        _sport("basketball_euroleague", "Basketball"),
        _sport("americanfootball_nfl", "American Football"),
        _sport("baseball_mlb", "Baseball"),
        _sport("baseball_kbo", "Baseball"),
        _sport("icehockey_nhl", "Ice Hockey"),
    ]
    assert select_accabonus_keys(sports) == [s["key"] for s in sports]


def test_selection_excludes_ineligible():
    sports = [
        _sport("tennis_atp", "Tennis"),  # keep one eligible so fallback doesn't kick in
        _sport("soccer_friendlies", "Soccer"),   # friendlies are not boost-eligible
        _sport("cricket_t20", "Cricket"),                     # cricket not eligible
        _sport("rugbyleague_nrl", "Rugby League"),            # rugby not eligible
        _sport("rugbyunion_premiership", "Rugby Union"),
        _sport("basketball_nbl", "Basketball"),               # NBL not in the terms
        _sport("americanfootball_ncaaf", "American Football"),  # NFL only
        _sport("icehockey_sweden_allsvenskan", "Ice Hockey"),  # NHL/WC/Olympics only
        _sport("basketball_nba_preseason", "Basketball"),     # pre-season excluded
        _sport("tennis_atp_winner", "Tennis"),                # outright key pattern
        _sport("mma_ufc_winner", "Mixed Martial Arts", has_outrights=True),
        _sport("basketball_nba", "Basketball", active=False),  # inactive
    ]
    assert select_accabonus_keys(sports) == ["tennis_atp"]


def test_selection_falls_back_when_empty():
    from app.accabonus.arbs import FALLBACK_SPORT_KEYS
    assert select_accabonus_keys([]) == FALLBACK_SPORT_KEYS


# --- Unit tests: compute_arbs ---

def test_value_bet_detected():
    # Pinnacle 1.92/1.92 (4.2% vig) → fair ~2.0/2.0. Bet365 2.05 home = edge ~2.5%
    events = [_make_event("1", "A", "B", b365_home=2.05, b365_away=1.85, pin_home=1.92, pin_away=1.92)]
    rows = compute_arbs(events, min_edge=0.0)
    assert len(rows) == 1
    assert rows[0].edge_home > 0
    assert rows[0].best_edge > 0
    assert rows[0].edge_draw is None  # 2-way market


def test_no_value_filtered_out():
    # Bet365 below fair on both sides
    events = [_make_event("2", "C", "D", b365_home=1.80, b365_away=1.80, pin_home=1.95, pin_away=1.95)]
    rows = compute_arbs(events, min_edge=0.0)
    assert rows == []


def test_near_value_shown_with_loose_threshold():
    # 5% below fair — passes min_edge=-0.05, filtered at 0.0
    events = [_make_event("3", "E", "F", b365_home=1.85, b365_away=1.85, pin_home=1.95, pin_away=1.95)]
    assert compute_arbs(events, min_edge=0.0) == []
    assert len(compute_arbs(events, min_edge=-0.10)) == 1


def test_missing_pinnacle_skipped():
    events = [_make_event("4", "G", "H", b365_home=1.80, b365_away=2.10)]
    assert compute_arbs(events, min_edge=-1.0) == []


def test_missing_bet365_skipped():
    event = {
        "id": "5",
        "home_team": "X",
        "away_team": "Y",
        "commence_time": "2024-06-01T10:00:00Z",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [{"key": "h2h", "outcomes": [{"name": "X", "price": 1.90}, {"name": "Y", "price": 2.00}]}],
            }
        ],
    }
    assert compute_arbs([event], min_edge=-1.0) == []


def test_sorted_best_edge_first():
    # e1 has higher edge_home than e2
    e1 = _make_event("e1", "A", "B", b365_home=2.10, b365_away=1.80, pin_home=1.92, pin_away=1.92)
    e2 = _make_event("e2", "C", "D", b365_home=2.00, b365_away=1.80, pin_home=1.92, pin_away=1.92)
    rows = compute_arbs([e2, e1], min_edge=-1.0)
    assert rows[0].best_edge >= rows[1].best_edge


def test_no_cross_book_arb_flag_because_the_bet_cannot_be_placed():
    """Pinnacle is the measuring stick here, not a book to bet at.

    A cross-book arb needs the Pinnacle leg, and the Acca Boost only exists at
    bet365 — so the flag advertised a bet nobody could take, as the most
    prominent badge on the row. These prices used to raise it.
    """
    events = [_make_event("arb", "P", "Q", b365_home=2.10, b365_away=1.85,
                          pin_home=1.95, pin_away=2.10)]
    rows = compute_arbs(events, min_edge=-1.0)
    assert rows
    assert not hasattr(rows[0], "is_arb")
    # The value at bet365 is still measured — that is the whole tool.
    assert rows[0].best_edge > 0


def test_edge_formula():
    events = [_make_event("f", "R", "S", b365_home=2.0, b365_away=2.0, pin_home=1.95, pin_away=1.95)]
    rows = compute_arbs(events, min_edge=-1.0)
    assert len(rows) == 1
    r = rows[0]
    assert abs(r.edge_home - (r.bet365_home / r.fair_home - 1)) < 1e-6
    assert abs(r.edge_away - (r.bet365_away / r.fair_away - 1)) < 1e-6


# --- Unit tests: 3-way markets (draw priced by both books, e.g. MMA) ---

def _three_way_event(**kw):
    return _make_event(
        "m3", "Jones", "Aspinall", sport_title="MMA",
        b365_home=kw.get("b365_home", 2.20), b365_away=kw.get("b365_away", 3.40),
        b365_draw=kw.get("b365_draw", 3.50),
        pin_home=kw.get("pin_home", 2.10), pin_away=kw.get("pin_away", 3.40),
        pin_draw=kw.get("pin_draw", 3.40),
    )


def test_three_way_devig_uses_all_outcomes():
    rows = compute_arbs([_three_way_event()], min_edge=-1.0)
    assert len(rows) == 1
    r = rows[0]
    assert r.fair_draw is not None
    # Fair probs over home/draw/away must sum to 1 (margin removed 3-way)
    total = (1 / r.fair_home) + (1 / r.fair_draw) + (1 / r.fair_away)
    assert abs(total - 1.0) < 1e-2  # fair_* are rounded to 3 dp
    assert r.sport_title == "MMA"


def test_three_way_draw_can_be_best_edge():
    # Bet365 prices the draw well above Pinnacle's fair line
    rows = compute_arbs([_three_way_event(b365_draw=4.20)], min_edge=-1.0)
    r = rows[0]
    assert r.edge_draw == pytest.approx(max(r.edge_home, r.edge_away, r.edge_draw))
    assert r.best_edge == pytest.approx(r.edge_draw)


def test_three_way_markets_still_devig_across_all_three_outcomes():
    """The draw leg matters for the DE-VIG even without a cross-book flag:
    the margin belongs to the whole market, so a 3-way must be de-vigged as
    one."""
    rows = compute_arbs(
        [_three_way_event(b365_home=2.20, b365_draw=4.20, pin_away=4.00)],
        min_edge=-1.0,
    )
    assert rows and rows[0].edge_draw is not None


def test_draw_ignored_when_only_one_book_prices_it():
    # Pinnacle 2-way only → de-vig stays 2-way, no draw edge
    ev = _make_event(
        "m1", "I", "J", b365_home=2.0, b365_away=1.9, b365_draw=3.5,
        pin_home=1.95, pin_away=1.95,
    )
    rows = compute_arbs([ev], min_edge=-1.0)
    assert rows[0].edge_draw is None


# --- Integration tests: GET /accabonus/arbs ---

class _FakeArbsClient:
    def __init__(self, events_by_key: dict[str, list[dict]]):
        self._events = events_by_key

    def fetch(self, sport_key: str, markets: str, bookmakers: str | None = None,
              ttl: int | None = None) -> list[dict]:
        return self._events.get(sport_key, [])


def test_endpoint_returns_value_bets():
    event = _make_event("t1", "Djokovic", "Alcaraz", b365_home=2.10, b365_away=1.85, pin_home=1.92, pin_away=1.92)
    app.state.arbs_client = _FakeArbsClient({"tennis_atp": [event]})

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/accabonus/arbs?min_edge=0.0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert "fair_home" in row
    assert "fair_away" in row
    assert "edge_home" in row
    assert "best_edge" in row
    assert "sport_title" in row
    assert row["edge_home"] > 0


def test_endpoint_respects_min_edge_filter():
    event = _make_event("t2", "Sinner", "Rune", b365_home=1.80, b365_away=1.80, pin_home=1.95, pin_away=1.95)
    app.state.arbs_client = _FakeArbsClient({"tennis_atp": [event]})

    client = TestClient(app, raise_server_exceptions=True)
    assert client.get("/accabonus/arbs?min_edge=0.0").json() == []
    assert len(client.get("/accabonus/arbs?min_edge=-0.20").json()) == 1


def test_endpoint_empty_when_no_arbs_client():
    app.state.arbs_client = None

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/accabonus/arbs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_legacy_tennis_routes_still_serve():
    """Stale PWA bundles call /tennis/* until their service worker updates."""
    app.state.arbs_client = None
    client = TestClient(app, raise_server_exceptions=True)
    assert client.get("/tennis/arbs").status_code == 200
    assert client.get("/tennis/feed").status_code == 200


# --- hourly pulls, and the guards that keep them affordable ----------------

def test_the_hourly_ttl_is_passed_through(monkeypatch):
    """AccaBonus reads on its OWN ttl, not the shared 24-hour default.

    Cost is 1 credit per sport key per pull, so this dial is the difference
    between ~450 credits a month and ~10,800.
    """
    from app.config import settings
    from app.routers.accabonus import _fetch_events

    seen = {}

    class _Client:
        quota_remaining = 100_000

        def list_sports(self):
            return []

        def fetch(self, sport_key, markets, bookmakers=None, ttl=None):
            seen["ttl"] = ttl
            seen["bookmakers"] = bookmakers
            return []

    monkeypatch.setattr(settings, "accabonus_sport_keys", "tennis_atp")
    monkeypatch.setattr(settings, "accabonus_cache_ttl", 3600)
    _fetch_events(_Client())
    assert seen["ttl"] == 3600
    # Both books in one call: bet365 is the bet, Pinnacle is the reference.
    assert seen["bookmakers"] == "bet365,pinnacle"


def test_accabonus_stops_at_the_quota_reserve(monkeypatch):
    """An hourly TTL across many keys could quietly eat the allowance the
    coupon fallback and the goalscorer scans depend on."""
    from app.config import settings
    from app.routers.accabonus import _fetch_events

    calls = []

    class _Broke:
        quota_remaining = settings.cards_quota_reserve

        def list_sports(self):
            return []

        def fetch(self, *a, **k):
            calls.append(a)
            return []

    monkeypatch.setattr(settings, "accabonus_sport_keys", "tennis_atp,mma_mixed_martial_arts")
    assert _fetch_events(_Broke()) == []
    assert calls == []


def test_a_failing_sport_key_is_logged_not_swallowed(monkeypatch, caplog):
    """Silently swallowing this is what let the cards client return nothing
    for months."""
    from app.config import settings
    from app.routers.accabonus import _fetch_events

    class _Flaky:
        quota_remaining = 100_000

        def list_sports(self):
            return []

        def fetch(self, sport_key, markets, bookmakers=None, ttl=None):
            raise RuntimeError("vendor down")

    monkeypatch.setattr(settings, "accabonus_sport_keys", "tennis_atp")
    with caplog.at_level("ERROR"):
        assert _fetch_events(_Flaky()) == []
    assert any("tennis_atp" in r.getMessage() for r in caplog.records)


# --- the eligible-key list is checkable against bet365's terms -------------

class _Sports:
    quota_remaining = 100_000

    def __init__(self, sports):
        self._sports = sports

    def list_sports(self):
        return self._sports

    def fetch(self, *a, **k):
        return []


SPORTS = [
    {"key": "soccer_efl_champ", "group": "Soccer", "title": "Championship", "active": True},
    {"key": "soccer_friendlies", "group": "Soccer", "title": "Friendlies", "active": True},
    {"key": "tennis_atp_us_open", "group": "Tennis", "title": "ATP US Open", "active": True},
    {"key": "tennis_itf_men", "group": "Tennis", "title": "ITF Men", "active": True},
    {"key": "mma_mixed_martial_arts", "group": "Mixed Martial Arts", "title": "MMA", "active": True},
    {"key": "basketball_nba", "group": "Basketball", "title": "NBA", "active": True},
    {"key": "basketball_ncaab", "group": "Basketball", "title": "NCAAB", "active": True},
    {"key": "soccer_epl", "group": "Soccer", "title": "EPL", "active": True},
    {"key": "cricket_test_match", "group": "Cricket", "title": "Test", "active": True},
    {"key": "tennis_atp_winner", "group": "Tennis", "title": "ATP Winner",
     "active": True, "has_outrights": True},
]


def test_eligible_keys_reports_what_is_accepted_and_what_just_missed():
    from app.routers.accabonus import eligible_keys
    report = eligible_keys(_Sports(SPORTS))
    accepted = {e["key"] for e in report["accepted"]}
    near = {e["key"] for e in report["rejected_near_misses"]}

    assert "basketball_nba" in accepted
    assert "mma_mixed_martial_arts" in accepted
    # Soccer IS boost-eligible and is the largest pool bet365 offers. It was
    # excluded from June to August 2026 on reasoning that did not hold.
    assert "soccer_epl" in accepted
    assert "basketball_ncaab" in near
    # Outrights carry no h2h to de-vig, so they are not eligible either way.
    assert "tennis_atp_winner" not in accepted
    # Cricket is not boost-eligible and is not a near miss worth listing.
    assert "cricket_test_match" not in accepted and "cricket_test_match" not in near


def test_itf_tennis_is_not_eligible():
    """bet365's list names Grand Slams, ATP, WTA, Davis Cup, BJK Cup and the
    Challenger Tour. ITF is not on it.

    Taking the whole Tennis group used to include ITF, producing value rows
    measured against a boost that would never pay on them.
    """
    from app.accabonus.arbs import _key_eligible
    from app.routers.accabonus import eligible_keys

    accepted = {e["key"] for e in eligible_keys(_Sports(SPORTS))["accepted"]}
    assert "tennis_itf_men" not in accepted
    # ...and the tours that ARE named stay in.
    assert "tennis_atp_us_open" in accepted
    assert _key_eligible("tennis_atp_challenger_biella", "Tennis")
    assert _key_eligible("tennis_wta_cincinnati", "Tennis")


def test_a_pinned_key_list_is_reported_as_such():
    from app.config import settings
    from app.routers.accabonus import eligible_keys
    import pytest as _pytest

    original = settings.accabonus_sport_keys
    settings.accabonus_sport_keys = "tennis_atp,mma_mixed_martial_arts"
    try:
        report = eligible_keys(_Sports(SPORTS))
    finally:
        settings.accabonus_sport_keys = original
    assert report["count"] == 2
    assert "override" in report["source"]



def test_soccer_is_eligible_and_is_the_biggest_pool():
    """bet365 names the Premier League, Championship, League One and Two, the
    FA and EFL Cups from their first rounds, the Scottish Premiership and the
    Champions and Europa Leagues from the group stage — 80+ competitions in all,
    on Full Time Result, which is the h2h market this tool reads.
    """
    from app.accabonus.arbs import _key_eligible
    for key in ("soccer_epl", "soccer_efl_champ", "soccer_england_league1",
                "soccer_england_league2", "soccer_fa_cup", "soccer_spl",
                "soccer_uefa_champs_league", "soccer_spain_la_liga"):
        assert _key_eligible(key, "Soccer"), key


def test_soccer_is_a_prefix_list_not_the_whole_group():
    """The Soccer group also carries friendlies and youth competitions the
    offer does not cover, so `soccer_` alone would sweep them in — the same
    over-inclusion just removed from tennis."""
    from app.accabonus.arbs import _key_eligible
    assert not _key_eligible("soccer_friendlies", "Soccer")
    assert not _key_eligible("soccer_fifa_world_cup_qualifiers", "Soccer")
    assert not _key_eligible("soccer_epl_preseason", "Soccer")


# --- bookmakers and regions are mutually exclusive on BOTH endpoints -------

def _captured_params(monkeypatch, call):
    """Run `call` against a stubbed httpx.get and return the params sent."""
    import app.odds.client as mod
    seen = {}

    class _Resp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return []

    def _get(url, params=None, timeout=None):
        seen.update(params or {})
        return _Resp()

    monkeypatch.setattr(mod.httpx, "get", _get)
    call(mod.TheOddsApiClient("k"))
    return seen


def test_naming_bookmakers_sends_no_region_filter(monkeypatch):
    """bet365 is a `uk` book. Sending regions=eu beside it can filter it out
    entirely — and arbs.py drops any event missing either book, so the whole
    feed came back empty with nothing logged to say why.
    """
    params = _captured_params(
        monkeypatch,
        lambda c: c.fetch("soccer_epl", "h2h", bookmakers="bet365,pinnacle"),
    )
    assert params["bookmakers"] == "bet365,pinnacle"
    assert "regions" not in params


def test_the_per_event_endpoint_follows_the_same_rule(monkeypatch):
    params = _captured_params(
        monkeypatch,
        lambda c: c.fetch_event("soccer_epl", "e1", "alternate_totals_corners",
                                bookmakers="paddypower"),
    )
    assert params["bookmakers"] == "paddypower"
    assert "regions" not in params


def test_a_region_is_still_sent_when_no_bookmakers_are_named(monkeypatch):
    """The coupon fallback names no books and must keep its region filter."""
    params = _captured_params(
        monkeypatch, lambda c: c.fetch("soccer_epl", "h2h,totals"),
    )
    assert params["regions"] == "eu"
    assert "bookmakers" not in params


def test_accabonus_diagnostics_names_the_missing_book(monkeypatch):
    """The failure that just produced a blank feed: bet365 absent because a
    region filter excluded a `uk` book."""
    from app.routers import accabonus as mod

    mod.LAST_PULL.clear()
    mod.LAST_PULL.update({"keys_read": 5, "keys_failed": 0, "keys_skipped": 0,
                          "events": 40, "events_with_both_books": 0})

    class _Req:
        class app:
            class state:
                arbs_client = object()

    verdict = mod.get_accabonus_diagnostics(_Req())["verdict"]
    assert "NONE carried both" in verdict
    assert "uk" in verdict


def test_accabonus_diagnostics_calls_a_working_tool_working():
    from app.routers import accabonus as mod

    mod.LAST_PULL.clear()
    mod.LAST_PULL.update({"keys_read": 5, "keys_failed": 0, "keys_skipped": 0,
                          "events": 40, "events_with_both_books": 40})

    class _Req:
        class app:
            class state:
                arbs_client = object()

    assert mod.get_accabonus_diagnostics(_Req())["verdict"].startswith("WORKING")
    mod.LAST_PULL.clear()
