"""Cards & corners: Paddy Power from TheOddsApi, Pinnacle from bettingiscool.

The old client asked the BULK endpoint for `alternate_totals` — a market that
endpoint rejects, and goal totals rather than corners anyway — then swallowed
the rejection. It returned no row in its entire life and looked like a market
with no value in it.

The split is a COST decision. TheOddsApi bills (markets x regions) per event;
Paddy Power is `uk` and Pinnacle is `eu`, so asking one vendor for both spans
two regions. Asking only for Paddy Power is unambiguously one, and bettingiscool
is Pinnacle-native on a per-DAY quota.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.cards.client import CardsApiClient, merge_pinnacle, PADDY_POWER
from app.cards.pinnacle import pinnacle_books, _strip_marker, _kind
from app.cards.value import compute_value_bets
from app.config import settings


def _iso(delta_hours):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


class _StubOddsApi:
    """TheOddsApi client with the network replaced, recording every call."""

    def __init__(self, fixtures):
        self._fixtures = fixtures
        self.event_calls = []

    def list_events(self, sport_key):
        return self._fixtures.get(sport_key, [])

    def fetch_event(self, sport_key, event_id, markets, bookmakers=None, ttl=None):
        self.event_calls.append({
            "sport_key": sport_key, "event_id": event_id, "markets": markets,
            "bookmakers": bookmakers, "ttl": ttl,
        })
        return {
            "id": event_id, "home_team": "Arsenal", "away_team": "Chelsea",
            "commence_time": _iso(1), "sport_key": sport_key,
            "bookmakers": [{
                "key": PADDY_POWER,
                "markets": [{"key": "alternate_totals_corners", "outcomes": [
                    {"name": "Over", "point": 10.5, "price": 2.10},
                    {"name": "Under", "point": 10.5, "price": 1.80},
                ]}],
            }],
        }


def _client(fixtures, **kw):
    return CardsApiClient(_StubOddsApi(fixtures), **kw)


# --- only Paddy Power, and only the uk region implied by naming it ----------

def test_only_paddy_power_is_requested():
    """Naming one book keeps the call to a single region.

    Asking for Pinnacle here as well would span uk+eu and, on the billing the
    July doc describes, double the cost of the whole tool.
    """
    stub = _StubOddsApi({"soccer_epl": [{"id": "e1", "commence_time": _iso(0.5)}]})
    c = CardsApiClient(stub, sport_keys=["soccer_epl"])
    c.get_events()
    assert stub.event_calls[0]["bookmakers"] == PADDY_POWER
    assert "pinnacle" not in stub.event_calls[0]["bookmakers"]


def test_the_real_market_keys_are_requested():
    """`alternate_totals` is GOAL totals, and the bulk endpoint rejects it.

    Requesting it was why this tool never returned a row.
    """
    stub = _StubOddsApi({"soccer_epl": [{"id": "e1", "commence_time": _iso(0.5)}]})
    CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    markets = stub.event_calls[0]["markets"]
    assert "alternate_totals_corners" in markets
    assert "alternate_totals_cards" in markets


# --- the window is the expensive dial --------------------------------------

def test_fixtures_outside_the_window_are_never_called():
    """Cost per fixture is sweeps x markets, so the window governs spend — and
    these markets are not even posted this far out."""
    stub = _StubOddsApi({"soccer_epl": [
        {"id": "soon", "commence_time": _iso(0.5)},
        {"id": "far", "commence_time": _iso(settings.cards_window_hours + 5)},
    ]})
    CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    assert [c["event_id"] for c in stub.event_calls] == ["soon"]


def test_kicked_off_fixtures_are_not_called():
    stub = _StubOddsApi({"soccer_epl": [{"id": "gone", "commence_time": _iso(-1)}]})
    CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    assert stub.event_calls == []


def test_cadence_is_tiered_by_time_to_kickoff():
    """Near kickoff gets the fast cadence; further out is served from cache.

    Driven by the odds cache's per-read TTL rather than a second scheduler, so
    a 5-minute sweep only costs credits on the fixtures that need it.
    """
    stub = _StubOddsApi({"soccer_epl": [
        {"id": "near", "commence_time": _iso(0.5)},
        {"id": "mid", "commence_time": _iso(3)},
    ]})
    CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    ttls = {c["event_id"]: c["ttl"] for c in stub.event_calls}
    assert ttls["near"] == settings.cards_fast_interval_minutes * 60
    assert ttls["mid"] == settings.cards_slow_interval_minutes * 60
    assert ttls["near"] < ttls["mid"]


# --- the budget guard ------------------------------------------------------

def test_the_daily_budget_is_a_hard_ceiling(monkeypatch):
    """The true credit cost of a per-event call is not confirmed — the doc says
    markets x regions, the accabonus comment says naming books bills as one.

    Until that is measured the sweep must stop rather than discover the rate
    when the month's quota is gone.
    """
    monkeypatch.setattr(settings, "cards_daily_call_budget", 3)
    stub = _StubOddsApi({"soccer_epl": [
        {"id": f"e{i}", "commence_time": _iso(0.5)} for i in range(10)
    ]})
    c = CardsApiClient(stub, sport_keys=["soccer_epl"])
    c.get_events()
    assert len(stub.event_calls) == 3
    assert c.calls_today == 3
    # And it stays spent across sweeps within the same day.
    c.get_events()
    assert len(stub.event_calls) == 3


def test_the_budget_keeps_the_soonest_fixtures():
    """A cut-off must drop what matters least, not whatever came back first."""
    stub = _StubOddsApi({"soccer_epl": [
        {"id": "later", "commence_time": _iso(3)},
        {"id": "sooner", "commence_time": _iso(0.5)},
    ]})
    c = CardsApiClient(stub, sport_keys=["soccer_epl"])
    import app.cards.client as mod
    mod.settings.cards_daily_call_budget = 1
    try:
        c.get_events()
    finally:
        mod.settings.cards_daily_call_budget = 700
    assert [call["event_id"] for call in stub.event_calls] == ["sooner"]


# --- the bettingiscool fair side -------------------------------------------

class _StubBic:
    """A bettingiscool provider exposing only what the cards tool needs."""

    def __init__(self, board, odds):
        self._board = board
        self._odds = odds

    def board(self):
        return self._board

    def odds_for(self, event_id):
        return self._odds.get(event_id, [])


def _corner_board():
    return [
        {"event_id": 900, "league_name": "England - Premier League (Corners)",
         "runner_home": "Arsenal (Corners)", "runner_away": "Chelsea (Corners)"},
        # A normal fixture must never be mistaken for a derived one.
        {"event_id": 901, "league_name": "England - Premier League",
         "runner_home": "Arsenal", "runner_away": "Chelsea"},
    ]


def _corner_odds():
    return {900: [
        {"period": 0, "market": "totals", "line": 10.5,
         "odds1": 1.90, "odds2": 1.95},
        {"period": 1, "market": "totals", "line": 4.5,      # first half — ignored
         "odds1": 2.00, "odds2": 1.85},
    ]}


def test_derived_fixtures_are_identified_and_normal_ones_are_not():
    board = _corner_board()
    assert _kind(board[0]) == "alternate_totals_corners"
    assert _kind(board[1]) is None
    assert _strip_marker("Arsenal (Corners)") == "Arsenal"


def test_pinnacle_prices_come_back_raw_for_value_to_devig():
    """De-vigging lives in value.py and must stay there.

    Handing it fair prices would have it de-vig them a second time.
    """
    bic = _StubBic(_corner_board(), _corner_odds())
    books = pinnacle_books(bic, [{"id": "e1", "home_team": "Arsenal",
                                  "away_team": "Chelsea"}])
    outcomes = books["e1"]["markets"][0]["outcomes"]
    assert books["e1"]["key"] == "pinnacle"
    # RAW, exactly as the vendor sent them.
    assert {o["price"] for o in outcomes} == {1.90, 1.95}


def test_only_full_match_totals_are_used():
    """A first-half line priced as a full-match one is a wrong price."""
    bic = _StubBic(_corner_board(), _corner_odds())
    books = pinnacle_books(bic, [{"id": "e1", "home_team": "Arsenal",
                                  "away_team": "Chelsea"}])
    points = {o["point"] for o in books["e1"]["markets"][0]["outcomes"]}
    assert points == {10.5}


def test_an_unrelated_fixture_gets_no_fair_price():
    """Never invent a match — a wrong fair price is worse than none."""
    bic = _StubBic(_corner_board(), _corner_odds())
    books = pinnacle_books(bic, [{"id": "e1", "home_team": "Real Madrid",
                                  "away_team": "Barcelona"}])
    assert books == {}


# --- the two vendors join, and value.py stays vendor-agnostic --------------

def test_the_two_sources_join_into_one_valued_row():
    """End to end: Paddy from TheOddsApi, Pinnacle from bettingiscool.

    Pinnacle 1.90/1.95 de-vigs to roughly 2.03/1.98; Paddy's 2.10 over is value.
    """
    stub = _StubOddsApi({"soccer_epl": [{"id": "e1", "commence_time": _iso(0.5)}]})
    events = CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    events = merge_pinnacle(events, _StubBic(_corner_board(), _corner_odds()))

    books = {bm["key"] for bm in events[0]["bookmakers"]}
    assert books == {PADDY_POWER, "pinnacle"}

    rows = compute_value_bets(events, min_edge=-0.50)
    assert rows, "the joined event must produce a valued row"
    over = next(r for r in rows if r.side == "Over")
    assert over.paddy_odds == 2.10
    assert over.pinnacle_odds == 1.90
    assert over.fair_odds > 1.90        # de-vigged, so longer than raw
    assert over.edge == pytest.approx(2.10 / over.fair_odds - 1, abs=1e-3)


def test_a_provider_without_a_board_is_reported_not_crashed():
    """The coupon provider is only bettingiscool when a key is configured.

    On the fallback or the mock there is no corners board, and that must be
    said rather than raised — an empty feed otherwise reads as "no value".
    """
    class _NoBoard:
        pass

    events = [{"id": "e1", "bookmakers": []}]
    assert merge_pinnacle(events, _NoBoard()) == events


def test_no_fair_price_means_no_row_rather_than_an_unvalued_one():
    """value.py already needs both books; this pins that the join keeps it so."""
    stub = _StubOddsApi({"soccer_epl": [{"id": "e1", "commence_time": _iso(0.5)}]})
    events = CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    events = merge_pinnacle(events, _StubBic([], {}))     # empty board
    assert compute_value_bets(events, min_edge=-0.50) == []


# --- the 5-minute sweep, and not re-alerting the same bet every tick --------

class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


def _valued_client():
    """A client whose sweep yields one Paddy Power event with real value."""
    return CardsApiClient(
        _StubOddsApi({"soccer_epl": [{"id": "e1", "commence_time": _iso(0.5)}]}),
        sport_keys=["soccer_epl"],
    )


def _reset_dedupe():
    from app import scheduler as sched
    sched._cards_notified.clear()


def test_sweep_alerts_new_value_once_not_every_tick():
    """The whole reason for tier dedupe.

    A bet sits in its fixture's window for hours and the sweep runs every five
    minutes. Without this it would fire ~48 Discord messages for one bet.
    """
    from app.scheduler import sweep_cards
    _reset_dedupe()
    bic = _StubBic(_corner_board(), _corner_odds())
    notifier = _Notifier()

    first = sweep_cards(_valued_client(), bic, notifier)
    assert first > 0
    assert len(notifier.sent) == 1

    # Same bet, same tier, next tick — silence.
    sweep_cards(_valued_client(), bic, notifier)
    assert len(notifier.sent) == 1


def test_sweep_forgets_bets_that_drop_off():
    """The dedupe map must not grow with every bet the process has ever seen.

    Same shape of leak the odds caches had before ttl_cache.py — a dict keyed
    by something unbounded, fed by a loop that never stops.
    """
    from app import scheduler as sched
    _reset_dedupe()
    bic = _StubBic(_corner_board(), _corner_odds())
    sched.sweep_cards(_valued_client(), bic, _Notifier())
    assert sched._cards_notified

    # Next sweep finds nothing (fixtures kicked off, markets down).
    empty = CardsApiClient(_StubOddsApi({"soccer_epl": []}), sport_keys=["soccer_epl"])
    sched.sweep_cards(empty, bic, _Notifier())
    assert sched._cards_notified == {}


def test_sweep_survives_a_vendor_failure():
    """A raising sweep must not kill the scheduler job."""
    from app.scheduler import sweep_cards

    class _Boom:
        def get_events(self):
            raise RuntimeError("vendor down")

    _reset_dedupe()
    assert sweep_cards(_Boom(), None, _Notifier()) == 0


def test_no_cards_client_means_no_sweep():
    from app.scheduler import sweep_cards
    assert sweep_cards(None, None, _Notifier()) == 0


def test_the_job_is_registered_only_when_the_client_exists(monkeypatch):
    """No ODDS_API_KEY should mean no job, rather than a job that wakes every
    five minutes to do nothing."""
    from app.scheduler import start_scheduler
    monkeypatch.setattr(settings, "enable_cards", True)

    without = start_scheduler(odds_provider=None, cards_client=None)
    try:
        assert without.get_job("cards_sweep") is None
    finally:
        without.shutdown(wait=False)

    with_client = start_scheduler(odds_provider=None, cards_client=_valued_client())
    try:
        job = with_client.get_job("cards_sweep")
        assert job is not None
        assert job.trigger.interval.total_seconds() == (
            settings.cards_fast_interval_minutes * 60
        )
    finally:
        with_client.shutdown(wait=False)
