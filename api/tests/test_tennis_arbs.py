"""Tests for AccaBonus arb/value finder (de-vigged Pinnacle vs Bet365)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.tennis.arbs import ArbRow, compute_arbs, _devigd
from app.odds.utils import devigd as _devigd_shared  # noqa: F401 — verify import works

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
) -> dict:
    bookmakers = [
        {
            "key": "bet365",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": home, "price": b365_home},
                        {"name": away, "price": b365_away},
                    ],
                }
            ],
        }
    ]
    if pin_home is not None and pin_away is not None:
        bookmakers.append(
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": pin_home},
                            {"name": away, "price": pin_away},
                        ],
                    }
                ],
            }
        )
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": "2024-06-01T10:00:00Z",
        "bookmakers": bookmakers,
    }


# --- Unit tests: _devigd ---

def test_devigd_symmetric():
    # Even-money market with 2% vig: 1/1.96 + 1/1.96 = 1.0204
    fair_h, fair_a, margin = _devigd(1.96, 1.96)
    assert abs(fair_h - 2.0) < 0.01
    assert abs(fair_a - 2.0) < 0.01
    assert abs(margin - 0.0204) < 0.001


def test_devigd_asymmetric():
    # Favourite/underdog: total impl should be split proportionally
    fair_h, fair_a, margin = _devigd(1.50, 2.80)
    # Check fair prices sum to total_impl (i.e. 1/fair_h + 1/fair_a == 1.0)
    assert abs((1 / fair_h) + (1 / fair_a) - 1.0) < 1e-6
    assert margin > 0


# --- Unit tests: compute_arbs ---

def test_value_bet_detected():
    # Pinnacle 1.92/1.92 (4.2% vig) → fair ~2.0/2.0. Bet365 2.05 home = edge ~2.5%
    events = [_make_event("1", "A", "B", b365_home=2.05, b365_away=1.85, pin_home=1.92, pin_away=1.92)]
    rows = compute_arbs(events, min_edge=0.0)
    assert len(rows) == 1
    assert rows[0].edge_home > 0
    assert rows[0].best_edge > 0


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


# --- Integration tests: GET /tennis/arbs ---

class _FakeArbsClient:
    def __init__(self, events_by_key: dict[str, list[dict]]):
        self._events = events_by_key

    def fetch(self, sport_key: str, markets: str, bookmakers: str | None = None) -> list[dict]:
        return self._events.get(sport_key, [])


def test_endpoint_returns_value_bets():
    event = _make_event("t1", "Djokovic", "Alcaraz", b365_home=2.10, b365_away=1.85, pin_home=1.92, pin_away=1.92)
    app.state.arbs_client = _FakeArbsClient({"tennis_atp": [event]})

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/tennis/arbs?min_edge=0.0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert "fair_home" in row
    assert "fair_away" in row
    assert "edge_home" in row
    assert "best_edge" in row
    assert row["edge_home"] > 0


def test_endpoint_respects_min_edge_filter():
    event = _make_event("t2", "Sinner", "Rune", b365_home=1.80, b365_away=1.80, pin_home=1.95, pin_away=1.95)
    app.state.arbs_client = _FakeArbsClient({"tennis_atp": [event]})

    client = TestClient(app, raise_server_exceptions=True)
    assert client.get("/tennis/arbs?min_edge=0.0").json() == []
    assert len(client.get("/tennis/arbs?min_edge=-0.20").json()) == 1


def test_endpoint_empty_when_no_arbs_client():
    app.state.arbs_client = None

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/tennis/arbs")
    assert resp.status_code == 200
    assert resp.json() == []
