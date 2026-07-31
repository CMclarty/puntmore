"""Shop visit route tracker: log/undo/reset, the goal cap, per-user isolation."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import ShopVisit
from app.routers.shop import GOAL, WEEK_SIZE

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
# -> "*", so these run as an authenticated user who owns the "shop" tool.

def test_route_starts_empty_and_serves_the_model():
    client = TestClient(app)
    body = client.get("/shop").json()
    assert body["visits"] == []
    model = body["model"]
    # The factors multiply out to the products the page counts down from — the
    # whole reason they are served rather than hardcoded twice.
    assert model["shops_per_day"] * model["days_per_week"] == model["week_size"]
    assert model["week_size"] * model["weeks"] == model["goal"]
    assert model["goal"] * model["value_per_shop"] == 400_000


def test_logged_visits_come_back_oldest_first():
    client = TestClient(app)
    ids = [client.post("/shop/visits").json()["id"] for _ in range(3)]
    rows = client.get("/shop").json()["visits"]
    assert [r["id"] for r in rows] == ids
    assert all("created_at" in r for r in rows)


def test_undo_deletes_one_visit():
    client = TestClient(app)
    first = client.post("/shop/visits").json()["id"]
    second = client.post("/shop/visits").json()["id"]
    assert client.delete(f"/shop/visits/{second}").status_code == 204
    assert [r["id"] for r in client.get("/shop").json()["visits"]] == [first]


def test_undo_of_an_unknown_visit_is_404():
    client = TestClient(app)
    assert client.delete("/shop/visits/999").status_code == 404


def test_reset_clears_the_whole_route():
    client = TestClient(app)
    for _ in range(3):
        client.post("/shop/visits")
    assert client.delete("/shop/visits").status_code == 204
    assert client.get("/shop").json()["visits"] == []


def test_visits_beyond_the_goal_are_refused():
    # Seeded straight into the DB — posting 800 times through the API would be
    # the same assertion at 800× the cost.
    db = TestSession()
    db.add_all([ShopVisit(user="testuser") for _ in range(GOAL)])
    db.commit()
    db.close()

    client = TestClient(app)
    assert client.post("/shop/visits").status_code == 409
    assert len(client.get("/shop").json()["visits"]) == GOAL


def test_a_visit_under_the_goal_still_logs():
    db = TestSession()
    db.add_all([ShopVisit(user="testuser") for _ in range(GOAL - 1)])
    db.commit()
    db.close()

    client = TestClient(app)
    assert client.post("/shop/visits").status_code == 200
    assert client.post("/shop/visits").status_code == 409   # now full


def test_week_size_is_the_daily_rate_times_the_week():
    # Guards the constant the frontend's "week N of 40" is derived from.
    assert WEEK_SIZE == 20
    assert GOAL == 800


def test_one_users_route_is_invisible_to_another():
    from app.security import require_user
    client = TestClient(app)
    mine = client.post("/shop/visits").json()["id"]

    app.dependency_overrides[require_user] = lambda: "someone_else"
    try:
        assert client.get("/shop").json()["visits"] == []
        # Another user's id is a 404, not a delete.
        assert client.delete(f"/shop/visits/{mine}").status_code == 404
        # And their reset must not touch my rows.
        assert client.delete("/shop/visits").status_code == 204
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"

    assert [r["id"] for r in client.get("/shop").json()["visits"]] == [mine]


def test_shop_router_requires_the_shop_tool():
    from app.security import user_permissions
    client = TestClient(app)
    app.dependency_overrides[user_permissions] = lambda: "coupons"
    try:
        assert client.get("/shop").status_code == 403
        assert client.post("/shop/visits").status_code == 403
        assert client.delete("/shop/visits").status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
