"""AW 26 value snapshots: the Paddy sweep's rows are kept (throttled, pruned),
served at /aw26/snapshots, and a failing write never breaks the sweep."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.aw26 import snapshots
from app.cards.value import CardValueRow
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import ValueLine, ValueSnapshot
from app.time_utils import utcnow

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(bind=engine)
NOW = datetime(2026, 10, 25, 12, 0)


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    snapshots._last.clear()
    yield
    snapshots._last.clear()
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def row(edge=0.05, market_key="alternate_totals_corners", line=9.5, side="Over",
        home="Arsenal", away="Chelsea", event_id="e1", basis="ladder"):
    fair = 2.0
    return CardValueRow(
        event_id=event_id, home_team=home, away_team=away,
        commence_time="2026-10-25T15:00:00Z", sport_key="soccer_epl",
        market_key=market_key, market_label="", line=line, side=side,
        paddy_odds=round(fair * (1 + edge), 4), pinnacle_odds=fair, fair_odds=fair,
        pinnacle_margin=0.05, edge=edge, basis=basis)


def test_record_stores_lines_and_headline():
    db = Session()
    snap = snapshots.record(db, [row(0.05), row(-0.01, side="Under"),
                                 row(0.10, market_key="team_corners_home", line=4.5)],
                            "paddy", NOW)
    assert (snap.n_lines, snap.n_value) == (3, 2)
    assert snap.best_edge == pytest.approx(0.10)
    assert snap.sum_edge_positive == pytest.approx(0.15)
    lines = db.query(ValueLine).order_by(ValueLine.id).all()
    assert [ln.market for ln in lines] == ["corners", "corners", "team_corners"]
    assert [ln.side for ln in lines] == ["over", "under", "over"]
    assert lines[2].team == "home" and lines[0].team is None
    assert lines[0].kickoff == datetime(2026, 10, 25, 15, 0)     # naive UTC
    assert lines[0].sport_key == "soccer_epl"
    assert lines[0].pick == "Over 9.5 corners"
    assert lines[2].pick == "Arsenal Over 4.5 corners"
    db.close()


def test_empty_sweep_is_still_a_snapshot():
    db = Session()
    snap = snapshots.record(db, [], "paddy", NOW)
    assert (snap.n_lines, snap.n_value, snap.best_edge) == (0, 0, None)
    db.close()


def test_latest_and_recent():
    db = Session()
    snapshots.record(db, [row()], "paddy", NOW - timedelta(hours=30))
    snapshots.record(db, [row()], "paddy", NOW - timedelta(hours=2))
    snapshots.record(db, [row()], "market", NOW - timedelta(hours=1))
    assert snapshots.latest(db, "paddy").at == NOW - timedelta(hours=2)
    assert snapshots.latest(db, None).source == "market"
    assert len(snapshots.recent(db, 24, now=NOW)) == 2
    assert len(snapshots.recent(db, 24, now=NOW, source="paddy")) == 1
    db.close()


def test_prune_drops_old_snapshots_and_their_lines():
    db = Session()
    snapshots.record(db, [row(), row()], "paddy", NOW - timedelta(days=40))
    snapshots.record(db, [row()], "paddy", NOW)     # record prunes as it goes
    assert db.query(ValueSnapshot).count() == 1
    assert db.query(ValueLine).count() == 1
    db.close()


def test_throttle(monkeypatch):
    monkeypatch.setattr(settings, "aw26_snapshot_minutes", 15)
    assert snapshots.snapshot_sweep([row()], Session, NOW) is True
    assert snapshots.snapshot_sweep([row()], Session, NOW + timedelta(minutes=14)) is False
    assert snapshots.snapshot_sweep([row()], Session, NOW + timedelta(minutes=15)) is True
    db = Session()
    assert db.query(ValueSnapshot).count() == 2
    db.close()


def test_failed_write_does_not_break_the_sweep(monkeypatch):
    import app.scheduler as sched

    class Client:
        def get_events(self):
            return []

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("app.cards.feed.price_events", lambda events, edge: [row()])
    monkeypatch.setattr(snapshots, "snapshot_sweep", boom)
    monkeypatch.setattr(settings, "cards_discord_alerts", False)
    assert sched.sweep_cards(Client(), None) == 1


def test_sweep_writes_a_snapshot_from_its_own_rows(monkeypatch):
    import app.scheduler as sched

    class Client:
        calls = 0

        def get_events(self):
            Client.calls += 1
            return []

    monkeypatch.setattr("app.cards.feed.price_events", lambda events, edge: [row(), row()])
    monkeypatch.setattr(sched, "SessionLocal", Session)
    monkeypatch.setattr(settings, "cards_discord_alerts", False)
    assert sched.sweep_cards(Client(), None) == 2
    assert Client.calls == 1                       # no extra vendor read
    db = Session()
    assert db.query(ValueSnapshot).one().n_lines == 2
    db.close()


def test_endpoint():
    db = Session()
    snapshots.record(db, [row(0.02), row(0.08, side="Under")], "paddy",
                     utcnow() - timedelta(minutes=5))
    db.close()
    body = TestClient(app).get("/aw26/snapshots?hours=24").json()
    assert len(body["snapshots"]) == 1
    assert body["snapshots"][0]["n_value"] == 2
    assert body["latest"]["source"] == "paddy"
    assert [ln["edge"] for ln in body["latest"]["lines"]] == [0.08, 0.02]   # best first


def test_endpoint_with_nothing_stored():
    body = TestClient(app).get("/aw26/snapshots").json()
    assert body == {"snapshots": [], "latest": None}
