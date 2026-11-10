"""Paddy's board, kept (app/cards/history.py; Callum, 10 Nov 2026): every line
the sweep prices is stored when its price changes, before kick-off, for 14 days
after kick-off, so a slip read late is valued at the fair it was struck at."""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.cards import history
from app.cards.value import CardValueRow
from app.main import app
from app.models import PaddyLine

from tests.aw26_kit import Session, setup  # noqa: F401  (autouse: database)

NOW = datetime(2026, 11, 10, 12, 0)


def row(odds=1.85, fair=1.70, line=10.5, side="Over", event="e1", ko="2026-11-10T19:45:00Z",
        market="alternate_totals_corners"):
    return CardValueRow(event_id=event, home_team="Arsenal", away_team="Chelsea", commence_time=ko,
                        sport_key="soccer_epl", market_key=market, market_label="", line=line, side=side,
                        paddy_odds=odds, pinnacle_odds=fair, fair_odds=fair, pinnacle_margin=0.0,
                        edge=odds / fair - 1)


def setup_function():
    history.reset()


def test_a_line_is_kept_once_and_again_only_when_its_price_moves():
    db = Session()
    assert history.record(db, [row()], NOW) == 1
    assert history.record(db, [row()], NOW + timedelta(minutes=2)) == 0          # unchanged
    assert history.record(db, [row(fair=1.704)], NOW + timedelta(minutes=4)) == 0  # noise under a penny
    assert history.record(db, [row(odds=1.80)], NOW + timedelta(minutes=6)) == 1
    assert db.query(PaddyLine).count() == 2


def test_after_kick_off_nothing_is_kept():
    db = Session()
    assert history.record(db, [row(ko="2026-11-10T11:30:00Z")], NOW) == 0


def test_a_restart_does_not_write_the_board_again():
    db = Session()
    history.record(db, [row(), row(line=9.5)], NOW)
    history.reset()                                                            # the process restarts
    assert history.record(db, [row(), row(line=9.5)], NOW + timedelta(minutes=2)) == 0


def test_lines_are_dropped_fourteen_days_after_kick_off():
    db = Session()
    history.record(db, [row(ko="2026-11-12T19:45:00Z")], NOW)
    later = datetime(2026, 11, 26, 20, 0)                                      # 14 days and a bit after
    history.record(db, [row(event="e2", ko="2026-11-27T15:00:00Z")], later)
    assert [r.event_id for r in db.query(PaddyLine).all()] == ["e2"]


def test_the_board_at_a_time_is_each_lines_price_then():
    db = Session()
    history.record(db, [row(odds=1.85, fair=1.70)], NOW)
    history.record(db, [row(odds=1.80, fair=1.75)], NOW + timedelta(hours=2))
    history.record(db, [row(line=9.5, side="Under", odds=2.0, fair=2.1)], NOW + timedelta(hours=3))
    board = history.board_at(db, NOW + timedelta(hours=1))
    assert len(board) == 1
    game = board[0]
    assert (game.event_id, game.home, game.away, game.commence_time) == ("e1", "Arsenal", "Chelsea", "2026-11-10T19:45:00Z")
    by_line = {(b.line, b.side): b for b in game.bets}
    assert (by_line[(10.5, "Over")].odds, by_line[(10.5, "Over")].fair) == (1.85, 1.70)
    assert by_line[(10.5, "Over")].pick == "Over 10.5 corners"
    # Not priced yet at that time: its first price, the nearest there is.
    assert (by_line[(9.5, "Under")].odds, by_line[(9.5, "Under")].fair) == (2.0, 2.1)
    # After kick-off a game is on no board.
    assert history.board_at(db, datetime(2026, 11, 10, 20, 0)) == []


def test_the_route_serves_it_on_paddy_and_not_on_hills():
    db = Session()
    history.record(db, [row()], NOW)
    client = TestClient(app)
    r = client.get("/paddy/board-at", params={"at": "2026-11-10T13:00:00"})
    assert r.status_code == 200
    assert r.json()[0]["bets"][0]["fair"] == 1.70
    assert client.get("/hills/board-at", params={"at": "2026-11-10T13:00:00"}).status_code == 404


def test_the_sweep_keeps_every_line_it_prices_down_to_the_floor(monkeypatch):
    from app import scheduler
    from app.cards import feed
    seen = {}

    def price(events, min_edge, book=None):
        seen["floor"] = min_edge
        return [row(), row(line=9.5, odds=1.40, fair=1.90)]      # the second far below fair

    class Client:
        def get_events(self):
            return []

    monkeypatch.setattr(feed, "price_events", price)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(scheduler, "utcnow", lambda: NOW)
    scheduler.sweep_cards(Client())
    assert seen["floor"] == history.FLOOR
    assert Session().query(PaddyLine).count() == 2
