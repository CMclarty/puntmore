"""One tip, one table (`tips`): the endpoint's row is the Bet Finder's row.

The one-off migration from `hub_tips` + `betfinder_tips` (run against a
file-backed SQLite holding data in BOTH old tables, as a live deploy would),
the two routes reading the one table, and the one retention rule."""
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import (
    Column, Date, DateTime, Float, Integer, String, Text, create_engine, inspect, text,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app import tip_migration
from app.betfinder import pipeline
from app.config import settings
from app.database import Base, get_db
from app.hub import service
from app.main import app
from app.models import Tip
from app.time_utils import utcnow


# --- the two old tables, exactly as their models created them ------------------

class Old(DeclarativeBase):
    pass


class OldHubTip(Old):
    __tablename__ = "hub_tips"
    id = Column(Integer, primary_key=True)
    tip_id = Column(String, unique=True, nullable=False)
    client_id = Column(String, nullable=False)
    source_id = Column(String, nullable=False)
    event = Column(String, nullable=False)
    market = Column(String, nullable=False)
    selection = Column(String, nullable=False)
    advised_price = Column(Float, nullable=True)
    tip_time = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    received_at = Column(DateTime, nullable=False, default=utcnow, index=True)


class OldBetFinderTip(Old):
    __tablename__ = "betfinder_tips"
    id = Column(Integer, primary_key=True)
    tip_id = Column(String, unique=True, nullable=False)
    hub_client_id = Column(String, nullable=False)
    source = Column(String, nullable=False, index=True)
    origin = Column(String, nullable=True)
    event_raw = Column(String, nullable=False)
    market_raw = Column(String, nullable=False)
    selection_raw = Column(String, nullable=False)
    market = Column(String, nullable=False)
    dedupe_key = Column(String, nullable=False, index=True)
    tip_time = Column(DateTime, nullable=True)
    tip_day = Column(Date, nullable=True)
    expiry = Column(DateTime, nullable=True)
    advised_price = Column(Float, nullable=True)
    advised_stake_units = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="new", index=True)
    quarantine = Column(String, nullable=True)
    duplicate_of = Column(String, nullable=True)
    event_id = Column(String, nullable=True, index=True)
    event = Column(String, nullable=True)
    home = Column(String, nullable=True)
    away = Column(String, nullable=True)
    selection = Column(String, nullable=True)
    side = Column(String, nullable=True)
    kickoff = Column(DateTime, nullable=True)
    competition = Column(String, nullable=True)
    match_score = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    p_win = Column(Float, nullable=True)
    fair_price = Column(Float, nullable=True)
    edge = Column(Float, nullable=True)
    priced_at = Column(DateTime, nullable=True)
    bet_ids = Column(Text, nullable=False, default="[]")
    outcome = Column(String, nullable=True)
    staked = Column(Float, nullable=True)
    returned = Column(Float, nullable=True)
    settled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


T0 = datetime(2026, 11, 1, 12, 0, 0)


def _wire(tid, source="sharp", event="Arsenal v Chelsea", selection="Arsenal", tip_time=T0):
    return {"tip_id": tid, "source_id": source, "event": event, "market": "Match Result",
            "selection": selection, "advised_price": 2.1, "advised_stake_units": None,
            "tip_time": tip_time.isoformat() + "+00:00", "expiry": None, "origin": "tipster",
            "source_scope": "owner"}


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A file SQLite as a deploy finds it: the old tables, with data, AND the
    new schema already created by the app's create_all (startup order)."""
    monkeypatch.setattr(settings, "hub_owner_client_id", "owner")
    eng = create_engine(f"sqlite:///{tmp_path / 'live.db'}",
                        connect_args={"check_same_thread": False})
    Old.metadata.create_all(bind=eng)
    S = sessionmaker(bind=eng, autoflush=False)
    s = S()
    key = pipeline.dedupe_key("sharp", "Arsenal v Chelsea", "match_result", "Arsenal", T0.date())
    # 1. A settled tip: both rows.
    s.add(OldHubTip(tip_id="tip-settled-1", client_id="owner", source_id="sharp",
                    event="Arsenal v Chelsea", market="Match Result", selection="Arsenal",
                    advised_price=2.1, tip_time=T0.isoformat() + "+00:00",
                    body=json.dumps(_wire("tip-settled-1")), received_at=T0))
    s.add(OldBetFinderTip(
        tip_id="tip-settled-1", hub_client_id="owner", source="sharp", origin="tipster",
        event_raw="Arsenal v Chelsea", market_raw="Match Result", selection_raw="Arsenal",
        market="match_result", dedupe_key=key, tip_time=T0, tip_day=T0.date(),
        advised_price=2.1, status="settled", event_id="ev-1", event="Arsenal v Chelsea",
        selection="Arsenal", side="home", price=2.2, p_win=0.5, edge=0.045,
        bet_ids='["b-1"]', outcome="won", staked=5.0, returned=11.0,
        settled_at=T0 + timedelta(hours=5), created_at=T0 + timedelta(minutes=1),
        updated_at=T0 + timedelta(hours=5)))
    # 2. A quarantined tip whose hub row was already swept (30 days there).
    s.add(OldBetFinderTip(
        tip_id="tip-old-quar", hub_client_id="owner", source="rival", event_raw="Foo v Bar",
        market_raw="BTTS", selection_raw="Yes", market="btts", dedupe_key="k-q",
        tip_time=T0 - timedelta(days=40), tip_day=(T0 - timedelta(days=40)).date(),
        status="quarantined", quarantine="market_not_priced", bet_ids="[]",
        created_at=T0 - timedelta(days=40), updated_at=T0 - timedelta(days=40)))
    # 3. A hub tip the Bet Finder had not ingested yet: the SAME tip as 1,
    #    re-printed, under another tip_id — must come out a duplicate of it.
    s.add(OldHubTip(tip_id="tip-not-seen", client_id="owner", source_id="Sharp",
                    event="ARSENAL vs Chelsea", market="1X2", selection="Arsenal",
                    advised_price=None, tip_time=(T0 + timedelta(minutes=30)).isoformat() + "Z",
                    body=json.dumps(_wire("tip-not-seen", event="ARSENAL vs Chelsea")),
                    received_at=T0 + timedelta(minutes=31)))
    s.commit()
    s.close()
    Base.metadata.create_all(bind=eng)     # what the lifespan does before migrating
    yield eng
    eng.dispose()


def test_a_live_database_is_copied_once_into_tips_and_the_old_tables_stay(live):
    counts = tip_migration.migrate_tips(live)
    assert counts["copied"] == 3 and counts["not_ingested"] == 1
    s = sessionmaker(bind=live)()
    rows = {t.tip_id: t for t in s.query(Tip).all()}
    assert set(rows) == {"tip-settled-1", "tip-old-quar", "tip-not-seen"}

    settled = rows["tip-settled-1"]          # wire columns from hub_tips, state from betfinder_tips
    assert (settled.client_id, settled.source_id, settled.tip_time_raw) == \
        ("owner", "sharp", T0.isoformat() + "+00:00")
    assert json.loads(settled.body)["tip_id"] == "tip-settled-1"
    assert (settled.status, settled.outcome, settled.staked, settled.returned) == \
        ("settled", "won", 5.0, 11.0)
    assert settled.bet_id_list == ["b-1"] and settled.tip_day == T0.date()
    assert settled.received_at == T0

    quar = rows["tip-old-quar"]              # no hub row: its own copies, a rebuilt body
    assert (quar.status, quar.quarantine, quar.source_id) == ("quarantined", "market_not_priced", "rival")
    assert json.loads(quar.body)["migrated"] is True and quar.received_at == T0 - timedelta(days=40)

    unseen = rows["tip-not-seen"]            # waits for the Bet Finder
    assert unseen.status is None and unseen.dedupe_key is None and unseen.event_raw == "ARSENAL vs Chelsea"

    # The old tables are left in place, untouched.
    names = inspect(live).get_table_names()
    assert "hub_tips" in names and "betfinder_tips" in names
    with live.connect() as c:
        assert c.execute(text("SELECT count(*) FROM hub_tips")).scalar() == 2
        assert c.execute(text("SELECT count(*) FROM betfinder_tips")).scalar() == 2

    # The next pass ingests the unseen row and dedupes it against the migrated one.
    assert pipeline.ingest(s, T0 + timedelta(hours=1))["duplicate"] == 1
    s.expire_all()
    unseen = s.query(Tip).filter_by(tip_id="tip-not-seen").one()
    assert (unseen.status, unseen.duplicate_of) == ("duplicate", "tip-settled-1")

    # Once only: a second start copies nothing, and a row swept since is not revived.
    s.query(Tip).filter_by(tip_id="tip-old-quar").delete()
    s.commit()
    assert tip_migration.migrate_tips(live) is None
    assert s.query(Tip).count() == 2
    s.close()


def test_a_failed_migration_rolls_back_whole_and_runs_again(live, monkeypatch):
    real = tip_migration._row
    calls = [0]

    def flaky(hub, bf):
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("disk hiccup")
        return real(hub, bf)

    monkeypatch.setattr(tip_migration, "_row", flaky)
    with pytest.raises(RuntimeError):
        tip_migration.migrate_tips(live)
    with live.connect() as c:
        assert c.execute(text("SELECT count(*) FROM tips")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM schema_migrations")).scalar() == 0
    monkeypatch.setattr(tip_migration, "_row", real)
    assert tip_migration.migrate_tips(live)["copied"] == 3


def test_a_tip_that_arrived_under_the_new_code_is_never_overwritten(live):
    s = sessionmaker(bind=live)()
    service._on_tip(s, "owner", dict(_wire("tip-settled-1"), event="New text v Other"))
    s.commit()
    counts = tip_migration.migrate_tips(live)
    assert counts["copied"] == 2 and counts["already_there"] == 1
    assert s.query(Tip).filter_by(tip_id="tip-settled-1").one().event_raw == "New text v Other"
    s.close()


def test_a_fresh_database_gets_only_the_new_table(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(bind=eng)
    assert tip_migration.migrate_tips(eng) is None
    names = inspect(eng).get_table_names()
    assert "tips" in names
    assert not {"hub_tips", "betfinder_tips", "schema_migrations"} & set(names)
    eng.dispose()


# --- one table, two routes ---------------------------------------------------

@pytest.fixture
def one(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hub_owner_client_id", "owner")
    eng = create_engine(f"sqlite:///{tmp_path / 'one.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    S = sessionmaker(bind=eng, autoflush=False)

    def _db():
        s = S()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    yield S
    app.dependency_overrides.pop(get_db, None)
    eng.dispose()


def test_the_endpoint_row_is_the_bet_finder_row(one):
    s = one()
    service._on_tip(s, "owner", _wire("tip-one-0001"))
    s.commit()                                   # each frame is its own transaction
    service._on_tip(s, "owner", _wire("tip-one-0001", event="re-scraped v text"))   # first kept
    s.commit()
    api = TestClient(app)
    hub = api.get("/2up/hub/tips").json()
    assert [t["tip_id"] for t in hub] == ["tip-one-0001"] and hub[0]["event"] == "Arsenal v Chelsea"
    assert hub[0]["tip"] == _wire("tip-one-0001")
    assert api.get("/2up/betfinder/tips").json() == []      # not ingested yet

    pipeline.ingest(s, T0 + timedelta(hours=1))
    [bf] = api.get("/2up/betfinder/tips").json()
    assert (bf["tip_id"], bf["hub_client_id"], bf["status"], bf["source"]) == \
        ("tip-one-0001", "owner", "new", "sharp")
    # Still one row, now carrying the Bet Finder's columns beside the wire ones.
    [row] = s.query(Tip).all()
    assert row.dedupe_key.startswith("sharp|") and row.source_id == "sharp"
    assert api.get("/2up/hub/tips").json()[0]["tip_id"] == "tip-one-0001"
    s.close()


# --- one retention rule, one sweep -------------------------------------------

def test_retention_keeps_settled_and_decided_and_drops_the_rest_after_90_days(one):
    s = one()
    now = datetime(2026, 11, 5, 12, 0)
    old, recent = now - timedelta(days=91), now - timedelta(days=89)
    statuses = [None, "new", "priced", "quarantined", "duplicate", "expired", "not_placed",
                "decided", "settled"]
    for i, st in enumerate(statuses):
        for age, at in (("old", old), ("new", recent)):
            s.add(Tip(tip_id=f"tip-{age}-{i:04d}", client_id="owner", source_id="sharp",
                      event_raw="a v b", market_raw="1x2", selection_raw="a", tip_time_raw="-",
                      body="{}", received_at=at, status=st, bet_ids="[]"))
    s.commit()
    # The Hub endpoint's hourly sweep is the one that runs the rule.
    assert service.sweep(s, now)["tips"] == 7
    left = {t.tip_id for t in s.query(Tip).all()}
    assert left == {f"tip-new-{i:04d}" for i in range(len(statuses))} | {
        f"tip-old-{statuses.index('decided'):04d}", f"tip-old-{statuses.index('settled'):04d}"}
    # And the Bet Finder pass no longer sweeps on its own.
    assert "swept" not in pipeline.run_pass(s, None, now)
    s.close()
