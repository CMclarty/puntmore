"""Refresh-loop notification dedupe + fixture-match transparency (R7)."""
from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Coupon, Leg
from app.comparison import value_tier, is_compound_pick
from app.scheduler import refresh_all_live_coupons
from app.providers.base import OddsProvider, MatchQuery, OddsResult
from app.providers.odds_api import TheOddsApiProvider

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def set_fk_pragma(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, message: str):
        self.messages.append(message)


class FixtureOddsProvider(OddsProvider):
    """Test double with mutable fair odds, keyed by match_name."""

    def __init__(self, fair: dict[str, float], fixture_names: dict[str, str] | None = None):
        self.fair = fair
        self.fixture_names = fixture_names or {}

    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        return [
            OddsResult(
                match_id=q.match_id,
                fair_odds=self.fair.get(q.match_name),
                matched_fixture=self.fixture_names.get(q.match_name),
            )
            for q in queries
        ]


def _live_coupon_with_leg(db, match_name="Arsenal vs Chelsea", odds=2.0):
    c = Coupon(name="Test Acca", type_code="WO25", match_day=date.today(), status="live")
    db.add(c)
    db.flush()
    leg = Leg(coupon_id=c.id, match_name=match_name, pick="Win", odds=odds, rank=0)
    db.add(leg)
    db.commit()
    return c, leg


# --- value_tier ---

def test_value_tier_thresholds():
    assert value_tier(0.07) == "hi"
    assert value_tier(0.06) == "hi"
    assert value_tier(0.05) == "mid"
    assert value_tier(0.04) == "mid"
    assert value_tier(0.01) == "lo"
    assert value_tier(0.0) is None
    assert value_tier(-0.05) is None
    assert value_tier(None) is None


# --- is_compound_pick / Leg.is_approx ---

def test_compound_pick_detection():
    assert is_compound_pick("Win and Over 2.5")
    assert is_compound_pick("win & under 3.5")
    assert not is_compound_pick("Over 2.5")
    assert not is_compound_pick("Win")


def test_leg_is_approx_property():
    assert Leg(match_name="x", pick="Win and Over 2.5", odds=3.0).is_approx
    assert not Leg(match_name="x", pick="Win", odds=1.8).is_approx


# --- notification dedupe ---

def test_first_refresh_notifies_value_leg(db):
    _live_coupon_with_leg(db, odds=2.0)
    provider = FixtureOddsProvider({"Arsenal vs Chelsea": 1.80})  # value ≈ +11% (hi)
    notifier = FakeNotifier()

    refresh_all_live_coupons(provider, notifier=notifier, db=db)

    assert len(notifier.messages) == 1
    assert "Arsenal vs Chelsea" in notifier.messages[0]


def test_unchanged_tier_not_renotified(db):
    _live_coupon_with_leg(db, odds=2.0)
    provider = FixtureOddsProvider({"Arsenal vs Chelsea": 1.80})
    notifier = FakeNotifier()

    refresh_all_live_coupons(provider, notifier=notifier, db=db)
    refresh_all_live_coupons(provider, notifier=notifier, db=db)
    refresh_all_live_coupons(provider, notifier=notifier, db=db)

    assert len(notifier.messages) == 1


def test_tier_change_renotifies(db):
    _live_coupon_with_leg(db, odds=2.0)
    provider = FixtureOddsProvider({"Arsenal vs Chelsea": 1.80})  # hi
    notifier = FakeNotifier()

    refresh_all_live_coupons(provider, notifier=notifier, db=db)
    provider.fair["Arsenal vs Chelsea"] = 1.91  # value ≈ +4.7% → mid
    refresh_all_live_coupons(provider, notifier=notifier, db=db)

    assert len(notifier.messages) == 2


def test_value_dropping_then_returning_renotifies(db):
    _, leg = _live_coupon_with_leg(db, odds=2.0)
    provider = FixtureOddsProvider({"Arsenal vs Chelsea": 1.80})
    notifier = FakeNotifier()

    refresh_all_live_coupons(provider, notifier=notifier, db=db)
    provider.fair["Arsenal vs Chelsea"] = 2.10  # negative value
    refresh_all_live_coupons(provider, notifier=notifier, db=db)
    db.refresh(leg)
    assert leg.notified_tier is None
    assert len(notifier.messages) == 1

    provider.fair["Arsenal vs Chelsea"] = 1.80  # back to hi
    refresh_all_live_coupons(provider, notifier=notifier, db=db)
    assert len(notifier.messages) == 2


def test_negative_value_never_notifies(db):
    _live_coupon_with_leg(db, odds=2.0)
    provider = FixtureOddsProvider({"Arsenal vs Chelsea": 2.20})
    notifier = FakeNotifier()

    refresh_all_live_coupons(provider, notifier=notifier, db=db)

    assert notifier.messages == []


# --- matched fixture persistence ---

def test_refresh_stores_matched_fixture(db):
    _, leg = _live_coupon_with_leg(db, match_name="Man Utd vs Chelsea", odds=2.0)
    provider = FixtureOddsProvider(
        {"Man Utd vs Chelsea": 1.80},
        fixture_names={"Man Utd vs Chelsea": "Manchester United vs Chelsea"},
    )

    refresh_all_live_coupons(provider, db=db)

    db.refresh(leg)
    assert leg.matched_fixture == "Manchester United vs Chelsea"


# --- TheOddsApiProvider fuzzy-match transparency ---

class StubClient:
    def __init__(self, events):
        self._events = events
        self._served = False

    def fetch(self, sport_key, markets, bookmakers=None, ttl=None):
        if self._served:
            return []
        self._served = True
        return self._events


def _event(home, away, home_price=1.85):
    return {
        "home_team": home,
        "away_team": away,
        "bookmakers": [{
            "key": "pinnacle",
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": home, "price": home_price},
                    {"name": away, "price": 2.10},
                ],
            }],
        }],
    }


def test_provider_returns_matched_fixture():
    provider = TheOddsApiProvider(StubClient([_event("Manchester United", "Chelsea")]))
    results = provider.get_fair_odds([MatchQuery(1, "Man Utd vs Chelsea", "Win")])
    assert results[0].matched_fixture == "Manchester United vs Chelsea"
    assert results[0].fair_odds == 1.85


def test_provider_threads_cache_ttl_to_client():
    # The coupon provider's 30-min TTL must reach the client so its full
    # per-sport pull refreshes on the coupon cadence, not the 24-h default.
    class TTLRecordingClient:
        def __init__(self):
            self.ttls = []

        def fetch(self, sport_key, markets, bookmakers=None, ttl=None):
            self.ttls.append(ttl)
            return []

    client = TTLRecordingClient()
    provider = TheOddsApiProvider(client, sport_keys=["soccer_epl"], cache_ttl=1800)
    provider.get_fair_odds([MatchQuery(1, "A vs B", "Win")])
    assert client.ttls == [1800]


def test_provider_no_match_returns_none_fixture():
    provider = TheOddsApiProvider(StubClient([_event("Paris Saint Germain", "Olympique Lyonnais")]))
    results = provider.get_fair_odds([MatchQuery(1, "PSG vs Lyon", "Win")])
    assert results[0].matched_fixture is None
    assert results[0].fair_odds is None


def test_provider_logs_borderline_match(caplog):
    # "Gladbach vs Bayern" scores ~0.61 against this fixture: accepted but logged.
    provider = TheOddsApiProvider(
        StubClient([_event("Borussia Monchengladbach", "Bayern Munich")])
    )
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        results = provider.get_fair_odds([MatchQuery(1, "Gladbach vs Bayern", "Win")])
    assert results[0].matched_fixture == "Borussia Monchengladbach vs Bayern Munich"
    assert any("Borderline fixture match" in r.message for r in caplog.records)
