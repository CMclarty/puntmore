"""The Finder routes (routers/finder.py): rows and statuses in on the ingest
key, the feed and the board out on the `finder` grant — and nobody else."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import apikeys
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.security import user_permissions
from tests.twoup_helpers import Session, engine

NOW = datetime.now(timezone.utc).replace(microsecond=0)
KICKOFF = NOW + timedelta(hours=6)
TIP = {
    "tip_id": "t1", "origin": "tipster-a", "sport": "football", "bookmaker": "bet365",
    "event": "A v B", "kickoff": KICKOFF.isoformat(), "market": "Match Result",
    "selection": "A", "odds": 3.0, "fair": 2.5, "advised_stake": 5, "reason": "price beats fair",
    "status": "offered",
}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    Base.metadata.create_all(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    # The real guards: a key is resolved, a grant is read. `grant()` stands in
    # for a logged-in session holding a grant.
    app.dependency_overrides.pop(user_permissions, None)
    monkeypatch.setattr(settings, "saves_ingest_key", "k-ingest")
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def api():
    return TestClient(app)


@pytest.fixture
def ingest_headers():
    return {"Authorization": "Bearer k-ingest"}


@contextmanager
def grant(perms):
    app.dependency_overrides[user_permissions] = lambda: perms
    try:
        yield
    finally:
        app.dependency_overrides.pop(user_permissions, None)


def _client_key(name, tools):
    s = Session()
    try:
        return {"Authorization": f"Bearer {apikeys.create(s, name, tools)[1]}"}
    finally:
        s.close()


def test_rows_come_in_on_the_ingest_key_and_are_served_to_the_finder_grant(api, ingest_headers):
    r = api.post("/finder/rows", json={"rows": [TIP]}, headers=ingest_headers)
    assert r.status_code == 200 and r.json()["stored"] == 1
    with grant("finder"):
        assert [x["tip_id"] for x in api.get("/finder/feed").json()["rows"]] == ["t1"]
        assert api.get("/finder/board").json()["rows"][0]["status"] == "offered"


def test_a_session_cannot_post_and_an_ingest_key_cannot_read(api, ingest_headers):
    with grant("finder"):
        assert api.post("/finder/rows", json={"rows": [TIP]}).status_code == 401
    assert api.get("/finder/feed", headers=ingest_headers).status_code in (401, 403)


def test_other_grants_and_the_value_grant_see_nothing(api):
    for perms in ("coupons", "accabonus", "value"):
        with grant(perms):
            assert api.get("/finder/feed").status_code == 403


def test_the_placer_reports_placed_and_the_row_leaves_the_feed(api, ingest_headers):
    api.post("/finder/rows", json={"rows": [TIP]}, headers=ingest_headers)
    r = api.post("/finder/rows/t1/status", json={"status": "placed", "at": NOW.isoformat()}, headers=ingest_headers)
    assert r.status_code == 200 and r.json()["status"] == "placed"
    with grant("finder"):
        assert api.get("/finder/feed").json()["rows"] == []
    assert api.post("/finder/rows/t1/status", json={"status": "gone", "at": NOW.isoformat()}, headers=ingest_headers).status_code == 409
    assert api.post("/finder/rows/nope/status", json={"status": "gone", "at": NOW.isoformat()}, headers=ingest_headers).status_code == 404
    assert api.post("/finder/rows/t1/status", json={"status": "offered", "at": NOW.isoformat()}, headers=ingest_headers).status_code == 422


def test_the_jobs_are_registered_only_with_the_flag(monkeypatch):
    from app.scheduler import start_scheduler
    s = start_scheduler(odds_provider=None)
    try:
        assert s.get_job("finder_expire") is not None and s.get_job("finder_sweep") is not None
    finally:
        s.shutdown(wait=False)
    monkeypatch.setattr(settings, "enable_finder", False)
    s = start_scheduler(odds_provider=None)
    try:
        assert s.get_job("finder_expire") is None and s.get_job("finder_sweep") is None
    finally:
        s.shutdown(wait=False)


# --- beyond the plan's five ---------------------------------------------------

def test_the_post_reports_refusals_and_duplicates_and_the_board_carries_every_column(api, ingest_headers):
    api.post("/finder/rows", json={"rows": [TIP]}, headers=ingest_headers)
    api.post("/finder/rows/t1/status", json={"status": "placed", "at": NOW.isoformat()}, headers=ingest_headers)
    twin = {**TIP, "tip_id": "t2", "origin": "tipster-b"}
    out = api.post("/finder/rows", json={"rows": [TIP, {**TIP, "tip_id": "t3"}, {**twin, "tip_id": "t4"}]},
                   headers=ingest_headers).json()
    assert out == {"stored": 2, "updated": 0, "refused": [["t1", "final"]], "duplicates": ["t4"]}
    with grant("finder"):
        rows = {r["tip_id"]: r for r in api.get("/finder/board").json()["rows"]}
    t1 = rows["t1"]
    assert t1["edge"] == pytest.approx(0.2) and t1["advised_stake"] == 5
    assert t1["kickoff"].endswith("Z") or t1["kickoff"].endswith("+00:00")
    assert datetime.fromisoformat(t1["expires_at"].replace("Z", "+00:00")) == KICKOFF
    for col in ("tip_id", "origin", "sport", "bookmaker", "event", "home", "away", "event_id",
                "kickoff", "market", "selection", "line", "side", "odds", "fair", "edge",
                "advised_stake", "reason", "status", "expires_at", "asked_at", "answered_at",
                "seen_at", "created_at", "updated_at"):
        assert col in t1, col
    assert rows["t4"]["status"] == "rejected" and rows["t4"]["reason"] == "duplicate of t3"


def test_a_bad_row_is_refused_whole(api, ingest_headers):
    for bad in ({**TIP, "sport": "tennis"}, {**TIP, "status": "placed"}, {**TIP, "odds": 0.5}):
        assert api.post("/finder/rows", json={"rows": [bad]}, headers=ingest_headers).status_code == 422


def test_the_board_reads_days_back(api, ingest_headers):
    api.post("/finder/rows", json={"rows": [TIP]}, headers=ingest_headers)
    with grant("finder"):
        assert len(api.get("/finder/board", params={"days": 1}).json()["rows"]) == 1
        assert api.get("/finder/board", params={"days": 0}).status_code == 422
        assert api.get("/finder/board", params={"days": 31}).status_code == 422


def test_the_placers_key_reads_and_reports_a_finder_only_key_cannot_post(api):
    placer = _client_key("placer", "finder,ingest")
    reader = _client_key("reader", "finder")
    assert api.post("/finder/rows", json={"rows": [TIP]}, headers=placer).status_code == 200
    assert [r["tip_id"] for r in api.get("/finder/feed", headers=placer).json()["rows"]] == ["t1"]
    assert api.get("/finder/feed", headers=reader).status_code == 200
    assert api.post("/finder/rows", json={"rows": [TIP]}, headers=reader).status_code == 403
    assert api.post("/finder/rows/t1/status", json={"status": "gone", "at": NOW.isoformat()},
                    headers=reader).status_code == 403
    assert api.get("/finder/feed", headers=_client_key("valuer", "value")).status_code == 403


def test_the_routes_are_mounted_with_the_flag_on():
    from tests.test_removed_modules import _paths
    assert sorted(p for p in set(_paths(app.routes)) if p.startswith("/finder")) == [
        "/finder/board", "/finder/feed", "/finder/rows", "/finder/rows/{tip_id}/status"]
