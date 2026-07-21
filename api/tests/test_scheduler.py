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
    c = Coupon(name="Test Acca", type_code="WO25", match_day=date.today(), status="confirmed")
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


# --- is_compound_pick / Leg.flag_list ---

def test_compound_pick_detection():
    assert is_compound_pick("Win and Over 2.5")
    assert is_compound_pick("win & under 3.5")
    assert not is_compound_pick("Over 2.5")
    assert not is_compound_pick("Win")


def test_leg_flag_list_round_trips():
    assert Leg(match_name="x", pick="Win", odds=1.8, flags=None).flag_list == []
    assert Leg(
        match_name="x", pick="Win", odds=1.8, flags="odds_range,no_match"
    ).flag_list == ["odds_range", "no_match"]


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


def _totals_event(home, away, over=1.90, under=1.95, point=2.5, with_under=True):
    outcomes = [{"name": "Over", "price": over, "point": point}]
    if with_under:
        outcomes.append({"name": "Under", "price": under, "point": point})
    return {
        "home_team": home,
        "away_team": away,
        "bookmakers": [{
            "key": "pinnacle",
            "markets": [{"key": "totals", "outcomes": outcomes}],
        }],
    }


def test_provider_returns_matched_fixture():
    provider = TheOddsApiProvider(StubClient([_event("Manchester United", "Chelsea")]))
    results = provider.get_fair_odds([MatchQuery(1, "Man Utd vs Chelsea", "Win")])
    assert results[0].matched_fixture == "Manchester United vs Chelsea"
    # De-vigged, NOT the raw 1.85 book price: 1/1.85 + 1/2.10 = 1.0167, so the
    # fair price is higher once the 1.67% overround is removed.
    assert results[0].fair_odds == 1.8782


# --- In-house de-vig (PLAN V2') ---
# Returning a vigged price as "fair odds" understates fair odds, which inflates
# computed value and fires Discord alerts on legs that hold no value.

def test_h2h_fair_odds_are_devigged_not_raw():
    provider = TheOddsApiProvider(StubClient([_event("Arsenal", "Spurs")]))
    fair = provider.get_fair_odds([MatchQuery(1, "Arsenal vs Spurs", "Win")])[0].fair_odds
    assert fair > 1.85, "fair odds must exceed the vigged book price"
    # The de-vigged book is fair: implied probabilities sum to 1 (the away
    # side de-vigs to 2.1386 from a raw 2.10).
    assert abs(1 / fair + 1 / 2.1386 - 1.0) < 1e-3


def test_totals_fair_odds_are_devigged():
    provider = TheOddsApiProvider(StubClient([_totals_event("Arsenal", "Spurs")]))
    fair = provider.get_fair_odds([MatchQuery(1, "Arsenal vs Spurs", "Over 2.5")])[0].fair_odds
    assert fair > 1.90, "fair odds must exceed the vigged book price"


def test_one_sided_total_is_unpriced_not_vigged():
    # Only an Over quote: the margin can't be determined, so the honest answer
    # is no price. Returning the raw 1.90 would be a vigged price mislabelled.
    provider = TheOddsApiProvider(
        StubClient([_totals_event("Arsenal", "Spurs", with_under=False)])
    )
    result = provider.get_fair_odds([MatchQuery(1, "Arsenal vs Spurs", "Over 2.5")])[0]
    assert result.fair_odds is None
    assert result.matched_fixture == "Arsenal vs Spurs"


def test_malformed_price_is_unpriced():
    event = _event("Arsenal", "Spurs")
    event["bookmakers"][0]["markets"][0]["outcomes"][1]["price"] = None
    provider = TheOddsApiProvider(StubClient([event]))
    assert provider.get_fair_odds(
        [MatchQuery(1, "Arsenal vs Spurs", "Win")]
    )[0].fair_odds is None


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


# --- Coupon odds source selection ---

def test_default_coupon_odds_source_is_bettingiscool():
    from app.config import Settings
    assert Settings().coupon_odds_source == "bettingiscool"


def test_bettingiscool_provider_requires_a_key():
    from app.providers.bettingiscool import (
        BettingIsCoolProvider,
        BettingIsCoolProviderNotConfigured,
    )
    with pytest.raises(BettingIsCoolProviderNotConfigured):
        BettingIsCoolProvider("")


# --- BettingIsCoolProvider pricing ---
# Row shapes below are copied from a real /api/odds response (Ecuador Serie A,
# July 2026). Index convention: 0 = draw, 1 = home/over, 2 = away/under.

def _bic_rows():
    return [
        {"period": 0, "market": "moneyline", "line": None,
         "odds0": 4.32, "odds1": 1.452, "odds2": 7.32,
         "todds0": 4.775, "todds1": 1.49, "todds2": 8.388},
        {"period": 0, "market": "totals", "line": 2.5,
         "odds0": None, "odds1": 1.84, "odds2": 2.0,
         "todds0": None, "todds1": 1.915, "todds2": 2.093},
        {"period": 0, "market": "home_totals", "line": 1.5,
         "odds0": None, "odds1": 1.558, "odds2": 2.43,
         "todds0": None, "todds1": 1.618, "todds2": 2.619},
        # First-half rows must never be picked up for a full-match leg.
        {"period": 1, "market": "totals", "line": 2.5,
         "odds0": None, "odds1": 9.99, "odds2": 9.99,
         "todds0": None, "todds1": 9.99, "todds2": 9.99},
    ]


class _BicProvider:
    """BettingIsCoolProvider with transport stubbed out."""

    def __new__(cls, fixtures, rows):
        from app.providers.bettingiscool import BettingIsCoolProvider
        p = BettingIsCoolProvider("test-key")
        p._fixtures = lambda: fixtures
        p._odds = lambda event_id: rows
        return p


def test_bic_devigs_in_house_matching_vendor_todds():
    # SPEC 7.1: todds is a CROSS-CHECK only. Our in-house power-method de-vig
    # must reproduce it -- verified against the live API to within 0.03%.
    from app.odds.utils import devigd_multi
    # Real rows only -- the period=1 row is a synthetic period-filter fixture
    # whose todds are placeholders, not a genuine de-vigged pair.
    for r in [r for r in _bic_rows() if r["period"] == 0]:
        idx = [i for i in (0, 1, 2) if r[f"odds{i}"] is not None]
        ours, _ = devigd_multi([r[f"odds{i}"] for i in idx])
        vendor = [r[f"todds{i}"] for i in idx]
        for a, b in zip(ours, vendor):
            assert abs(a - b) / b < 0.005


def test_bic_prices_home_win_from_devigged_moneyline():
    fx = [{"event_id": 1, "runner_home": "Independiente del Valle",
           "runner_away": "Emelec"}]
    p = _BicProvider(fx, _bic_rows())
    res = p.get_fair_odds([
        MatchQuery(1, "Independiente del Valle vs Emelec",
                   "Independiente del Valle Win")
    ])[0]
    assert res.matched_fixture == "Independiente del Valle vs Emelec"
    assert res.side is True
    # De-vigged home price, not the raw 1.452. The vendor reports 1.49 to 2dp;
    # we keep 4, so compare within the 0.03% agreement measured against todds.
    assert res.fair_odds == pytest.approx(1.49, rel=0.005)
    assert res.fair_odds > 1.452


def test_bic_win_without_a_named_team_is_unpriced():
    """A bare "Win" names no side, so it cannot be priced.

    This used to silently return the HOME price. Refusing is correct — but it
    means OCR must carry the selected team into the pick text, or every
    team-specific leg lands unpriced. See PLAN item 2a.
    """
    fx = [{"event_id": 1, "runner_home": "Independiente del Valle",
           "runner_away": "Emelec"}]
    p = _BicProvider(fx, _bic_rows())
    res = p.get_fair_odds([
        MatchQuery(1, "Independiente del Valle vs Emelec", "Win")
    ])[0]
    assert res.side is None
    assert res.fair_odds is None
    # Both sides are still carried, so the UI can show where value would be.
    assert res.fair_odds_home is not None
    assert res.fair_odds_away is not None


def test_bic_prices_over_from_devigged_totals():
    fx = [{"event_id": 1, "runner_home": "A", "runner_away": "B"}]
    p = _BicProvider(fx, _bic_rows())
    res = p.get_fair_odds([MatchQuery(1, "A vs B", "Over 2.5")])[0]
    assert res.fair_odds == pytest.approx(1.915, rel=0.005)  # not the raw 1.84


def test_bic_ignores_first_half_rows():
    # The period=1 totals row is priced 9.99; picking it up would be a silent
    # mispricing rather than an error.
    fx = [{"event_id": 1, "runner_home": "A", "runner_away": "B"}]
    p = _BicProvider(fx, _bic_rows())
    res = p.get_fair_odds([MatchQuery(1, "A vs B", "Over 2.5")])[0]
    assert res.fair_odds is not None and res.fair_odds < 3


def test_bic_unknown_line_is_unpriced():
    fx = [{"event_id": 1, "runner_home": "A", "runner_away": "B"}]
    p = _BicProvider(fx, _bic_rows())
    res = p.get_fair_odds([MatchQuery(1, "A vs B", "Over 4.5")])[0]
    assert res.fair_odds is None


def test_bic_no_fixture_match_is_unpriced():
    fx = [{"event_id": 1, "runner_home": "Alianza Lima",
           "runner_away": "Sport Huancayo"}]
    p = _BicProvider(fx, _bic_rows())
    res = p.get_fair_odds([MatchQuery(1, "Arsenal vs Chelsea", "Win")])[0]
    assert res.matched_fixture is None
    assert res.fair_odds is None


def test_bic_ignores_asian_lines_as_grid_constraints():
    # Only clean .5 lines feed the fit. Quarter lines are split stakes and
    # integer lines push; both are ignored (measured cost: rmse 0.0117 ->
    # 0.0119, still over-determined at 7 constraints for 3 parameters).
    from app.providers.bettingiscool import BettingIsCoolProvider as P
    assert P._is_half_line(2.5) and P._is_half_line(0.5) and P._is_half_line(3.5)
    assert not P._is_half_line(2.0)    # integer: pushes
    assert not P._is_half_line(2.25)   # quarter: split stake
    assert not P._is_half_line(2.75)
    assert not P._is_half_line(None)

    rows = [
        {"period": 0, "market": "moneyline", "line": None,
         "odds0": 4.32, "odds1": 1.452, "odds2": 7.32},
        {"period": 0, "market": "totals", "line": 2.5,
         "odds0": None, "odds1": 1.84, "odds2": 2.0},
        {"period": 0, "market": "totals", "line": 2.75,
         "odds0": None, "odds1": 1.95, "odds2": 1.90},
        {"period": 0, "market": "totals", "line": 3.0,
         "odds0": None, "odds1": 2.10, "odds2": 1.75},
    ]
    cons = P._grid_constraints(rows)
    lines = sorted(l for _, l, _ in cons if l is not None)
    assert lines == [2.5]
