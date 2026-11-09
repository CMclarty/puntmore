"""AW 26's shared test kit: the in-memory database, the autouse `setup`
(which pins the clock), the five-leg refund slip and the body builders.

Every AW 26 test module imports what it uses by name, `setup` included: an
autouse fixture imported into a module applies there."""
import io
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.aw26 import service
from app.config import settings
from app.database import Base, get_db
from app.main import app

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)


@event.listens_for(engine, "connect")
def _fks_on(dbapi_conn, _):
    # As production opens SQLite (app/database.py).
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


Session = sessionmaker(bind=engine)


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


def clock(monkeypatch, when: datetime) -> None:
    """AW 26 reads the time as `service.utcnow()`: make it `when` (naive UTC)."""
    monkeypatch.setattr(service, "utcnow", lambda: when)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    clock(monkeypatch, datetime(2026, 11, 9, 13, 0))
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        for t in ("aw_leg_lines", "value_lines", "value_snapshots"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))


LEGS = [  # (event, market, selection, odds, fair)
    ("Arsenal v Chelsea", "Total Match Corners", "Over 8.5", 1.30, 1.28),
    ("Man City v Liverpool", "Total Match Corners", "Over 9.5", 1.36, 1.36),
    ("Everton v Leeds", "Total Match Cards", "Over 3.5", 1.33, 1.33),
    ("Brighton v Fulham", "Total Match Corners", "Under 11.5", 1.25, 1.25),
    ("Wolves v Burnley", "Total Goals", "Over 1.5", 1.30, 1.30),
]
ODDS = [o for *_, o, _ in LEGS]
FAIRS = [f for *_, f in LEGS]


def _body(receipt="REF-1", offer="refund", stake=50, fairs=True, legs=LEGS):
    out = []
    for ev, m, s, odds, fair in legs:
        leg = {"event": ev, "market": m, "selection": s, "odds": odds}
        if fairs is True or (fairs and ev in fairs):
            leg["fair_odds"] = fair
        out.append(leg)
    return {"receipt": receipt, "offer": offer, "stake": stake, "legs": out}


def _record(client, body=None, **kw):
    return client.post("/aw26/slips/record", json=body or _body(**kw))


def _edit_body(slip, leg_changes):
    """The page's PATCH: every leg as it stands, with `leg_changes` applied by
    position ({i: {field: value}})."""
    legs = []
    for i, lg in enumerate(slip["legs"]):
        d = {k: lg[k] for k in ("id", "event", "market", "selection", "odds", "fair_odds", "result")}
        d.update(leg_changes.get(i, {}))
        legs.append(d)
    return {"legs": legs}


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, "PNG")
    return buf.getvalue()


def leg(event, market, selection, odds, fair=None, **extra) -> dict:
    """A leg body. `extra` carries kickoff, kind, line, side, fixture_id and
    fair_assumed."""
    out = {"event": event, "market": market, "selection": selection, "odds": odds}
    if fair is not None:
        out["fair_odds"] = fair
    out.update(extra)
    return out


def slip_body(receipt, legs, offer="other", stake=10, placed_at="09/11/2026 12:00", **extra) -> dict:
    """A record body: `legs` are `leg(...)` dicts."""
    return {"receipt": receipt, "offer": offer, "stake": stake, "placed_at": placed_at,
            "legs": list(legs), **extra}
