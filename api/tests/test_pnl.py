"""Private P&L tracker: add/list/delete bets, per-user isolation, tool gating."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Bet

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)


# conftest's autouse bypass sets require_user -> "testuser" and user_permissions
# -> "*", so these run as an authenticated user who owns the "pnl" tool.

def test_add_and_list_bets_in_order():
    client = TestClient(app)
    for amt in (45.0, -30.0, 60.5):
        assert client.post("/pnl/bets", json={"amount": amt}).status_code == 200
    rows = client.get("/pnl/bets").json()
    assert [r["amount"] for r in rows] == [45.0, -30.0, 60.5]   # oldest first
    assert all("created_at" in r and "id" in r for r in rows)


def test_zero_bet_rejected():
    client = TestClient(app)
    assert client.post("/pnl/bets", json={"amount": 0}).status_code == 400
    assert client.post("/pnl/bets", json={"amount": 0.004}).status_code == 400  # rounds to 0


def test_amount_is_rounded_to_pennies():
    client = TestClient(app)
    r = client.post("/pnl/bets", json={"amount": 12.349}).json()
    assert r["amount"] == 12.35


def test_stake_return_entry_derives_the_net():
    client = TestClient(app)
    r = client.post("/pnl/bets", json={"stake": 10, "returned": 34.5})
    assert r.status_code == 200
    assert r.json() == {**r.json(), "amount": 24.5, "stake": 10.0, "returned": 34.5}

    lost = client.post("/pnl/bets", json={"stake": 25, "returned": 0}).json()
    assert lost["amount"] == -25.0 and lost["stake"] == 25.0


def test_flat_entry_leaves_stake_and_return_null():
    # None means "not recorded" — never 0, or turnover/ROI would be wrong.
    client = TestClient(app)
    r = client.post("/pnl/bets", json={"amount": 40}).json()
    assert r["stake"] is None and r["returned"] is None


def test_push_is_allowed_only_in_stake_return_mode():
    client = TestClient(app)
    r = client.post("/pnl/bets", json={"stake": 20, "returned": 20})
    assert r.status_code == 200 and r.json()["amount"] == 0.0
    assert client.post("/pnl/bets", json={"amount": 0}).status_code == 400


def test_mixed_or_incomplete_entry_rejected():
    client = TestClient(app)
    bad = [
        {"amount": 5, "stake": 10, "returned": 15},   # both modes at once
        {"stake": 10},                                # return missing
        {"returned": 15},                             # stake missing
        {},                                           # neither
        {"stake": 0, "returned": 5},                  # zero stake
        {"stake": 10, "returned": -1},                # negative return
    ]
    for body in bad:
        assert client.post("/pnl/bets", json=body).status_code == 422, body


def test_stake_and_return_are_rounded_to_pennies():
    client = TestClient(app)
    r = client.post("/pnl/bets", json={"stake": 10.004, "returned": 22.229}).json()
    assert (r["stake"], r["returned"], r["amount"]) == (10.0, 22.23, 12.23)


def test_price_and_bfsp_recorded_alongside_either_mode():
    client = TestClient(app)
    flat = client.post(
        "/pnl/bets", json={"amount": 50, "price_taken": 3.5, "bfsp": 2.94}
    ).json()
    assert (flat["price_taken"], flat["bfsp"]) == (3.5, 2.94)

    detailed = client.post(
        "/pnl/bets",
        json={"stake": 20, "returned": 70, "price_taken": 3.5, "bfsp": 4.2},
    ).json()
    assert detailed["amount"] == 50.0 and detailed["bfsp"] == 4.2


def test_price_and_bfsp_are_independent_and_optional():
    # BFSP is only known after the off, so a price can be logged without it —
    # and a bet with neither is still a perfectly good bet.
    client = TestClient(app)
    only_price = client.post("/pnl/bets", json={"amount": 10, "price_taken": 6.0}).json()
    assert only_price["price_taken"] == 6.0 and only_price["bfsp"] is None

    only_sp = client.post("/pnl/bets", json={"amount": 10, "bfsp": 6.0}).json()
    assert only_sp["price_taken"] is None and only_sp["bfsp"] == 6.0

    neither = client.post("/pnl/bets", json={"amount": 10}).json()
    assert neither["price_taken"] is None and neither["bfsp"] is None


def test_odds_outside_the_decimal_range_rejected():
    client = TestClient(app)
    for body in [
        {"amount": 10, "price_taken": 1.0},     # decimal odds start above 1
        {"amount": 10, "price_taken": 0},
        {"amount": 10, "bfsp": -2},
        {"amount": 10, "bfsp": 1001},           # past the top of the ladder
    ]:
        assert client.post("/pnl/bets", json=body).status_code == 422, body


def test_odds_still_require_a_money_result():
    # The odds pair attaches to a bet; it is not a bet on its own.
    client = TestClient(app)
    r = client.post("/pnl/bets", json={"price_taken": 3.0, "bfsp": 2.5})
    assert r.status_code == 422


def test_delete_bet():
    client = TestClient(app)
    bet_id = client.post("/pnl/bets", json={"amount": 20.0}).json()["id"]
    assert client.delete(f"/pnl/bets/{bet_id}").status_code == 204
    assert client.get("/pnl/bets").json() == []
    assert client.delete(f"/pnl/bets/{bet_id}").status_code == 404   # already gone


def test_bets_are_scoped_per_user():
    from app.security import require_user
    client = TestClient(app)
    client.post("/pnl/bets", json={"amount": 100.0})   # owned by "testuser"

    # Another user must not see or delete it.
    app.dependency_overrides[require_user] = lambda: "someone_else"
    try:
        assert client.get("/pnl/bets").json() == []
        # find the other user's bet id straight from the DB and try to delete it
        db = TestSession()
        other_id = db.query(Bet).first().id
        db.close()
        assert client.delete(f"/pnl/bets/{other_id}").status_code == 404
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"

    assert len(client.get("/pnl/bets").json()) == 1   # original owner still has it


def test_pnl_router_requires_the_pnl_tool():
    from app.security import require_user, user_permissions
    client = TestClient(app)
    # Authenticated, but grant is coupons-only → no pnl → 403.
    app.dependency_overrides[user_permissions] = lambda: "coupons"
    try:
        assert client.get("/pnl/bets").status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
