"""The Python client (clients/python/puntmore) against the real app, in-process
over httpx.ASGITransport — the same routes, auth and shapes a deployment serves."""
import sys
from datetime import date, timedelta
from pathlib import Path

import anyio
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "clients" / "python"))

from puntmore import AsyncPuntmore, Puntmore, PuntmoreError, ValueSelection  # noqa: E402

from app import apikeys  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.asgi import root  # noqa: E402  (the deployed shape: API under /api)
from app.main import app  # noqa: E402
from app.models import Coupon, Leg  # noqa: E402
from app.security import require_user, user_permissions  # noqa: E402
from app.time_utils import utcnow  # noqa: E402

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


class _SyncASGI(httpx.BaseTransport):
    """httpx.ASGITransport is async-only; drive it from the sync client."""

    def __init__(self, asgi):
        self._t = httpx.ASGITransport(app=asgi)

    def handle_request(self, request):
        async def go():
            resp = await self._t.handle_async_request(httpx.Request(
                request.method, request.url, headers=request.headers, content=request.read()))
            return httpx.Response(resp.status_code, headers=resp.headers,
                                  content=await resp.aread())
        return anyio.run(go)


@pytest.fixture
def key(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides.pop(require_user, None)
    app.dependency_overrides.pop(user_permissions, None)
    app.state.cards_client = None
    app.state.arbs_client = None
    monkeypatch.setattr(settings, "state_api_key", "")
    db = Session()
    c = Coupon(type_code="TTS2", match_day=date.today(), status="confirmed")
    c.legs = [Leg(match_name="Hull v Stoke", pick="Hull to Score 2", odds=3.0, fair_odds=2.5,
                  value=0.2, kickoff=utcnow() + timedelta(hours=5), side="home", rank=0)]
    db.add(c)
    db.commit()
    _, k = apikeys.create(db, "placer", "value,cards,state")
    db.close()
    yield k
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def test_sync_client_reads_value(key):
    with Puntmore("http://test", key, transport=_SyncASGI(root)) as pm:
        assert pm.whoami()["username"] == "api:placer"
        feed = pm.value(min_edge=0.05)
        assert feed.errors == {}
        [row] = feed.selections
        assert isinstance(row, ValueSelection)
        assert (row.source, row.selection, row.odds, row.fair) == ("coupon", "Hull to Score 2", 3.0, 2.5)
        assert row.kickoff is not None and row.kickoff.tzinfo is not None
        assert pm.value(source="coupon", bookmaker="bet365").selections == []
        assert pm.paddy.feed() == []
        assert set(pm.state()) >= {"coupons", "cards", "accabonus"}



def test_async_client_over_asgi_transport(key):
    async def go():
        async with AsyncPuntmore("http://test/api", key,
                                 transport=httpx.ASGITransport(app=root)) as pm:
            feed = await pm.value()
            assert [r.source for r in feed.selections] == ["coupon"]
            assert (await pm.whoami())["tools"]
            async with AsyncPuntmore("http://test", "pm_wrong",
                                     transport=httpx.ASGITransport(app=root)) as bad:
                try:
                    await bad.value()
                except PuntmoreError as exc:
                    return exc.status
    assert anyio.run(go) == 401
