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
    app.state.cards_client = None
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


# An ingest write with nothing in it: 200 when the key is admitted.
EMPTY = {"prices": []}


def _client(name="bot", tools="shop,pnl"):
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
    assert row.permissions == "pnl+shop"
    db.close()


def test_bearer_and_x_api_key_both_admit_a_granted_client():
    key = _client()
    api = TestClient(app)
    assert api.get("/shop", headers=bearer(key)).status_code == 200
    assert api.get("/shop", headers={"X-API-Key": key}).status_code == 200
    assert api.get("/pnl/dogs/bets", headers=bearer(key)).status_code == 200


def test_a_client_is_held_to_its_grant():
    key = _client(tools="shop")
    api = TestClient(app)
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
    assert "shop" in me["tools"] and "pnl" in me["tools"]


def test_last_used_is_recorded():
    key = _client()
    TestClient(app).get("/shop", headers=bearer(key))
    db = Session()
    assert db.query(ApiClient).one().last_used_at is not None
    db.close()


def test_static_ingest_key_still_works_and_is_the_ingest_grant(monkeypatch):
    monkeypatch.setattr(settings, "saves_ingest_key", "static-ingest")
    api = TestClient(app)
    assert api.post("/paddy/saves-fair", json=EMPTY, headers={"X-API-Key": "static-ingest"}).status_code == 200
    assert api.post("/paddy/saves-fair", json=EMPTY, headers=bearer("static-ingest")).status_code == 200
    assert api.post("/paddy/saves-fair", json=EMPTY, headers={"X-API-Key": "wrong"}).status_code == 401
    # The static key is ONLY the ingest grant: it reaches no tool.
    assert api.get("/shop", headers={"X-API-Key": "static-ingest"}).status_code == 403


def test_a_client_can_hold_the_ingest_grant(monkeypatch):
    monkeypatch.setattr(settings, "saves_ingest_key", "")
    ingest = _client("scraper-box", tools="ingest")
    other = _client("reader", tools="shop")
    api = TestClient(app)
    assert api.post("/paddy/saves-fair", json=EMPTY, headers=bearer(ingest)).status_code == 200
    assert api.post("/paddy/saves-fair", json=EMPTY, headers=bearer(other)).status_code == 403
    # No static key configured and no client admitted: the old 503 stands.
    assert api.post("/paddy/saves-fair", json=EMPTY, headers={"X-API-Key": "x"}).status_code == 503


def test_a_client_can_hold_the_state_grant(monkeypatch):
    monkeypatch.setattr(settings, "state_api_key", "")
    key = _client("dash", tools="state")
    assert TestClient(app).get("/state", headers=bearer(key)).status_code == 200


def test_parse_tools_aliases_and_refusals():
    assert apikeys.parse_tools("paddy,shop") == "cards+shop"
    assert apikeys.parse_tools("365") == "accabonus+goalscorers"
    assert apikeys.parse_tools("*") == "*"
    assert apikeys.parse_tools("value+coupons") == "coupons+value"
    for bad in ("", "nonsense", "*,shop"):
        with pytest.raises(ValueError):
            apikeys.parse_tools(bad)


def test_cli_prints_the_key_once_and_never_again(monkeypatch, capsys):
    monkeypatch.setattr(apikeys, "SessionLocal", Session)
    monkeypatch.setattr(apikeys, "engine", engine)
    assert apikeys.main(["create", "placer", "--tools", "value,shop"]) == 0
    out = capsys.readouterr().out
    key = out.strip().splitlines()[-1]
    assert key.startswith("pm_") and out.count(key) == 1
    assert apikeys.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "placer" in listed and key not in listed
    assert apikeys.main(["create", "placer", "--tools", "shop"]) == 2   # names are unique
    assert apikeys.main(["revoke", "placer"]) == 0
    assert TestClient(app).get("/shop", headers=bearer(key)).status_code == 401



def test_hub_is_no_longer_a_grant_the_cli_accepts():
    with pytest.raises(ValueError, match="unknown tool 'hub'"):
        apikeys.parse_tools("hub")


def test_a_key_issued_with_the_dead_hub_grant_still_loads_and_is_refused():
    from app.security import has_permission, parse_permissions
    assert parse_permissions("hub") == {"hub"}
    assert has_permission("hub", "accabonus") is False


def _legacy_hub_key(name="oldhub"):
    """A key issued before the grant was removed: `hub`, written straight to the row."""
    key = apikeys.generate_api_key()
    db = Session()
    try:
        db.add(ApiClient(name=name, key_hash=apikeys.hash_api_key(key),
                         permissions="hub", enabled=True))
        db.commit()
    finally:
        db.close()
    return key


def test_a_dead_hub_key_is_refused_on_every_route_including_the_docs_gate():
    key = _legacy_hub_key()
    api = TestClient(app)
    for path in ("/docs", "/openapi.json", "/shop", "/2up/feed", "/value"):
        assert api.get(path, headers=bearer(key)).status_code == 403, path


def test_the_docs_gate_still_admits_a_real_grant_and_the_star():
    api = TestClient(app)
    assert api.get("/openapi.json", headers=bearer(_client("a", "shop"))).status_code == 200
    assert api.get("/openapi.json", headers=bearer(_client("b", "value"))).status_code == 200
    assert api.get("/openapi.json", headers=bearer(_client("c", "*"))).status_code == 200


def test_a_grant_is_added_to_an_existing_key_without_replacing_it():
    db = Session()
    apikeys.create(db, "hub", "finder")
    assert apikeys.grant(db, "hub", "aw26,paddy").permissions == "aw26+cards+finder"   # "paddy" is the alias
    assert apikeys.grant(db, "hub", "aw26").permissions == "aw26+cards+finder"         # again: no change
    with pytest.raises(ValueError):
        apikeys.grant(db, "nobody", "aw26")
    with pytest.raises(ValueError):
        apikeys.grant(db, "hub", "not-a-tool")
    apikeys.revoke(db, "hub")
    with pytest.raises(ValueError):
        apikeys.grant(db, "hub", "aw26")
    db.close()


def test_a_star_on_either_side_of_a_grant_gives_the_star():
    db = Session()
    apikeys.create(db, "all", "*")
    apikeys.create(db, "some", "finder")
    assert apikeys.grant(db, "all", "aw26").permissions == "*"
    assert apikeys.grant(db, "some", "*").permissions == "*"
    db.close()


def test_a_granted_key_reaches_the_new_tool_with_the_same_key():
    key = _client("hub", tools="finder")
    api = TestClient(app)
    assert api.get("/aw26/photos", headers=bearer(key)).status_code == 403
    db = Session()
    key_hash = db.query(ApiClient).one().key_hash
    apikeys.grant(db, "hub", "aw26")
    assert db.query(ApiClient).one().key_hash == key_hash                  # the key is untouched
    db.close()
    assert api.get("/aw26/photos", headers=bearer(key)).status_code == 200


def test_cli_grant_never_prints_a_key(monkeypatch, capsys):
    monkeypatch.setattr(apikeys, "SessionLocal", Session)
    monkeypatch.setattr(apikeys, "engine", engine)
    assert apikeys.main(["create", "hub", "--tools", "finder"]) == 0
    key = capsys.readouterr().out.strip().splitlines()[-1]
    assert apikeys.main(["grant", "hub", "--tools", "aw26,cards"]) == 0
    out = capsys.readouterr().out
    assert out == "Client 'hub' now has grant 'aw26+cards+finder'.\n"
    assert key not in out
    assert apikeys.main(["grant", "nobody", "--tools", "aw26"]) == 2
    assert TestClient(app).get("/aw26/photos", headers=bearer(key)).status_code == 200
