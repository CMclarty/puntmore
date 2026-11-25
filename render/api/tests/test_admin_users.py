"""The Users page's API (`/admin/users`, routers/admin.py) and the `admin` tool.

Accounts live in the database (the register, 25 Nov 2026): a session whose
raw grant is "*" adds, regrants and removes them here. conftest bypasses auth
for every test; these pop that bypass so the real guards run. Every password
below is obviously fake.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import apikeys
from app.database import Base, get_db
from app.main import app
from app.models import User
from app.security import (
    SESSION_SECRET,
    account_mark,
    enabled_tools,
    grantable_tools,
    granted_tools,
    has_permission,
    hash_password,
    parse_permissions,
    require_user,
    user_permissions,
    verify_password,
)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

ADMIN_PW = "fake-admin-password"
USER_PW = "fake-user-password"
NEW_PW = "fake-new-password"


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
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def _add(username, password, permissions):
    db = Session()
    try:
        db.add(User(username=username, password_hash=hash_password(password),
                    permissions=permissions))
        db.commit()
    finally:
        db.close()


def _row(username):
    db = Session()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()


def _signed_in(username, password):
    c = TestClient(app)
    assert c.post("/auth/login", json={"username": username, "password": password}).status_code == 200
    return c


@pytest.fixture
def admin():
    _add("callum", ADMIN_PW, "*")
    _add("filo", USER_PW, "cards+hills")
    return _signed_in("callum", ADMIN_PW)


GRANTABLE = grantable_tools()


# --- the admin tool -----------------------------------------------------------

def test_admin_is_a_me_tool_held_only_by_star():
    from app.site import ME_TOOLS
    assert "admin" in ME_TOOLS
    assert "admin" in enabled_tools()
    assert "admin" in granted_tools("*")
    assert has_permission("*", "admin")


@pytest.mark.parametrize("grant", ["admin", "cards+admin", "admin+*", " admin ", "ADMIN"])
def test_a_grant_naming_admin_never_confers_it(grant):
    assert "admin" not in granted_tools(grant)
    assert "admin" not in parse_permissions(grant)
    assert not has_permission(grant, "admin")


def test_a_named_admin_grant_keeps_the_other_tools():
    assert granted_tools("cards+admin") == ["cards"]


def test_login_and_me_carry_admin_only_for_star(admin):
    assert "admin" in admin.get("/auth/me").json()["tools"]
    _add("sneaky", USER_PW, "cards+admin")
    c = TestClient(app)
    body = c.post("/auth/login", json={"username": "sneaky", "password": USER_PW}).json()
    assert body["tools"] == ["cards"]
    assert c.get("/auth/me").json()["tools"] == ["cards"]
    assert c.get("/admin/users").status_code == 403


def test_the_cli_refuses_admin_for_a_key():
    with pytest.raises(ValueError):
        apikeys.parse_tools("admin")


# --- access control -----------------------------------------------------------

ROUTES = [
    ("get", "/admin/users", None),
    ("post", "/admin/users", {"username": "x", "password": NEW_PW, "tools": ["cards"]}),
    ("patch", "/admin/users/filo", {"tools": ["cards"]}),
    ("delete", "/admin/users/filo", None),
]


def _call(c, method, path, body, **kw):
    if body is None:
        return getattr(c, method)(path, **kw)
    if method == "delete":
        return c.request("DELETE", path, json=body, **kw)
    return getattr(c, method)(path, json=body, **kw)


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_anonymous_is_401(method, path, body):
    _add("filo", USER_PW, "cards+hills")
    assert _call(TestClient(app), method, path, body).status_code == 401
    assert _row("filo").permissions == "cards+hills"


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_a_non_admin_session_is_403(method, path, body):
    _add("filo", USER_PW, "cards+hills")
    _add("pal", USER_PW, "cards+hills+pnl+shop+aw26+finder+props+kelly+boost+coupons")
    c = _signed_in("pal", USER_PW)
    assert _call(c, method, path, body).status_code == 403
    assert _row("filo").permissions == "cards+hills"


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_an_api_key_is_refused_even_with_star(method, path, body):
    _add("filo", USER_PW, "cards+hills")
    db = Session()
    try:
        _, key = apikeys.create(db, "bot", "*")
    finally:
        db.close()
    r = _call(TestClient(app), method, path, body, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403
    assert _row("filo").permissions == "cards+hills"


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_a_key_beside_an_admin_session_is_still_refused(admin, method, path, body):
    db = Session()
    try:
        _, key = apikeys.create(db, "bot", "*")
    finally:
        db.close()
    r = _call(admin, method, path, body, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403


def test_a_bad_key_is_401(admin):
    assert admin.get("/admin/users", headers={"Authorization": "Bearer pm_nope"}).status_code == 401


# --- GET ----------------------------------------------------------------------

def test_list_shows_every_account_and_the_grantable_tools(admin):
    body = admin.get("/admin/users").json()
    users = {u["username"]: u for u in body["users"]}
    assert users["callum"] == {"username": "callum", "permissions": "*",
                               "tools": granted_tools("*")}
    assert users["filo"] == {"username": "filo", "permissions": "cards+hills",
                             "tools": ["cards", "hills"]}
    assert body["grantable"] == GRANTABLE
    assert "admin" not in body["grantable"]


def test_list_never_carries_a_password_or_hash(admin):
    text = admin.get("/admin/users").text
    assert "password" not in text and "$2" not in text


# --- POST ---------------------------------------------------------------------

def test_add_a_user_who_can_then_sign_in(admin):
    r = admin.post("/admin/users", json={"username": "  dave  ", "password": NEW_PW,
                                         "tools": ["hills", "cards"]})
    assert r.status_code == 201
    assert r.json() == {"username": "dave", "permissions": "cards+hills",
                        "tools": ["cards", "hills"]}
    assert "$2" not in r.text and NEW_PW not in r.text
    row = _row("dave")
    assert row.permissions == "cards+hills"
    assert row.password_hash != NEW_PW and verify_password(NEW_PW, row.password_hash)
    me = _signed_in("dave", NEW_PW).get("/auth/me").json()
    assert me["tools"] == ["cards", "hills"]


def test_add_stores_tools_in_nav_order_without_repeats(admin):
    r = admin.post("/admin/users", json={"username": "dave", "password": NEW_PW,
                                         "tools": ["props", "coupons", "props"]})
    assert r.status_code == 201
    assert _row("dave").permissions == "coupons+props"


@pytest.mark.parametrize("name", ["", "   "])
def test_add_refuses_an_empty_username(admin, name):
    r = admin.post("/admin/users", json={"username": name, "password": NEW_PW, "tools": ["cards"]})
    assert r.status_code == 422


@pytest.mark.parametrize("name", ["api:bot", "a/b"])
def test_add_refuses_a_colon_or_slash_in_the_username(admin, name):
    # "api:<name>" is how a key is named where a route records who acted; a
    # slash could not be reached at /admin/users/{username}.
    r = admin.post("/admin/users", json={"username": name, "password": NEW_PW,
                                         "tools": ["cards"]})
    assert r.status_code == 422


def test_a_username_with_a_space_can_be_regranted_and_removed(admin):
    assert admin.post("/admin/users", json={"username": "EV fan", "password": NEW_PW,
                                            "tools": ["cards"]}).status_code == 201
    assert admin.patch("/admin/users/EV%20fan", json={"tools": ["hills"]}).status_code == 200
    assert _row("EV fan").permissions == "hills"
    assert admin.delete("/admin/users/EV%20fan").status_code == 204
    assert _row("EV fan") is None


@pytest.mark.parametrize("name", ["filo", "FILO", " Filo ", "Callum"])
def test_add_refuses_a_taken_username_ignoring_case(admin, name):
    r = admin.post("/admin/users", json={"username": name, "password": NEW_PW, "tools": ["cards"]})
    assert r.status_code == 409
    assert r.json()["detail"]
    assert _row("filo").permissions == "cards+hills"


@pytest.mark.parametrize("pw", ["short", "elevenchars", "x" * 11])
def test_add_refuses_a_short_password(admin, pw):
    r = admin.post("/admin/users", json={"username": "dave", "password": pw, "tools": ["cards"]})
    assert r.status_code == 422
    assert "12" in r.json()["detail"]
    assert _row("dave") is None


def test_add_refuses_a_common_password(admin, monkeypatch):
    from app import security
    monkeypatch.setattr(security, "_COMMON_PASSWORDS",
                        security._COMMON_PASSWORDS | {"fake-common-password"})
    r = admin.post("/admin/users", json={"username": "dave", "password": "Fake-Common-Password",
                                         "tools": ["cards"]})
    assert r.status_code == 422
    assert _row("dave") is None


def test_add_refuses_no_tools(admin):
    r = admin.post("/admin/users", json={"username": "dave", "password": NEW_PW, "tools": []})
    assert r.status_code == 422
    assert _row("dave") is None


@pytest.mark.parametrize("tools", [["admin"], ["cards", "admin"], ["*"], ["cards+hills"],
                                   ["nonsense"], ["value"], ["ingest"]])
def test_add_refuses_a_tool_that_is_not_grantable(admin, tools):
    r = admin.post("/admin/users", json={"username": "dave", "password": NEW_PW, "tools": tools})
    assert r.status_code == 422
    assert _row("dave") is None


def test_add_refuses_a_tool_switched_off(admin, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "enable_props", False)
    r = admin.post("/admin/users", json={"username": "dave", "password": NEW_PW, "tools": ["props"]})
    assert r.status_code == 422


# --- PATCH --------------------------------------------------------------------

def test_regrant_replaces_the_tools(admin):
    r = admin.patch("/admin/users/filo", json={"tools": ["pnl"]})
    assert r.status_code == 200
    assert r.json() == {"username": "filo", "permissions": "pnl", "tools": ["pnl"]}
    assert _row("filo").permissions == "pnl"
    assert verify_password(USER_PW, _row("filo").password_hash)   # password untouched


def test_a_regrant_takes_effect_on_a_live_session(admin):
    filo = _signed_in("filo", USER_PW)
    assert filo.get("/shop").status_code == 403
    admin.patch("/admin/users/filo", json={"tools": ["shop"]})
    assert filo.get("/auth/me").json()["tools"] == ["shop"]
    assert filo.get("/shop").status_code == 200


@pytest.mark.parametrize("tools", [[], ["admin"], ["*"], ["nonsense"]])
def test_regrant_validates_like_add(admin, tools):
    assert admin.patch("/admin/users/filo", json={"tools": tools}).status_code == 422
    assert _row("filo").permissions == "cards+hills"


def test_regrant_of_an_unknown_user_is_404(admin):
    assert admin.patch("/admin/users/ghost", json={"tools": ["cards"]}).status_code == 404


def test_a_star_account_cannot_be_regranted(admin):
    _add("other", USER_PW, "*")
    for name in ("callum", "other"):
        r = admin.patch(f"/admin/users/{name}", json={"tools": ["cards"]})
        assert r.status_code == 403
        assert _row(name).permissions == "*"


# --- DELETE -------------------------------------------------------------------

def test_remove_deletes_the_row(admin):
    assert admin.delete("/admin/users/filo").status_code == 204
    assert _row("filo") is None
    assert [u["username"] for u in admin.get("/admin/users").json()["users"]] == ["callum"]


def test_removing_a_user_ends_their_session_at_once(admin):
    _add("sam", USER_PW, "shop")
    sam = _signed_in("sam", USER_PW)
    assert sam.get("/auth/me").status_code == 200
    assert sam.get("/shop").status_code == 200
    assert admin.delete("/admin/users/sam").status_code == 204
    assert sam.get("/auth/me").status_code == 401
    assert sam.get("/shop").status_code == 401
    assert sam.post("/auth/login", json={"username": "sam", "password": USER_PW}).status_code == 401


def test_remove_of_an_unknown_user_is_404(admin):
    assert admin.delete("/admin/users/ghost").status_code == 404


def test_a_star_account_cannot_be_removed_not_even_by_itself(admin):
    _add("other", USER_PW, "*")
    for name in ("callum", "other"):
        assert admin.delete(f"/admin/users/{name}").status_code == 403
        assert _row(name) is not None
    assert admin.get("/admin/users").status_code == 200


def test_a_deleted_admin_session_is_refused(admin):
    db = Session()
    try:
        db.query(User).filter(User.username == "callum").delete()
        db.commit()
    finally:
        db.close()
    assert admin.get("/admin/users").status_code == 401


def test_the_routes_live_under_admin():
    from tests.test_removed_modules import _paths
    paths = set(_paths(app.routes))
    assert {"/admin/users", "/admin/users/{username}"} <= paths


# --- a session belongs to an account, not to a name ---------------------------

def _cookie(session: dict) -> str:
    """A session cookie signed as SessionMiddleware signs one."""
    import json
    from base64 import b64encode
    from itsdangerous import TimestampSigner
    return TimestampSigner(str(SESSION_SECRET)).sign(
        b64encode(json.dumps(session).encode())).decode()


def _with_session(session: dict) -> TestClient:
    c = TestClient(app)
    c.cookies.set("session", _cookie(session))
    return c


def _session_of(client) -> dict:
    import json
    from base64 import b64decode
    return json.loads(b64decode(client.cookies.get("session").split(".")[0]))


def test_a_recreated_username_does_not_revive_an_old_session(admin):
    _add("bob", USER_PW, "shop")
    old = _signed_in("bob", USER_PW)
    assert old.get("/shop").status_code == 200
    assert admin.delete("/admin/users/bob").status_code == 204
    assert admin.post("/admin/users", json={"username": "bob", "password": USER_PW,
                                            "tools": ["shop"]}).status_code == 201
    assert old.get("/auth/me").status_code == 401
    assert old.get("/shop").status_code == 401
    fresh = _signed_in("bob", USER_PW)
    assert fresh.get("/auth/me").status_code == 200
    assert fresh.get("/shop").status_code == 200


def test_the_cookie_carries_a_fingerprint_never_the_hash(admin):
    session = _session_of(admin)
    assert set(session) == {"user", "acct"}
    row = _row("callum")
    assert session["acct"] == account_mark(row.password_hash)
    assert row.password_hash not in admin.cookies.get("session")
    assert row.password_hash not in str(session)


def test_a_session_signed_with_the_right_fingerprint_is_signed_in():
    _add("bob", USER_PW, "shop")
    c = _with_session({"user": "bob", "acct": account_mark(_row("bob").password_hash)})
    assert c.get("/auth/me").status_code == 200
    assert c.get("/shop").status_code == 200


@pytest.mark.parametrize("bad", [None, "", "0" * 64, "other-account"])
def test_a_session_with_no_or_a_wrong_fingerprint_is_not_signed_in(bad):
    _add("bob", USER_PW, "shop")
    _add("other", USER_PW, "shop")
    session = {"user": "bob"}
    if bad == "other-account":
        session["acct"] = account_mark(_row("other").password_hash)
    elif bad is not None:
        session["acct"] = bad
    c = _with_session(session)
    assert c.get("/auth/me").status_code == 401
    assert c.get("/shop").status_code == 401
    assert c.post("/diagnostics/client", json={"message": "x"}).status_code == 401


def test_an_admin_session_without_a_fingerprint_is_refused_everywhere():
    _add("callum", ADMIN_PW, "*")
    c = _with_session({"user": "callum"})
    assert c.get("/admin/users").status_code == 401
    assert c.get("/diagnostics/errors").status_code == 403
    ok = _with_session({"user": "callum", "acct": account_mark(_row("callum").password_hash)})
    assert ok.get("/admin/users").status_code == 200
    assert ok.get("/diagnostics/errors").status_code == 200


def test_a_new_password_hash_ends_the_session(admin):
    _add("bob", USER_PW, "shop")
    bob = _signed_in("bob", USER_PW)
    db = Session()
    try:
        db.query(User).filter(User.username == "bob").update(
            {"password_hash": hash_password("fake-other-password")})
        db.commit()
    finally:
        db.close()
    assert bob.get("/auth/me").status_code == 401


# --- regranting keeps what the page does not show -----------------------------

def test_regrant_keeps_tokens_the_page_does_not_show(admin, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "enable_finder", False)
    _add("gina", USER_PW, "finder+goalscorers+cards+hub")
    r = admin.patch("/admin/users/gina", json={"tools": ["hills"]})
    assert r.status_code == 200
    assert _row("gina").permissions == "hills+finder+goalscorers+hub"
    assert r.json()["tools"] == ["hills"]


@pytest.mark.parametrize("stored", ["cards+admin", "cards+*", "cards+admin+*"])
def test_regrant_never_keeps_admin_or_star(admin, stored):
    _add("gina", USER_PW, stored)
    assert admin.patch("/admin/users/gina", json={"tools": ["hills"]}).status_code == 200
    assert _row("gina").permissions == "hills"


# --- 365 (accabonus) is grantable ---------------------------------------------

def test_365_is_grantable_without_changing_what_the_app_surfaces():
    assert "accabonus" in grantable_tools()
    assert "admin" not in grantable_tools()
    assert "accabonus" not in enabled_tools()
    assert granted_tools("cards+accabonus") == ["cards"]


def test_365_can_be_given_and_shows_on_the_list(admin):
    assert "accabonus" in admin.get("/admin/users").json()["grantable"]
    r = admin.post("/admin/users", json={"username": "sam", "password": NEW_PW,
                                         "tools": ["cards", "accabonus"]})
    assert r.status_code == 201
    assert _row("sam").permissions == "accabonus+cards"
    users = {u["username"]: u for u in admin.get("/admin/users").json()["users"]}
    assert users["sam"]["tools"] == ["accabonus", "cards"]
    assert admin.patch("/admin/users/sam", json={"tools": ["accabonus"]}).json()["tools"] == ["accabonus"]


def test_the_list_shows_an_accounts_grantable_tools_from_its_raw_grant(admin):
    _add("gina", USER_PW, "accabonus+hills+goalscorers")     # goalscorers is off
    users = {u["username"]: u for u in admin.get("/admin/users").json()["users"]}
    assert users["gina"]["tools"] == ["accabonus", "hills"]
    assert users["gina"]["permissions"] == "accabonus+hills+goalscorers"


# --- passwords ------------------------------------------------------------------

@pytest.mark.parametrize("pw", ["x" * 73, "é" * 37, "fake-" + "€" * 23])
def test_a_password_over_72_bytes_is_refused_plainly(admin, pw):
    assert len(pw.encode()) > 72
    r = admin.post("/admin/users", json={"username": "dave", "password": pw, "tools": ["cards"]})
    assert r.status_code == 422
    assert "72" in r.json()["detail"]
    assert _row("dave") is None


@pytest.mark.parametrize("pw", ["x" * 72, "é" * 36])
def test_a_password_of_72_bytes_is_taken(admin, pw):
    r = admin.post("/admin/users", json={"username": "dave", "password": pw, "tools": ["cards"]})
    assert r.status_code == 201
    assert _signed_in("dave", pw).get("/auth/me").status_code == 200


@pytest.mark.parametrize("pw", [" " * 14, "\t" * 12, " \n " * 5])
def test_a_password_of_only_whitespace_is_refused(admin, pw):
    r = admin.post("/admin/users", json={"username": "dave", "password": pw, "tools": ["cards"]})
    assert r.status_code == 422
    assert _row("dave") is None


# --- usernames ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["bob", "Bob Smith", "b.o_b-1", "9lives", "a" * 32, "EV fan"])
def test_a_plain_username_is_taken(admin, name):
    r = admin.post("/admin/users", json={"username": name, "password": NEW_PW, "tools": ["cards"]})
    assert r.status_code == 201
    assert r.json()["username"] == name


@pytest.mark.parametrize("name", [
    "a" * 33, "-bob", "_bob", ".bob", ".", "..", " ", "bo\u200bb", "\u200bbob", "bo\nb",
    "bob\n", "\tbob", "bo\tb", "Émile", "émile", "straße", "STRASSE\u0130", "bob!", "bob@x",
    "bo#b", "bob'", "api:bot", "a/b",
])
def test_any_other_username_is_refused(admin, name):
    r = admin.post("/admin/users", json={"username": name, "password": NEW_PW, "tools": ["cards"]})
    assert r.status_code == 422, name
    assert r.json()["detail"]


def test_a_non_ascii_case_pair_never_makes_two_accounts(admin):
    # SQLite's lower() folds ASCII only, so "Émile" and "émile" would both pass
    # a case-blind uniqueness check: the name rule keeps them both out.
    for name in ("Émile", "émile"):
        assert admin.post("/admin/users", json={"username": name, "password": NEW_PW,
                                                "tools": ["cards"]}).status_code == 422


@pytest.mark.parametrize("pw", ["aaaaaaaaaaaa\x00", "aaaaaaaaaaaa\ud800", "aaaaaa\naaaaaa"])
def test_a_password_bcrypt_cannot_take_is_refused_not_a_500(pw):
    from app.security import password_problem
    assert password_problem(pw) == "Password has a character that cannot be used"
