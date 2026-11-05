"""First-goalscorer value finder: compute, watchlist, scan+store, feed."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import GoalscorerAlert
from app.goalscorers.value import (
    compute_goalscorer_value,
    player_match_score,
    best_player_match,
)
from app.goalscorers.service import build_watchlist, scan_and_store
from app.odds.utils import devigd_multi

# --- In-memory DB shared across the TestClient and direct sessions ---

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
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


# --- Helpers ---

def _market(prices: dict[str, float]) -> dict:
    # Player name in `description` (documented player-prop shape).
    return {
        "key": "player_first_goal_scorer",
        "outcomes": [
            {"description": p, "name": "Yes", "price": pr} for p, pr in prices.items()
        ],
    }


def _event(b365: dict | None, pin: dict | None, *, eid="ev1",
           home="Arsenal", away="Chelsea", commence="2099-08-01T14:00:00Z") -> dict:
    books = []
    if b365 is not None:
        books.append({"key": "bet365", "markets": [_market(b365)]})
    if pin is not None:
        books.append({"key": "pinnacle", "markets": [_market(pin)]})
    return {
        "id": eid, "home_team": home, "away_team": away,
        "commence_time": commence, "bookmakers": books,
    }


class FakeClient:
    """Stands in for TheOddsApiClient for watchlist + per-event pulls."""
    def __init__(self, events_by_sport=None, event_odds=None):
        self._events = events_by_sport or {}
        self._odds = event_odds or {}

    def list_events(self, sport_key, ttl=None):
        return self._events.get(sport_key, [])

    def fetch_event(self, sport_key, event_id, markets, bookmakers=None, ttl=None):
        return self._odds.get(event_id, {})


class RecordingNotifier:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


# --- fuzzy player-name matching ---

def test_player_match_score_pairs_initial_and_full_name():
    # Surname carries the identity: "M. Salah" ↔ "Mohamed Salah".
    assert player_match_score("M. Salah", "Mohamed Salah") >= 0.8


def test_player_match_score_folds_accents():
    assert player_match_score("Gündoğan", "Gundogan") == 1.0
    assert player_match_score("Núñez", "Nunez") == 1.0


def test_player_match_score_separates_distinct_players():
    # Different players must fall below the precision floor, even with short,
    # superficially-similar surnames.
    assert player_match_score("Saka", "Salah") < 0.8


def test_best_player_match_respects_floor():
    cands = ["Mohamed Salah", "Bukayo Saka"]
    name, score = best_player_match("M. Salah", cands, floor=0.8)
    assert name == "Mohamed Salah" and score >= 0.8
    # Nothing clears the floor → (None, best_score).
    name, score = best_player_match("Erling Haaland", cands, floor=0.8)
    assert name is None


# --- compute_goalscorer_value ---

def test_value_excludes_no_goalscorer_and_computes_edge():
    pin = {"Saka": 3.0, "Jackson": 4.0, "No Goalscorer": 2.0}
    b365 = {"Saka": 4.5, "Jackson": 4.2}
    rows = compute_goalscorer_value(_event(b365, pin), min_edge=-1.0)

    players = {r.player for r in rows}
    assert players == {"Saka", "Jackson"}          # no-goal outcome dropped

    fair_list, _ = devigd_multi([3.0, 4.0, 2.0])
    fair = dict(zip(["Saka", "Jackson", "No Goalscorer"], fair_list))
    saka = next(r for r in rows if r.player == "Saka")
    assert saka.fair_price == round(fair["Saka"], 3)
    assert saka.edge == pytest.approx(4.5 / fair["Saka"] - 1, abs=1e-4)
    # Removing positive vig lengthens fair odds beyond the offered price.
    assert saka.fair_price > saka.pinnacle_price


def test_value_sorted_and_threshold_filters():
    pin = {"Saka": 3.0, "Jackson": 4.0, "No Goalscorer": 2.0}
    b365 = {"Saka": 4.5, "Jackson": 4.2}
    rows = compute_goalscorer_value(_event(b365, pin), min_edge=-1.0)
    assert [r.edge for r in rows] == sorted((r.edge for r in rows), reverse=True)

    # An unreachable threshold yields nothing.
    assert compute_goalscorer_value(_event(b365, pin), min_edge=5.0) == []


def test_value_empty_when_a_book_is_missing():
    pin = {"Saka": 3.0, "No Goalscorer": 1.5}
    assert compute_goalscorer_value(_event(None, pin), min_edge=-1.0) == []
    assert compute_goalscorer_value(_event({"Saka": 4.0}, None), min_edge=-1.0) == []


def test_value_pairs_differently_spelled_names():
    # bet365 abbreviates; Pinnacle spells out. They must still be compared.
    pin = {"Mohamed Salah": 3.0, "Bukayo Saka": 4.0, "No Goalscorer": 2.0}
    b365 = {"M. Salah": 6.0, "B. Saka": 5.5}
    rows = compute_goalscorer_value(_event(b365, pin), min_edge=-1.0)

    assert {r.player for r in rows} == {"M. Salah", "B. Saka"}
    salah = next(r for r in rows if r.player == "M. Salah")
    assert salah.matched_player == "Mohamed Salah"
    assert salah.match_score >= 0.8


def test_value_drops_bet365_player_with_no_pinnacle_match():
    # Haaland priced by bet365 but not by Pinnacle → no fair line → dropped,
    # never guessed onto another player.
    pin = {"Mohamed Salah": 3.0, "No Goalscorer": 1.8}
    b365 = {"Erling Haaland": 8.0}
    assert compute_goalscorer_value(_event(b365, pin), min_edge=-1.0) == []


# --- build_watchlist ---

def test_watchlist_keeps_only_fixtures_in_window():
    now = datetime.now(timezone.utc)
    soon = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far = (now + timedelta(hours=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client = FakeClient(events_by_sport={"soccer_epl": [
        {"id": "a", "home_team": "A", "away_team": "B", "commence_time": soon},
        {"id": "b", "home_team": "C", "away_team": "D", "commence_time": far},
    ]})
    watch = build_watchlist(client, ["soccer_epl"], within_hours=30)
    assert [w["event_id"] for w in watch] == ["a"]


# --- scan_and_store + notify ---

def test_scan_stores_rows_and_alerts_new_value():
    pin = {"Saka": 3.0, "Jackson": 4.0, "No Goalscorer": 2.0}
    b365 = {"Saka": 6.0, "Jackson": 5.5}   # both well above fair → value
    client = FakeClient(event_odds={"ev1": _event(b365, pin, eid="ev1")})
    ref = {"sport_key": "soccer_epl", "event_id": "ev1",
           "commence_time": "2099-08-01T14:00:00Z"}
    notifier = RecordingNotifier()

    db = TestSession()
    try:
        stored = scan_and_store(client, ref, db, notifier, min_edge=0.0)
        assert {a.player for a in stored} == {"Saka", "Jackson"}
        assert db.query(GoalscorerAlert).count() == 2
        assert len(notifier.messages) == 1        # one alert for the fixture

        # Re-scan: same players already stored → no repeat alert, rows replaced.
        stored2 = scan_and_store(client, ref, db, notifier, min_edge=0.0)
        assert len(stored2) == 2
        assert db.query(GoalscorerAlert).count() == 2
        assert len(notifier.messages) == 1        # still just the first
    finally:
        db.close()


# --- feed router ---

def test_feed_returns_stored_rows_best_edge_first():
    db = TestSession()
    try:
        db.add_all([
            GoalscorerAlert(event_id="ev1", match_name="A v B", player="Low",
                            bet365_price=4.0, pinnacle_price=4.0, fair_price=4.2,
                            edge=0.01, inplay=False),
            GoalscorerAlert(event_id="ev1", match_name="A v B", player="High",
                            bet365_price=6.0, pinnacle_price=5.0, fair_price=5.2,
                            edge=0.15, inplay=True),
        ])
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    resp = client.get("/goalscorers/feed")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["player"] for r in body] == ["High", "Low"]   # edge desc
    assert body[0]["inplay"] is True
