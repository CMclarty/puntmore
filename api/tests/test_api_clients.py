"""API clients: keys hashed at rest, one resolution seam, the CLI.

conftest bypasses auth for every test; these pop that bypass so the real
`user_permissions` / `require_user` run.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import apikeys
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import ApiClient
from app.security import require_user, user_permissions

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def real_auth():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides.pop(require_user, None)
    app.dependency_overrides.pop(user_permissions, None)
    app.state.arbs_client = None
    app.state.cards_client = None
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def _client(name="bot", tools="shop,aw26"):
    db = Session()
    try:
        _, key = apikeys.create(db, name, tools)
    finally:
        db.close()
    return key


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_key_is_stored_only_as_its_hash():
    key = _client()
    db = Session()
    row = db.query(ApiClient).one()
    assert row.key_hash == hashlib.sha256(key.encode()).hexdigest()
    assert key not in (row.key_hash, row.name, row.permissions)
    assert row.permissions == "aw26+shop"
    db.close()


def test_bearer_and_x_api_key_both_admit_a_granted_client():
    key = _client()
    api = TestClient(app)
    assert api.get("/shop", headers=bearer(key)).status_code == 200
    assert api.get("/shop", headers={"X-API-Key": key}).status_code == 200
    assert api.get("/aw26/slips", headers=bearer(key)).status_code == 200


def test_a_client_is_held_to_its_grant():
    key = _client(tools="shop")
    api = TestClient(app)
    assert api.get("/aw26/slips", headers=bearer(key)).status_code == 403
    assert api.get("/pnl/dogs/bets", headers=bearer(key)).status_code == 403


def test_bad_missing_and_revoked_keys_are_refused():
    key = _client()
    api = TestClient(app)
    assert api.get("/shop").status_code == 401
    assert api.get("/shop", headers=bearer("pm_nope")).status_code == 401
    db = Session()
    apikeys.revoke(db, "bot")
    db.close()
    assert api.get("/shop", headers=bearer(key)).status_code == 401


def test_a_client_acts_under_its_own_name():
    key = _client()
    api = TestClient(app)
    assert api.post("/shop/visits", headers=bearer(key)).status_code == 200
    me = api.get("/auth/me", headers=bearer(key)).json()
    assert me["username"] == "api:bot"
    assert "shop" in me["tools"] and "aw26" in me["tools"]


def test_last_used_is_recorded():
    key = _client()
    TestClient(app).get("/shop", headers=bearer(key))
    db = Session()
    assert db.query(ApiClient).one().last_used_at is not None
    db.close()


def test_static_ingest_key_still_works_and_is_the_ingest_grant(monkeypatch):
    monkeypatch.setattr(settings, "saves_ingest_key", "static-ingest")
    api = TestClient(app)
    assert api.get("/aw26/open-leagues", headers={"X-API-Key": "static-ingest"}).status_code == 200
    assert api.get("/aw26/open-leagues", headers=bearer("static-ingest")).status_code == 200
    assert api.get("/aw26/open-leagues", headers={"X-API-Key": "wrong"}).status_code == 401
    # The static key is ONLY the ingest grant: it reaches no tool.
    assert api.get("/shop", headers={"X-API-Key": "static-ingest"}).status_code == 403


def test_a_client_can_hold_the_ingest_grant(monkeypatch):
    monkeypatch.setattr(settings, "saves_ingest_key", "")
    ingest = _client("scraper-box", tools="ingest")
    other = _client("reader", tools="shop")
    api = TestClient(app)
    assert api.get("/aw26/open-leagues", headers=bearer(ingest)).status_code == 200
    assert api.get("/aw26/open-leagues", headers=bearer(other)).status_code == 403
    # No static key configured and no client admitted: the old 503 stands.
    assert api.get("/aw26/open-leagues", headers={"X-API-Key": "x"}).status_code == 503


def test_a_client_can_hold_the_state_grant(monkeypatch):
    monkeypatch.setattr(settings, "state_api_key", "")
    key = _client("dash", tools="state")
    assert TestClient(app).get("/state", headers=bearer(key)).status_code == 200


def test_parse_tools_aliases_and_refusals():
    assert apikeys.parse_tools("paddy,aw26") == "aw26+cards"
    assert apikeys.parse_tools("365") == "accabonus+goalscorers"
    assert apikeys.parse_tools("*") == "*"
    assert apikeys.parse_tools("value+coupons") == "coupons+value"
    for bad in ("", "nonsense", "*,aw26"):
        with pytest.raises(ValueError):
            apikeys.parse_tools(bad)


def test_cli_prints_the_key_once_and_never_again(monkeypatch, capsys):
    monkeypatch.setattr(apikeys, "SessionLocal", Session)
    monkeypatch.setattr(apikeys, "engine", engine)
    assert apikeys.main(["create", "placer", "--tools", "value,aw26"]) == 0
    out = capsys.readouterr().out
    key = out.strip().splitlines()[-1]
    assert key.startswith("pm_") and out.count(key) == 1
    assert apikeys.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "placer" in listed and key not in listed
    assert apikeys.main(["create", "placer", "--tools", "aw26"]) == 2   # names are unique
    assert apikeys.main(["revoke", "placer"]) == 0
    assert TestClient(app).get("/aw26/slips", headers=bearer(key)).status_code == 401
