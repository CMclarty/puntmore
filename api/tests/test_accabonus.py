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
        _sport("soccer_epl", "Soccer"),  # football covered by the other tools
        _sport("soccer_uefa_champs_league", "Soccer"),
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


def test_is_arb_when_cross_book_profitable():
    # Bet365 home 2.10, Pinnacle away 2.10: 1/2.10 + 1/2.10 = 0.952 < 1.0 → arb
    events = [_make_event("arb", "P", "Q", b365_home=2.10, b365_away=1.85, pin_home=1.95, pin_away=2.10)]
    rows = compute_arbs(events, min_edge=-1.0)
    assert any(r.is_arb for r in rows)


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


def test_three_way_arb_detection():
    # Best prices across books: 2.20 home (b365), 4.20 draw (b365), 4.00 away (pin)
    # 1/2.2 + 1/4.2 + 1/4.0 = 0.943 < 1 → arb
    rows = compute_arbs(
        [_three_way_event(b365_home=2.20, b365_draw=4.20, pin_away=4.00)],
        min_edge=-1.0,
    )
    assert rows[0].is_arb is True


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

    def fetch(self, sport_key: str, markets: str, bookmakers: str | None = None) -> list[dict]:
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
