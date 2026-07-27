"""Private P&L tracker: add/list/delete bets, per-user isolation, tool gating."""
import csv
import io

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
        assert client.post("/pnl/dogs/bets", json={"amount": amt}).status_code == 200
    rows = client.get("/pnl/dogs/bets").json()
    assert [r["amount"] for r in rows] == [45.0, -30.0, 60.5]   # oldest first
    assert all("created_at" in r and "id" in r for r in rows)


def test_zero_bet_rejected():
    client = TestClient(app)
    assert client.post("/pnl/dogs/bets", json={"amount": 0}).status_code == 400
    assert client.post("/pnl/dogs/bets", json={"amount": 0.004}).status_code == 400  # rounds to 0


def test_amount_is_rounded_to_pennies():
    client = TestClient(app)
    r = client.post("/pnl/dogs/bets", json={"amount": 12.349}).json()
    assert r["amount"] == 12.35


def test_stake_return_entry_derives_the_net():
    client = TestClient(app)
    r = client.post("/pnl/dogs/bets", json={"stake": 10, "returned": 34.5})
    assert r.status_code == 200
    assert r.json() == {**r.json(), "amount": 24.5, "stake": 10.0, "returned": 34.5}

    lost = client.post("/pnl/dogs/bets", json={"stake": 25, "returned": 0}).json()
    assert lost["amount"] == -25.0 and lost["stake"] == 25.0


def test_flat_entry_leaves_stake_and_return_null():
    # None means "not recorded" — never 0, or turnover/ROI would be wrong.
    client = TestClient(app)
    r = client.post("/pnl/dogs/bets", json={"amount": 40}).json()
    assert r["stake"] is None and r["returned"] is None


def test_push_is_allowed_only_in_stake_return_mode():
    client = TestClient(app)
    r = client.post("/pnl/dogs/bets", json={"stake": 20, "returned": 20})
    assert r.status_code == 200 and r.json()["amount"] == 0.0
    assert client.post("/pnl/dogs/bets", json={"amount": 0}).status_code == 400


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
        assert client.post("/pnl/dogs/bets", json=body).status_code == 422, body


def test_stake_and_return_are_rounded_to_pennies():
    client = TestClient(app)
    r = client.post("/pnl/dogs/bets", json={"stake": 10.004, "returned": 22.229}).json()
    assert (r["stake"], r["returned"], r["amount"]) == (10.0, 22.23, 12.23)


def test_price_and_bfsp_recorded_alongside_either_mode():
    client = TestClient(app)
    flat = client.post(
        "/pnl/dogs/bets", json={"amount": 50, "price_taken": 3.5, "bfsp": 2.94}
    ).json()
    assert (flat["price_taken"], flat["bfsp"]) == (3.5, 2.94)

    detailed = client.post(
        "/pnl/dogs/bets",
        json={"stake": 20, "returned": 70, "price_taken": 3.5, "bfsp": 4.2},
    ).json()
    assert detailed["amount"] == 50.0 and detailed["bfsp"] == 4.2


def test_price_and_bfsp_are_independent_and_optional():
    # BFSP is only known after the off, so a price can be logged without it —
    # and a bet with neither is still a perfectly good bet.
    client = TestClient(app)
    only_price = client.post("/pnl/dogs/bets", json={"amount": 10, "price_taken": 6.0}).json()
    assert only_price["price_taken"] == 6.0 and only_price["bfsp"] is None

    only_sp = client.post("/pnl/dogs/bets", json={"amount": 10, "bfsp": 6.0}).json()
    assert only_sp["price_taken"] is None and only_sp["bfsp"] == 6.0

    neither = client.post("/pnl/dogs/bets", json={"amount": 10}).json()
    assert neither["price_taken"] is None and neither["bfsp"] is None


def test_odds_outside_the_decimal_range_rejected():
    client = TestClient(app)
    for body in [
        {"amount": 10, "price_taken": 1.0},     # decimal odds start above 1
        {"amount": 10, "price_taken": 0},
        {"amount": 10, "bfsp": -2},
        {"amount": 10, "bfsp": 1001},           # past the top of the ladder
    ]:
        assert client.post("/pnl/dogs/bets", json=body).status_code == 422, body


def test_odds_still_require_a_money_result():
    # The odds pair attaches to a bet; it is not a bet on its own.
    client = TestClient(app)
    r = client.post("/pnl/dogs/bets", json={"price_taken": 3.0, "bfsp": 2.5})
    assert r.status_code == 422


def test_delete_bet():
    client = TestClient(app)
    bet_id = client.post("/pnl/dogs/bets", json={"amount": 20.0}).json()["id"]
    assert client.delete(f"/pnl/dogs/bets/{bet_id}").status_code == 204
    assert client.get("/pnl/dogs/bets").json() == []
    assert client.delete(f"/pnl/dogs/bets/{bet_id}").status_code == 404   # already gone


def test_ledgers_are_isolated_by_category():
    # The two ledgers share a table but never bleed into each other.
    client = TestClient(app)
    client.post("/pnl/dogs/bets", json={"amount": 10})
    client.post("/pnl/dogs/bets", json={"amount": 20})
    client.post("/pnl/virtuals/bets", json={"amount": -5})

    dogs = client.get("/pnl/dogs/bets").json()
    virtuals = client.get("/pnl/virtuals/bets").json()
    assert [b["amount"] for b in dogs] == [10.0, 20.0]
    assert [b["amount"] for b in virtuals] == [-5.0]
    assert all(b["category"] == "dogs" for b in dogs)
    assert all(b["category"] == "virtuals" for b in virtuals)

    # A delete is scoped to its ledger — the same id in the other one is untouched.
    dog_id = dogs[0]["id"]
    assert client.delete(f"/pnl/virtuals/bets/{dog_id}").status_code == 404
    assert len(client.get("/pnl/dogs/bets").json()) == 2


def test_unknown_ledger_is_a_404():
    client = TestClient(app)
    assert client.get("/pnl/horses/bets").status_code == 404
    assert client.post("/pnl/horses/bets", json={"amount": 10}).status_code == 404
    assert client.get("/pnl/horses/bets.csv").status_code == 404


def test_bets_are_scoped_per_user():
    from app.security import require_user
    client = TestClient(app)
    client.post("/pnl/dogs/bets", json={"amount": 100.0})   # owned by "testuser"

    # Another user must not see or delete it.
    app.dependency_overrides[require_user] = lambda: "someone_else"
    try:
        assert client.get("/pnl/dogs/bets").json() == []
        # find the other user's bet id straight from the DB and try to delete it
        db = TestSession()
        other_id = db.query(Bet).first().id
        db.close()
        assert client.delete(f"/pnl/dogs/bets/{other_id}").status_code == 404
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"

    assert len(client.get("/pnl/dogs/bets").json()) == 1   # original owner still has it


def test_csv_export_contains_the_full_history():
    client = TestClient(app)
    client.post("/pnl/dogs/bets", json={"amount": 45})
    client.post("/pnl/dogs/bets", json={"stake": 20, "returned": 70, "price_taken": 3.5, "bfsp": 2.8})

    r = client.get("/pnl/dogs/bets.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "puntmore-pnl-dogs-testuser-" in r.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == [
        "id", "created_at", "amount", "stake", "returned",
        "price_taken", "bfsp", "vs_sp_pct",
    ]
    assert len(rows) == 3   # header + 2 bets, oldest first
    assert rows[1][2] == "45.00"
    assert rows[2][2:7] == ["50.00", "20.00", "70.00", "3.50", "2.80"]
    assert rows[2][7] == "25.00"   # 3.5/2.8 - 1


def test_csv_leaves_unrecorded_fields_empty_not_zero():
    # In a spreadsheet a 0 stake would read as a real measurement.
    client = TestClient(app)
    client.post("/pnl/dogs/bets", json={"amount": 45})
    rows = list(csv.reader(io.StringIO(client.get("/pnl/dogs/bets.csv").text)))
    assert rows[1][3:] == ["", "", "", "", ""]


def test_csv_export_is_scoped_to_its_owner():
    from app.security import require_user
    client = TestClient(app)
    client.post("/pnl/dogs/bets", json={"amount": 100})   # testuser's

    app.dependency_overrides[require_user] = lambda: "someone_else"
    try:
        rows = list(csv.reader(io.StringIO(client.get("/pnl/dogs/bets.csv").text)))
        assert len(rows) == 1   # header only — no sight of another user's ledger
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"


def test_csv_export_needs_the_pnl_tool():
    from app.security import user_permissions
    client = TestClient(app)
    app.dependency_overrides[user_permissions] = lambda: "coupons"
    try:
        assert client.get("/pnl/dogs/bets.csv").status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


def test_pnl_router_requires_the_pnl_tool():
    from app.security import require_user, user_permissions
    client = TestClient(app)
    # Authenticated, but grant is coupons-only → no pnl → 403.
    app.dependency_overrides[user_permissions] = lambda: "coupons"
    try:
        assert client.get("/pnl/dogs/bets").status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
