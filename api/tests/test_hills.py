"""Hills — the Paddy tool priced against William Hill (cards/ with `book`).

Same pipeline, a different book: the source asks OddsPapi for William Hill's
board (resolved against the plan) beside Pinnacle's, the events carry it under
"williamhill", pricing reads that book only, and the page's routes read
app.state.hills_client. Paddy's own path is unchanged and covered elsewhere.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.cards import feed, laddered
from app.cards import oddspapi as op
from app.main import app

client = TestClient(app)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
CATALOGUE = [
    {"marketId": 10803, "marketName": "Total Corners", "handicap": 9.5, "sportId": 10,
     "outcomes": [{"outcomeId": 10803, "outcomeName": "Over"},
                  {"outcomeId": 10804, "outcomeName": "Under"}]},
]


def _p(price):
    return {"players": {"0": {"price": price, "active": True}}}


def _board(book, over, under, hours=20):
    return {
        "fixtureId": "id1", "participant1Name": "Arsenal", "participant2Name": "Chelsea",
        "tournamentId": 17,
        "startTime": (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "bookmakerOdds": {book: {"markets": {
            "10803": {"outcomes": {"10803": _p(over), "10804": _p(under)}}}}},
    }


def _event(**books):
    """A joined event with each named book quoting corners 3.5 (one rung: the
    pricer de-vigs Pinnacle's own rung, which is all these tests need)."""
    def market(over, under):
        return [{"key": "alternate_totals_corners", "outcomes": [
            {"name": "Over", "point": 3.5, "price": over},
            {"name": "Under", "point": 3.5, "price": under}]}]
    return {
        "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
        "sport_key": "soccer_epl",
        "bookmakers": [{"key": k, "markets": market(*v)} for k, v in books.items()],
    }


class StubSource:
    def __init__(self, events, book):
        self._events, self.book, self.last_sweep = events, book, {}

    def get_events(self):
        return [dict(e) for e in self._events]


# --- the source ---------------------------------------------------------------

def test_events_carry_william_hills_lines_under_its_own_key():
    events, counts = op.to_events([_board("williamhill", 2.10, 1.70)],
                                  op.corner_card_markets(CATALOGUE), {},
                                  now=NOW, window_hours=48, book=op.WILLIAM_HILL)
    assert counts["carried_paddy_prices"] == 1
    assert events[0]["bookmakers"][0]["key"] == "williamhill"
    # A Paddy-only board carries nothing for William Hill.
    _, counts = op.to_events([_board("paddypower", 2.10, 1.70)],
                             op.corner_card_markets(CATALOGUE), {},
                             now=NOW, window_hours=48, book=op.WILLIAM_HILL)
    assert counts["carried_paddy_prices"] == 0


def test_a_variant_slug_the_plan_grants_is_keyed_by_the_plain_book():
    events, _ = op.to_events([_board("williamhill+30", 2.10, 1.70)],
                             op.corner_card_markets(CATALOGUE), {},
                             now=NOW, window_hours=48, book="williamhill+30")
    assert events[0]["bookmakers"][0]["key"] == "williamhill"


class FakeOddsPapi:
    requests_today = 0

    def __init__(self):
        self.asked: list[str] = []

    def catalogue(self):
        return CATALOGUE

    def participants(self):
        return []

    def tournaments(self):
        return []

    def resolve_book(self, wanted):
        return wanted

    def odds_by_tournaments(self, ids, bookmaker):
        self.asked.append(bookmaker)
        prices = {"williamhill": (2.10, 1.70), "pinnacle": (1.95, 1.85)}.get(bookmaker)
        return [_board(bookmaker, *prices, hours=20)] if prices else []


def test_the_hills_source_asks_for_william_hill_and_pinnacle_only(monkeypatch):
    monkeypatch.setattr(op, "_tournament_entries", lambda: ["17"])
    monkeypatch.setattr(op, "_tournament_ids", lambda: [17])
    monkeypatch.setattr(op.settings, "oddspapi_fair_book", "pinnacle")
    fake = FakeOddsPapi()
    src = op.OddsPapiPaddySource(fake, book=op.WILLIAM_HILL)
    monkeypatch.setattr(op, "to_events", lambda *a, **kw: _capture(kw, a))
    src.get_events()
    assert fake.asked == ["williamhill", "pinnacle"]
    assert CAPTURED["book"] == "williamhill"
    assert src.last_sweep["book"] == "williamhill" and src.label == "William Hill"


CAPTURED: dict = {}


def _capture(kw, args):
    CAPTURED.clear()
    CAPTURED.update(kw)
    return [], {"fixtures_in_window": 0, "carried_paddy_prices": 0,
                "carried_pinnacle_prices": 0}


# --- pricing ------------------------------------------------------------------

def test_hills_prices_william_hill_and_never_paddy():
    event = _event(pinnacle=(1.95, 1.85), paddypower=(1.50, 2.40), williamhill=(2.30, 1.55))
    rows = feed.price_events([event], -0.5, op.WILLIAM_HILL)
    over = next(r for r in rows if r.side == "Over")
    assert over.paddy_odds == 2.30          # William Hill's price, not Paddy's 1.50


def test_each_book_keeps_its_own_diagnostics_tally():
    laddered.LAST_RUN.clear()
    feed.price_events([_event(pinnacle=(1.95, 1.85), williamhill=(2.30, 1.55))], -0.5,
                      op.WILLIAM_HILL)
    assert laddered.LAST_RUNS["williamhill"]["with_both_books"] == 1
    assert laddered.LAST_RUN == {}          # Paddy's tally untouched
    assert laddered.LAST_RUNS["paddypower"] is laddered.LAST_RUN


def test_most_rows_are_paddys_only(monkeypatch):
    sentinel = SimpleNamespace(edge=1.0, market_key="most_corners")
    monkeypatch.setattr(feed, "most_value", lambda events, min_edge: [sentinel])
    event = _event(pinnacle=(1.95, 1.85), paddypower=(2.30, 1.55), williamhill=(2.30, 1.55))
    assert sentinel in feed.price_events([event], -0.5)
    assert sentinel not in feed.price_events([event], -0.5, op.WILLIAM_HILL)


def test_diagnostics_name_william_hill():
    src = StubSource([], op.WILLIAM_HILL)
    src.last_sweep = {"source": "oddspapi", "fixtures_in_window": 3, "called": 3,
                      "carried_paddy_prices": 0}
    verdict = feed.diagnose(src)["verdict"]
    assert "William Hill" in verdict and "Paddy" not in verdict


# --- routes -------------------------------------------------------------------

def test_the_hills_feed_reads_the_hills_client_and_paddys_its_own():
    event = _event(pinnacle=(1.95, 1.85), paddypower=(1.50, 2.40), williamhill=(2.30, 1.55))
    app.state.hills_client = StubSource([event], op.WILLIAM_HILL)
    app.state.cards_client = None
    try:
        rows = client.get("/hills/feed?min_edge=-0.5").json()
        assert rows and rows[0]["match"] == "Arsenal vs Chelsea"
        assert {b["odds"] for b in rows[0]["bets"]} <= {2.30, 1.55}
        assert client.get("/paddy/feed").json() == []
        assert "verdict" in client.get("/hills/diagnostics").json()
    finally:
        app.state.hills_client = None


def test_hills_needs_its_own_grant():
    from app.security import user_permissions
    app.dependency_overrides[user_permissions] = lambda: "cards"
    try:
        assert client.get("/hills/feed").status_code == 403
        assert client.get("/paddy/feed").status_code == 200
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
