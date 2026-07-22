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
