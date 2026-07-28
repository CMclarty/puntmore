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


def test_each_way_bet_records_its_terms():
    # £10 e/w at 5.0 on 1/4 terms = £20 outlay. Placed (not won) returns only
    # the place part: 10 × (1 + 4 × 0.25) = £20 — a push in money terms.
    client = TestClient(app)
    r = client.post("/pnl/virtuals/bets", json={
        "stake": 20, "returned": 20, "price_taken": 5.0,
        "each_way": True, "place_fraction": 0.25,
    })
    assert r.status_code == 200
    b = r.json()
    assert b["each_way"] is True and b["place_fraction"] == 0.25
    assert b["stake"] == 20.0 and b["amount"] == 0.0


def test_bets_default_to_win_only():
    client = TestClient(app)
    b = client.post("/pnl/dogs/bets", json={"stake": 10, "returned": 0}).json()
    assert b["each_way"] is False and b["place_fraction"] is None


def test_each_way_and_terms_must_agree():
    client = TestClient(app)
    bad = [
        # Each-way with no terms can't be reconstructed later...
        {"stake": 20, "returned": 0, "each_way": True},
        # ...and terms without each-way describe a bet with no place part.
        {"stake": 10, "returned": 0, "place_fraction": 0.25},
        {"stake": 20, "returned": 0, "each_way": True, "place_fraction": 0},
        {"stake": 20, "returned": 0, "each_way": True, "place_fraction": 1.5},
    ]
    for body in bad:
        assert client.post("/pnl/virtuals/bets", json=body).status_code == 422, body


def test_csv_carries_each_way_columns():
    client = TestClient(app)
    client.post("/pnl/virtuals/bets", json={
        "stake": 20, "returned": 45, "price_taken": 5.0,
        "each_way": True, "place_fraction": 0.2,
    })
    client.post("/pnl/virtuals/bets", json={"stake": 10, "returned": 0})
    rows = list(csv.reader(io.StringIO(client.get("/pnl/virtuals/bets.csv").text)))
    assert rows[0][8:10] == ["each_way", "place_fraction"]
    assert rows[1][8:10] == ["1", "0.2000"]
    assert rows[2][8:10] == ["0", ""]     # win-only: flag off, no terms


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
        "price_taken", "bfsp", "vs_sp_pct", "each_way", "place_fraction",
        "is_tip", "boosted_from",
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
    # stake, returned, price_taken, bfsp, vs_sp_pct all blank; each_way is a
    # real "0" (the bet WAS win-only) and its terms are correctly absent, and
    # so is the (non-tip) is_tip flag and the un-boosted boosted_from.
    assert rows[1][3:] == ["", "", "", "", "", "0", "", "0", ""]


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


# --- Staff tips (a cash loss that is not a bet) ---

def test_tip_is_stored_as_a_flagged_cash_loss():
    client = TestClient(app)
    r = client.post("/pnl/virtuals/bets", json={"amount": -15, "is_tip": True})
    assert r.status_code == 200
    b = r.json()
    assert b["is_tip"] is True and b["amount"] == -15
    # A tip carries no bet detail.
    assert b["stake"] is None and b["price_taken"] is None and b["boosted_from"] is None


def test_tip_amount_must_be_a_loss():
    client = TestClient(app)
    # Zero or positive isn't money out of the till.
    assert client.post("/pnl/virtuals/bets", json={"amount": 20, "is_tip": True}).status_code == 422
    assert client.post("/pnl/virtuals/bets", json={"amount": 0, "is_tip": True}).status_code == 422
    # A tip that tries to carry bet detail is rejected outright.
    assert client.post(
        "/pnl/virtuals/bets",
        json={"amount": -10, "is_tip": True, "stake": 10, "returned": 0},
    ).status_code == 422


# --- Boosted-from price (the virtuals EV analogue) ---

def test_boosted_from_is_stored_with_the_price_taken():
    client = TestClient(app)
    r = client.post("/pnl/virtuals/bets", json={
        "stake": 10, "returned": 0, "price_taken": 3.0, "boosted_from": 2.5,
    })
    assert r.status_code == 200
    b = r.json()
    assert b["price_taken"] == 3.0 and b["boosted_from"] == 2.5


def test_boosted_from_needs_the_price_taken():
    client = TestClient(app)
    # A pre-boost price on its own measures nothing.
    r = client.post("/pnl/virtuals/bets", json={
        "stake": 10, "returned": 0, "boosted_from": 2.5,
    })
    assert r.status_code == 422
