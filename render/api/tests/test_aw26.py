"""AW 26: a page over Callum's stored slips. A slip is recorded (by HubAI on
an API client key granted `aw26`, or anyone signed in with the grant), priced
once every leg has a fair, settled from its legs' results, corrected on the
page and deleted there. Render does no other work for it."""
import importlib
import io
import os
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import apikeys
from app.aw26.offers import expected_value, settle
from app.aw26.service import parse_when, summary
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import AwLeg, AwSlip
from app.security import require_user, user_permissions

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


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
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


# --- recording and listing ---------------------------------------------------


def test_a_recorded_slip_is_listed_priced_and_counted():
    client = TestClient(app)
    r = _record(client)
    assert r.status_code == 200, r.text
    slip = r.json()
    want = round(expected_value("refund", 50, ODDS, FAIRS), 2)
    assert slip["ev"] == want and slip["ev_note"] is None
    assert slip["user"] == "testuser" and slip["status"] == "open" and len(slip["legs"]) == 5
    # Nothing is read or looked up for a new slip: the legs are as recorded.
    assert all(lg["kind"] is None and lg["fixture_id"] is None for lg in slip["legs"])

    body = client.get("/aw26/slips").json()
    assert [s["receipt"] for s in body["slips"]] == ["REF-1"]
    total = body["summary"]["total"]
    assert (total["slips"], total["legs"], total["staked"], total["ev"], total["open"]) == (1, 5, 50, want, 1)
    assert "ev_by_source" not in body["summary"]["total"]
    assert "value_on_offer" not in body["summary"]


def test_the_same_receipt_twice_is_refused_and_stored_once():
    client = TestClient(app)
    assert _record(client, receipt="DUP").status_code == 200
    r = _record(client, receipt="DUP")
    assert r.status_code == 409
    assert len(client.get("/aw26/slips").json()["slips"]) == 1


def test_an_unknown_offer_is_refused():
    assert _record(TestClient(app), offer="bogus").status_code == 400


def test_an_acca_with_no_stake_is_staked_at_50_and_other_is_not():
    client = TestClient(app)
    legs = [("A v B", "Total Corners", "Over 8.5", 1.5, 1.4)]
    assert _record(client, receipt="S50", stake=None, legs=legs).json()["stake"] == 50
    assert _record(client, receipt="SOTH", offer="other", stake=None, legs=legs).json()["stake"] is None


# --- pricing -----------------------------------------------------------------


def test_one_leg_without_a_fair_leaves_the_slip_unpriced_until_it_is_added():
    client = TestClient(app)
    priced = {ev for ev, *_ in LEGS[:4]}
    slip = _record(client, fairs=priced).json()
    assert slip["ev"] is None and slip["ev_note"] == "1 of 5 legs have no fair price yet"
    assert client.get("/aw26/slips").json()["summary"]["total"]["unpriced"] == 1

    r = client.patch(f"/aw26/slips/{slip['id']}", json=_edit_body(slip, {4: {"fair_odds": FAIRS[4]}}))
    assert r.status_code == 200, r.text
    assert r.json()["ev"] == round(expected_value("refund", 50, ODDS, FAIRS), 2)
    assert r.json()["ev_note"] is None and r.json()["edited"] is True


def test_a_slip_off_its_offers_terms_says_so():
    client = TestClient(app)
    slip = _record(client, legs=LEGS[:4]).json()            # a refund needs five legs
    assert slip["ev"] == round(expected_value("refund", 50, ODDS[:4], FAIRS[:4]), 2)
    assert slip["ev_note"] == "valued without the refund: refund needs exactly 5 legs (has 4)"


# --- settling ----------------------------------------------------------------


def test_leg_results_settle_the_slip_as_the_offer_says():
    client = TestClient(app)
    slip = _record(client).json()
    results = {i: {"result": "won"} for i in range(5)}
    results[2] = {"result": "lost"}
    r = client.patch(f"/aw26/slips/{slip['id']}", json=_edit_body(slip, results))
    out = r.json()
    want = settle("refund", 50, [(o, results[i]["result"]) for i, o in enumerate(ODDS)])
    assert (out["status"], out["returns"]) == want == ("refunded", 37.5)
    assert out["settled_at"] is not None
    total = client.get("/aw26/slips").json()["summary"]["total"]
    assert total["profit"] == -12.5 and total["open"] == 0


def test_a_pending_leg_keeps_the_slip_open():
    client = TestClient(app)
    slip = _record(client, offer="other", legs=LEGS[:2]).json()
    r = client.patch(f"/aw26/slips/{slip['id']}", json=_edit_body(slip, {0: {"result": "won"}}))
    assert (r.json()["status"], r.json()["returns"]) == ("open", None)


# --- deleting and the photo --------------------------------------------------


def test_delete_removes_the_slip_and_the_image_route_is_404_without_a_photo():
    client = TestClient(app)
    slip = _record(client).json()
    assert client.get(f"/aw26/slips/{slip['id']}/image").status_code == 404
    assert client.delete(f"/aw26/slips/{slip['id']}").json() == {"deleted": slip["id"]}
    assert client.get("/aw26/slips").json()["slips"] == []
    db = Session()
    assert db.query(AwLeg).count() == 0
    db.close()
    assert client.delete(f"/aw26/slips/{slip['id']}").status_code == 404


def _old_slip(db, image_path=None):
    """A slip as the retired ledger stored it: legs read into kind/line/side,
    a fixture id, a photo."""
    slip = AwSlip(placed_at=datetime(2026, 10, 25, 12), user="callum", receipt="OLD-1",
                  offer="other", stake=10, status="open", image_path=image_path, ev=0.25)
    slip.legs.append(AwLeg(position=0, event="Arsenal v Chelsea", market="Total Match Corners",
                           selection="Over 9.5", odds=2.05, fair_odds=2.0, kind="corners",
                           line=9.5, side="over", fixture_id="id1000", result="pending"))
    db.add(slip)
    db.commit()
    return slip.id, slip.legs[0].id


def test_an_old_slip_loads_with_its_photo_and_its_read_legs(tmp_path):
    photo = tmp_path / "aw26" / "old.png"
    photo.parent.mkdir()
    photo.write_bytes(b"\x89PNG old")
    db = Session()
    sid, _ = _old_slip(db, str(photo))
    db.close()
    client = TestClient(app)
    slip = client.get("/aw26/slips").json()["slips"][0]
    assert slip["has_image"] and slip["legs"][0]["kind"] == "corners"
    assert slip["legs"][0]["fixture_id"] == "id1000" and slip["legs"][0]["line"] == 9.5
    assert client.get(f"/aw26/slips/{sid}/image").content == b"\x89PNG old"
    # The row and its photo go together.
    assert client.delete(f"/aw26/slips/{sid}").status_code == 200
    assert not os.path.exists(photo)


def test_an_old_legs_read_fields_stand_until_its_words_are_corrected():
    db = Session()
    sid, _ = _old_slip(db)
    db.close()
    client = TestClient(app)
    slip = client.get("/aw26/slips").json()["slips"][0]
    kept = client.patch(f"/aw26/slips/{sid}", json=_edit_body(slip, {0: {"result": "won"}})).json()
    assert (kept["legs"][0]["kind"], kept["legs"][0]["fixture_id"]) == ("corners", "id1000")
    moved = client.patch(f"/aw26/slips/{sid}", json=_edit_body(kept, {0: {"selection": "Over 10.5"}})).json()
    leg = moved["legs"][0]
    assert (leg["kind"], leg["line"], leg["side"]) == (None, None, None)
    assert leg["fixture_id"] == "id1000"            # same event: its fixture stands


def test_deleting_an_old_slip_takes_its_unmapped_leg_lines_with_it():
    """aw_leg_lines has no model now, but production still has rows in it
    pointing at aw_legs. Its foreign key cascades (as the retired model
    created it) and foreign keys are on, so the delete goes through and the
    rows go with their leg."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE value_snapshots (id INTEGER NOT NULL, at DATETIME NOT NULL, "
            "source VARCHAR NOT NULL, n_lines INTEGER NOT NULL, n_value INTEGER NOT NULL, "
            "best_edge FLOAT, sum_edge_positive FLOAT NOT NULL, PRIMARY KEY (id))"))
        conn.execute(text(
            "CREATE TABLE value_lines (id INTEGER NOT NULL, snapshot_id INTEGER NOT NULL, "
            "event_id VARCHAR, home VARCHAR NOT NULL, away VARCHAR NOT NULL, kickoff DATETIME, "
            "sport_key VARCHAR, market VARCHAR NOT NULL, team VARCHAR, line FLOAT, side VARCHAR, "
            "pick VARCHAR, offered_odds FLOAT NOT NULL, fair_odds FLOAT NOT NULL, "
            "edge FLOAT NOT NULL, basis VARCHAR, PRIMARY KEY (id), "
            "FOREIGN KEY(snapshot_id) REFERENCES value_snapshots (id) ON DELETE CASCADE)"))
        conn.execute(text(
            "CREATE TABLE aw_leg_lines (id INTEGER NOT NULL, leg_id INTEGER NOT NULL, "
            "line_id INTEGER, source VARCHAR NOT NULL, snapshot_at DATETIME NOT NULL, "
            "offered_odds_at_snapshot FLOAT NOT NULL, fair_odds FLOAT NOT NULL, "
            "edge_at_snapshot FLOAT NOT NULL, match_score FLOAT NOT NULL, basis VARCHAR, "
            "PRIMARY KEY (id), UNIQUE (leg_id), "
            "FOREIGN KEY(leg_id) REFERENCES aw_legs (id) ON DELETE CASCADE, "
            "FOREIGN KEY(line_id) REFERENCES value_lines (id) ON DELETE SET NULL)"))
    db = Session()
    sid, leg_id = _old_slip(db)
    db.execute(text("INSERT INTO value_snapshots VALUES (1, '2026-10-25 11:55:00', 'paddy', 1, 1, 0.05, 0.05)"))
    db.execute(text("INSERT INTO value_lines (id, snapshot_id, home, away, market, offered_odds, "
                    "fair_odds, edge) VALUES (1, 1, 'Arsenal', 'Chelsea', 'corners', 2.1, 2.0, 0.05)"))
    db.execute(text("INSERT INTO aw_leg_lines VALUES (1, :leg, 1, 'paddy', '2026-10-25 11:55:00', "
                    "2.1, 2.0, 0.05, 1.0, 'ladder')"), {"leg": leg_id})
    db.commit()
    db.close()

    client = TestClient(app)
    assert len(client.get("/aw26/slips").json()["slips"]) == 1        # loads beside them
    assert client.delete(f"/aw26/slips/{sid}").status_code == 200
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM aw_leg_lines")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM aw_legs")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM value_lines")).scalar() == 1   # untouched


# --- who may call ------------------------------------------------------------


def test_the_routes_need_the_aw26_grant():
    app.dependency_overrides[user_permissions] = lambda: "coupons+pnl"
    try:
        client = TestClient(app)
        assert client.get("/aw26/slips").status_code == 403
        assert _record(client).status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


def test_hubai_records_on_an_api_key_granted_aw26_under_its_own_name():
    db = Session()
    _, key = apikeys.create(db, "hubai", "aw26")
    _, other = apikeys.create(db, "reader", "shop")
    db.close()
    app.dependency_overrides.pop(require_user, None)
    app.dependency_overrides.pop(user_permissions, None)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}
    r = client.post("/aw26/slips/record", json=_body(receipt="H-1"), headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["user"] == "api:hubai"
    assert client.get("/aw26/slips", headers=headers).json()["slips"][0]["receipt"] == "H-1"
    assert client.post("/aw26/slips/record", json=_body(receipt="H-1"), headers=headers).status_code == 409
    assert client.get("/aw26/slips", headers={"Authorization": f"Bearer {other}"}).status_code == 403
    assert client.get("/aw26/slips").status_code == 401


# --- what stays retired ------------------------------------------------------


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, "PNG")
    return buf.getvalue()


def test_render_reads_no_photo_and_runs_no_feeder_route(monkeypatch):
    """Slips are recorded by HubAI or corrected on the page: no photo upload,
    no ingest, fairs, results or snapshots route, on any key."""
    monkeypatch.setattr(settings, "saves_ingest_key", "test-ingest-key")
    client = TestClient(app)
    assert client.get("/aw26/slips").status_code == 200          # the router is mounted
    r = client.post("/aw26/slips", data={"offer": "refund"},
                    files={"file": ("slip.png", _png(), "image/png")})
    assert r.status_code in (404, 405)
    key = {"X-API-Key": "test-ingest-key"}
    for path in ("/aw26/slips/ingest", "/aw26/fairs", "/aw26/results"):
        assert client.post(path, json={}, headers=key).status_code in (404, 405), path
    for path in ("/aw26/snapshots", "/aw26/results-check", "/aw26/open-leagues"):
        assert client.get(path, headers=key).status_code in (404, 405), path


@pytest.mark.parametrize("name", [
    "app.providers.slip_ocr", "app.routers.aw26_ingest", "app.aw26.attribution",
    "app.aw26.legs", "app.aw26.marry", "app.aw26.results", "app.aw26.snapshots",
])
def test_the_retired_modules_do_not_import(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(name)


def test_no_ocr_provider_reads_a_slip():
    from app.providers.mock_ocr import MockOCRProvider
    assert not hasattr(MockOCRProvider, "read_slip")


def test_nothing_aw26_is_scheduled():
    from app.scheduler import start_scheduler

    class Cards:
        def get_events(self):
            return []

    s = start_scheduler(odds_provider=None, cards_client=Cards())
    try:
        ids = [job.id for job in s.get_jobs()]
    finally:
        s.shutdown(wait=False)
    assert "cards_sweep" in ids and "paddy_health" in ids
    assert not [i for i in ids if "aw26" in i]
    assert not hasattr(settings, "aw26_snapshot_minutes")


# --- the production tables ---------------------------------------------------

# aw_slips and aw_legs as they stand in the production database, read off the
# archived models (archive/aw26, api/app/models.py). A model edit that drifts
# from these breaks loading Callum's existing rows: no column is added,
# renamed or dropped, and a nullable column stays nullable.
TABLES = {
    "aw_slips": ["id", "created_at", "placed_at", "user", "image_path", "receipt", "offer",
                 "stake", "potential_returns", "status", "returns", "settled_at", "ev",
                 "ev_note", "edited"],
    "aw_legs": ["id", "slip_id", "position", "event", "market", "selection", "odds", "kickoff",
                "kind", "line", "side", "fair_odds", "fixture_id", "result"],
}
NOT_NULL = {
    "aw_slips": {"id", "placed_at", "user", "offer", "status", "edited"},
    "aw_legs": {"id", "slip_id", "position", "event", "result"},
}


@pytest.mark.parametrize("table", sorted(TABLES))
def test_the_models_match_the_production_tables(table):
    t = Base.metadata.tables.get(table)
    assert t is not None, f"no model for {table}"
    assert [c.name for c in t.columns] == TABLES[table]
    assert {c.name for c in t.columns if not c.nullable} == NOT_NULL[table]


def test_the_other_aw26_tables_have_no_model():
    import app.models  # noqa: F401  (every model registered)
    for table in ("aw_fairs", "aw_leg_lines", "value_snapshots", "value_lines"):
        assert table not in Base.metadata.tables, table


# --- reading times and the figures -------------------------------------------


def test_parse_when_reads_the_terminals_london_time():
    ref = datetime(2026, 10, 21, 12, 0)
    assert parse_when("21/10/2026 14:32", ref) == datetime(2026, 10, 21, 13, 32)   # BST
    assert parse_when("Sat 15:00", ref) == datetime(2026, 10, 24, 14, 0)
    assert parse_when("2026-11-02T15:00:00Z", ref) == datetime(2026, 11, 2, 15, 0)
    assert parse_when("not a time", ref) is None


def test_summary_counts_days_from_the_first_slip_against_the_targets():
    s = AwSlip(placed_at=datetime(2026, 10, 20, 12), user="u", offer="refund", stake=50,
               status="open", ev=15.15)
    out = summary([s], date(2026, 10, 21))
    assert out["total"]["days"] == 2 and out["total"]["ev_target"] == 2000
    assert out["targets"] == {"ev_per_day": 1000.0, "profit": 100_000.0}
    assert out["today"]["slips"] == 0 and out["total"]["ev"] == 15.15
