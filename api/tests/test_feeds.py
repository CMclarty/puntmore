"""Tests for the v3 feed endpoints: /accabonus/feed and /cards/feed."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# --- Stub clients (duck-typed for app.state) ---

class StubArbsClient:
    """Returns the given events for every sport key fetch."""
    def __init__(self, events):
        self._events = events
        self._served = False

    def fetch(self, sport_key, markets, bookmakers=None):
        # Serve the batch once; other sport keys return empty.
        if self._served:
            return []
        self._served = True
        return self._events


class StubCardsClient:
    def __init__(self, events):
        self._events = events

    def get_events(self):
        return self._events


def _h2h_event(event_id, home, away, b365_home, b365_away, pin_home, pin_away):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": "2026-06-14T15:00:00Z",
        "bookmakers": [
            {"key": "bet365", "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": b365_home},
                {"name": away, "price": b365_away},
            ]}]},
            {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": pin_home},
                {"name": away, "price": pin_away},
            ]}]},
        ],
    }


def _totals_event(event_id, home, away, pin_over, pin_under, paddy_over, paddy_under, line=3.5):
    def outcomes(over, under):
        return [
            {"name": "Over", "point": line, "price": over},
            {"name": "Under", "point": line, "price": under},
        ]
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": "2026-06-14T15:00:00Z",
        "sport_key": "soccer_epl",
        "bookmakers": [
            {"key": "pinnacle", "markets": [{"key": "alternate_totals", "outcomes": outcomes(pin_over, pin_under)}]},
            {"key": "paddypower", "markets": [{"key": "alternate_totals", "outcomes": outcomes(paddy_over, paddy_under)}]},
        ],
    }


# --- /accabonus/feed ---

def test_accabonus_feed_empty_without_client():
    app.state.arbs_client = None
    assert client.get("/accabonus/feed").json() == []


def test_accabonus_feed_picks_best_side():
    # Bet365 home 2.10 vs fair ~2.05 → home is the value side
    app.state.arbs_client = StubArbsClient([
        _h2h_event("e1", "Alcaraz", "Sinner", 2.10, 1.80, 2.00, 1.90),
    ])
    rows = client.get("/accabonus/feed?min_edge=-0.5").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["match"] == "Alcaraz vs Sinner"
    assert row["pick"] == "Alcaraz"
    assert row["odds"] == 2.10
    assert row["value"] > 0
    assert "sport" in row


def test_accabonus_feed_respects_min_edge():
    app.state.arbs_client = StubArbsClient([
        # Bet365 well below fair on both sides → big negative edge
        _h2h_event("e1", "A", "B", 1.50, 1.50, 2.00, 2.00),
    ])
    rows = client.get("/accabonus/feed?min_edge=0.0").json()
    assert rows == []


def test_accabonus_feed_flags_arb():
    # 1/2.30 + 1/1.90 = 0.961 < 1 → guaranteed cross-book arb
    app.state.arbs_client = StubArbsClient([
        _h2h_event("e1", "A", "B", 2.30, 1.60, 2.10, 1.90),
    ])
    rows = client.get("/accabonus/feed?min_edge=-0.5").json()
    assert len(rows) == 1
    assert rows[0]["is_arb"] is True


# --- /cards/feed ---

def test_cards_feed_empty_without_client():
    app.state.cards_client = None
    assert client.get("/cards/feed").json() == []


def test_cards_feed_groups_by_match():
    app.state.cards_client = StubCardsClient([
        _totals_event("e1", "Arsenal", "Chelsea", 1.90, 1.90, 2.10, 1.70),
    ])
    rows = client.get("/cards/feed?min_edge=-0.5").json()
    assert len(rows) == 1
    g = rows[0]
    assert g["match"] == "Arsenal vs Chelsea"
    assert g["event_id"] == "e1"
    assert len(g["bets"]) >= 1
    # bets sorted by value desc; best_value matches the top bet
    values = [b["value"] for b in g["bets"]]
    assert values == sorted(values, reverse=True)
    assert g["best_value"] == values[0]
    # Over 2.10 vs fair 2.00 → the Over side leads
    assert g["bets"][0]["pick"].startswith("Over")
    assert g["bets"][0]["odds"] == 2.10


def test_cards_feed_sorted_by_best_value():
    app.state.cards_client = StubCardsClient([
        _totals_event("e1", "A", "B", 1.90, 1.90, 1.95, 1.85),   # small edge
        _totals_event("e2", "C", "D", 1.90, 1.90, 2.20, 1.60),   # big edge
    ])
    rows = client.get("/cards/feed?min_edge=-0.5").json()
    assert [r["event_id"] for r in rows] == ["e2", "e1"]


def test_feeds_require_auth():
    from app.security import require_user
    app.dependency_overrides.pop(require_user, None)
    try:
        assert client.get("/accabonus/feed").status_code == 401
        assert client.get("/tennis/feed").status_code == 401  # legacy alias guarded too
        assert client.get("/cards/feed").status_code == 401
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"
