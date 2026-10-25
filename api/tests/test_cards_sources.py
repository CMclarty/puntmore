"""Cards & corners: the Paddy Power client, the sweep, the ladder pricing and
the diagnostics.

The old client asked the BULK endpoint for `alternate_totals` — a market that
endpoint rejects, and goal totals rather than corners anyway — then swallowed
the rejection. It returned no row in its entire life and looked like a market
with no value in it. Only Paddy Power is requested here (one region); the fair
side comes from OddsPapi on the same fixture id (test_oddspapi.py).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.cards.client import CardsApiClient, PADDY_POWER
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


# --- the sweep, and not re-alerting the same bet every tick -----------------

class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


def _valued_client():
    """A client whose sweep yields one Paddy Power event with real value.

    The fair side rides on the SAME event, as OddsPapi delivers it: Pinnacle
    1.90/1.95 at 10.5 de-vigs to ~2.03/1.98, so Paddy's 2.10 over is value.
    """
    client = CardsApiClient(
        _StubOddsApi({"soccer_epl": [{"id": "e1", "commence_time": _iso(0.5)}]}),
        sport_keys=["soccer_epl"],
    )
    paddy_only = client.get_events

    def with_pinnacle():
        events = paddy_only()
        for e in events:
            books = e.setdefault("bookmakers", [])
            if not any(b.get("key") == "pinnacle" for b in books):
                books.append({"key": "pinnacle", "markets": [{
                    "key": "alternate_totals_corners", "outcomes": [
                        {"name": "Over", "point": 10.5, "price": 1.90},
                        {"name": "Under", "point": 10.5, "price": 1.95},
                    ]}]})
        return events
    client.get_events = with_pinnacle
    return client


def _reset_dedupe():
    from app import scheduler as sched
    sched._cards_notified.clear()


def test_sweep_alerts_new_value_once_not_every_tick(monkeypatch):
    """The whole reason for tier dedupe.

    A bet sits in its fixture's window for hours and the sweep runs every two
    minutes. Without this it would fire ~120 Discord messages for one bet.

    Alerts default OFF while the feed is unproven, so this turns them on to
    test the dedupe rather than the switch.
    """
    from app.scheduler import sweep_cards
    monkeypatch.setattr(settings, "cards_discord_alerts", True)
    monkeypatch.setattr(settings, "cards_use_ladder_model", False)
    _reset_dedupe()
    notifier = _Notifier()

    first = sweep_cards(_valued_client(), notifier)
    assert first > 0
    assert len(notifier.sent) == 1

    # Same bet, same tier, next tick — silence.
    sweep_cards(_valued_client(), notifier)
    assert len(notifier.sent) == 1


def test_sweep_forgets_bets_that_drop_off(monkeypatch):
    """The dedupe map must not grow with every bet the process has ever seen.

    Same shape of leak the odds caches had before ttl_cache.py — a dict keyed
    by something unbounded, fed by a loop that never stops.
    """
    from app import scheduler as sched
    monkeypatch.setattr(settings, "cards_use_ladder_model", False)
    _reset_dedupe()
    sched.sweep_cards(_valued_client(), _Notifier())
    assert sched._cards_notified

    # Next sweep finds nothing (fixtures kicked off, markets down).
    empty = CardsApiClient(_StubOddsApi({"soccer_epl": []}), sport_keys=["soccer_epl"])
    sched.sweep_cards(empty, _Notifier())
    assert sched._cards_notified == {}


def test_sweep_survives_a_vendor_failure():
    """A raising sweep must not kill the scheduler job."""
    from app.scheduler import sweep_cards

    class _Boom:
        def get_events(self):
            raise RuntimeError("vendor down")

    _reset_dedupe()
    assert sweep_cards(_Boom(), _Notifier()) == 0


def test_no_cards_client_means_no_sweep():
    from app.scheduler import sweep_cards
    assert sweep_cards(None, _Notifier()) == 0


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



def test_cards_alerts_are_off_by_default():
    """A value tool that alerts before anyone has checked its prices trains
    trust it has not earned. The sweep still runs and the rows still appear."""
    from app.scheduler import sweep_cards
    _reset_dedupe()
    notifier = _Notifier()
    sweep_cards(_valued_client(), notifier)
    assert notifier.sent == []


# --- laddered pricing: every line PADDY quotes, off Pinnacle's whole ladder --

def _pin_ladder_book(lines):
    """A Pinnacle corners ladder as OddsPapi attaches it to the event."""
    outcomes = []
    for line, (over, under) in sorted(lines.items()):
        outcomes.append({"name": "Over", "point": line, "price": over})
        outcomes.append({"name": "Under", "point": line, "price": under})
    return {"key": "pinnacle",
            "markets": [{"key": "alternate_totals_corners", "outcomes": outcomes}]}


def _paddy_book(lines):
    outcomes = []
    for line, (over, under) in sorted(lines.items()):
        outcomes.append({"name": "Over", "point": line, "price": over})
        outcomes.append({"name": "Under", "point": line, "price": under})
    return {"key": PADDY_POWER,
            "markets": [{"key": "alternate_totals_corners", "outcomes": outcomes}]}


def _joined(pin_lines, paddy_lines):
    return [{
        "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": _iso(0.5), "sport_key": "soccer_epl",
        "bookmakers": [_pin_ladder_book(pin_lines), _paddy_book(paddy_lines)],
    }]


# A realistic Pinnacle corners ladder around 10.
PIN = {
    8.5: (1.44, 2.75), 9.5: (1.74, 2.10),
    10.5: (2.20, 1.68), 11.5: (2.95, 1.42),
}


def test_a_line_pinnacle_does_not_quote_is_still_priced():
    """The whole reason this path exists.

    The matched-line path sees nothing here: Paddy quotes 7.5 and 12.5, neither
    of which Pinnacle prices. Most of Paddy's board looks like this.
    """
    from app.cards.laddered import compute_laddered_value
    from app.cards.value import compute_value_bets

    # Fair at 7.5 is short and at 12.5 ~4.22; both beat the band comfortably.
    events = _joined(PIN, {7.5: (1.33, 3.40), 12.5: (4.80, 1.24)})
    assert compute_value_bets(events, -0.5) == [], "matched-line path sees nothing"

    rows = compute_laddered_value(events, -0.5)
    assert rows, "laddered path must price lines Pinnacle does not quote"
    assert {r.line for r in rows} <= {7.5, 12.5}


def test_only_lines_paddy_offers_are_priced():
    """A fair price for a line nobody offers is a number you cannot bet."""
    from app.cards.laddered import compute_laddered_value
    rows = compute_laddered_value(_joined(PIN, {10.5: (2.60, 1.60)}), -0.5)
    assert {r.line for r in rows} == {10.5}


def test_lines_outside_the_hard_bounds_are_never_priced():
    """Paddy may quote a 2.5 or an 18.5 corner line; the model will not."""
    from app.cards.laddered import compute_laddered_value
    rows = compute_laddered_value(
        _joined(PIN, {2.5: (1.02, 15.0), 18.5: (26.0, 1.01), 10.5: (2.60, 1.60)}),
        -0.5,
    )
    assert {r.line for r in rows} == {10.5}


def test_push_lines_are_never_priced():
    """Whole-number (push) lines are never bet, so the model never quotes them.

    Paddy's shops quote a 9.0 corner line (stake back on exactly 9) beside the
    .5 lines. We only ever back the .5 line, so the integer line must not reach
    the feed even though it is in bounds and Paddy offers it.
    """
    from app.cards.laddered import compute_laddered_value
    rows = compute_laddered_value(
        _joined(PIN, {9.0: (1.85, 2.30), 10.5: (2.60, 1.60)}),
        -0.5,
    )
    assert {r.line for r in rows} == {10.5}


# --- a one-rung Pinnacle ladder (team markets): matched line only -----------

def _joined_team(pin_lines, paddy_lines):
    from app.cards.oddspapi import TEAM_CORNERS_HOME

    def book(key, lines):
        outcomes = []
        for line, (over, under) in sorted(lines.items()):
            outcomes.append({"name": "Over", "point": line, "price": over})
            outcomes.append({"name": "Under", "point": line, "price": under})
        return {"key": key, "markets": [{"key": TEAM_CORNERS_HOME, "outcomes": outcomes}]}

    return [{
        "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": _iso(0.5), "sport_key": "soccer_epl",
        "bookmakers": [book("pinnacle", pin_lines), book(PADDY_POWER, paddy_lines)],
    }]


def test_a_single_pinnacle_rung_prices_only_the_matching_paddy_line():
    """Pinnacle posts team corners as ONE line. That is too thin to fit a shape,
    but it is still Pinnacle's price at that line: where Paddy quotes the same
    line it is de-vigged directly, and Paddy's other lines stay unpriced.
    """
    from app.cards.laddered import compute_laddered_value, LAST_RUN
    rows = compute_laddered_value(
        _joined_team({4.5: (1.90, 1.95)}, {4.5: (2.10, 1.80), 5.5: (2.60, 1.50)}),
        -0.5,
    )
    assert {r.line for r in rows} == {4.5}, "only the line both books quote"
    over = next(r for r in rows if r.side == "Over")
    assert over.pinnacle_odds == 1.90
    assert over.fair_odds > 1.90                       # de-vigged, so longer than raw
    assert over.edge == pytest.approx(2.10 / over.fair_odds - 1, abs=1e-3)
    tally = LAST_RUN["by_market"]["team_corners"]
    assert tally["thin"] == 1 and tally["matched"] == 2 and tally["fitted"] == 0


def test_a_single_rung_prices_nothing_off_paddy_s_other_lines():
    """No shared line, no row — one rung must never become a fitted curve."""
    from app.cards.laddered import compute_laddered_value
    rows = compute_laddered_value(_joined_team({4.5: (1.90, 1.95)}, {5.5: (2.60, 1.50)}), -0.5)
    assert rows == []


def _joined_team_with_match(pin_match, pin_team, paddy_team):
    """Pinnacle's match-corners ladder AND one team rung; Paddy quotes only
    the team lines (it need not quote the match market for the shape to count)."""
    from app.cards.oddspapi import TEAM_CORNERS_HOME

    def market(key, lines):
        outcomes = []
        for line, (over, under) in sorted(lines.items()):
            outcomes.append({"name": "Over", "point": line, "price": over})
            outcomes.append({"name": "Under", "point": line, "price": under})
        return {"key": key, "outcomes": outcomes}

    return [{
        "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": _iso(0.5), "sport_key": "soccer_epl",
        "bookmakers": [
            {"key": "pinnacle", "markets": [market("alternate_totals_corners", pin_match),
                                             market(TEAM_CORNERS_HOME, pin_team)]},
            {"key": PADDY_POWER, "markets": [market(TEAM_CORNERS_HOME, paddy_team)]},
        ],
    }]


def test_a_one_rung_team_ladder_borrows_the_match_ladder_s_shape():
    """The hybrid: the team rung pins the mean, the fixture's fitted match
    ladder lends its dispersion, so Paddy's OTHER team lines get a fair price
    — every row saying so (basis "hybrid"), with the wide sparse band.
    """
    from app.cards.laddered import compute_laddered_value, LAST_RUN
    rows = compute_laddered_value(
        _joined_team_with_match(PIN, {4.5: (1.90, 1.95)},
                                {4.5: (2.10, 1.80), 5.5: (2.60, 1.50), 6.5: (3.80, 1.25)}),
        -0.5,
    )
    assert {r.line for r in rows} >= {4.5, 5.5}, "lines beyond the one rung are priced"
    assert all(r.basis == "hybrid" for r in rows)
    t = LAST_RUN["by_market"]["team_corners"]
    assert t["thin"] == 1 and t["hybrid"] == 1 and t["matched"] == 0 and t["fitted"] == 0
    assert t["split_sources"] == {"home_line": 1, "away_line": 0, "handicap": 0}
    # Beyond the single quoted rung the usual honesty applies: the 6.5 line sits
    # past the grace and must carry the extrapolation marker if it is priced.
    far = [r for r in rows if r.line == 6.5]
    assert all("+" in r.market_label for r in far)


# --- team to have MOST corners / cards: a fair three-way off the two teams ---

def _mk(key, lines):
    outcomes = []
    for line, (over, under) in sorted(lines.items()):
        outcomes.append({"name": "Over", "point": line, "price": over})
        outcomes.append({"name": "Under", "point": line, "price": under})
    return {"key": key, "outcomes": outcomes}


def _joined_split(pin_match, *, home=None, away=None, handicap=None, paddy_home=None):
    """Pinnacle's match ladder plus any of: a home team line, an away team line,
    a corners handicap (as {h: (home_price, away_price)}); Paddy quotes the
    home team's lines."""
    from app.cards.oddspapi import CORNERS_HANDICAP, TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME
    pin = [_mk("alternate_totals_corners", pin_match)]
    if home:
        pin.append(_mk(TEAM_CORNERS_HOME, home))
    if away:
        pin.append(_mk(TEAM_CORNERS_AWAY, away))
    if handicap:
        pin.append({"key": CORNERS_HANDICAP, "outcomes": [
            o for h, (ph, pa) in handicap.items()
            for o in ({"name": "Home", "point": h, "price": ph},
                      {"name": "Away", "point": h, "price": pa})]})
    return [{
        "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": _iso(0.5), "sport_key": "soccer_epl",
        "bookmakers": [{"key": "pinnacle", "markets": pin},
                       {"key": PADDY_POWER, "markets": [_mk(TEAM_CORNERS_HOME, paddy_home or {})]}],
    }]


def test_the_share_reads_the_handicap_as_a_third_source():
    from app.cards.laddered import team_split
    from app.cards.value import _get_bookmaker
    events = _joined_split(PIN, home={5.5: (1.90, 1.95)}, away={4.5: (1.95, 1.90)},
                           handicap={-0.5: (1.80, 2.05), 0.5: (1.50, 2.60)})
    split = team_split(events[0], _get_bookmaker(events[0], "pinnacle"), "team_corners_home", "soccer_epl")
    assert split is not None
    _, point, totals, share, quoted = split
    # Every quote is an observation: one home rung, one away rung, BOTH handicaps.
    assert share.kinds == {"home_line", "away_line", "handicap"}
    assert set(share.sources) == {"home_line@5.5", "away_line@4.5", "handicap@-0.5", "handicap@0.5"}
    assert 0.5 < share.p < 0.7 and not share.disagree
    assert len(totals) == 3                      # the match law and its band
    assert quoted == {"home": (5.5, 5.5), "away": (4.5, 4.5)}


def test_a_contradicted_share_prices_nothing_and_is_counted():
    """The home line says the home side has the larger share; the away line
    says the away side does. Pinnacle's own quotes disagree, so no team line
    is priced off them, no three-way is published, and the tally says why."""
    from app.cards.laddered import compute_laddered_value, LAST_RUN
    from app.cards.most import most_fairs
    events = _joined_split(PIN, home={6.5: (1.90, 1.95)}, away={6.5: (1.90, 1.95)},
                           paddy_home={5.5: (2.60, 1.50), 6.5: (3.40, 1.30)})
    assert compute_laddered_value(events, -0.5) == []
    t = LAST_RUN["by_market"]["team_corners"]
    assert t["thin"] == 1 and t["split_disagreement"] == 1 and t["hybrid"] == 0
    assert most_fairs(events) == []


def test_most_corners_fair_comes_from_the_two_team_lines():
    """Both teams at one Pinnacle rung each, the match ladder for the shape:
    the side pinned at the higher line is the favourite, basis says hybrid."""
    from app.cards.most import most_fairs
    from app.cards.oddspapi import TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME
    events = [{
        "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": _iso(0.5), "sport_key": "soccer_epl",
        "bookmakers": [
            {"key": "pinnacle", "markets": [
                _mk("alternate_totals_corners", PIN),
                _mk(TEAM_CORNERS_HOME, {5.5: (1.90, 1.95)}),
                _mk(TEAM_CORNERS_AWAY, {4.5: (1.90, 1.95)}),
            ]},
            {"key": PADDY_POWER, "markets": []},
        ],
    }]
    fairs = most_fairs(events)
    assert [f.market_key for f in fairs] == ["most_corners"]
    f = fairs[0]
    assert f.basis == "hybrid"
    assert f.p_home + f.p_tie + f.p_away == pytest.approx(1.0, abs=1e-9)
    assert f.p_home > f.p_away
    assert f.fair("home") < f.fair("away")
    assert f.fair("tie") > f.fair("home")


def test_most_needs_a_share_from_somewhere():
    """One team line (or the handicap) is enough to pin the share; a fixture
    with only the match ladder has no share and publishes nothing."""
    from app.cards.most import most_fairs
    from app.cards.oddspapi import TEAM_CORNERS_HOME

    def event(markets):
        return [{
            "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
            "commence_time": _iso(0.5), "sport_key": "soccer_epl",
            "bookmakers": [{"key": "pinnacle", "markets": markets}],
        }]
    assert most_fairs(event([_mk("alternate_totals_corners", PIN)])) == []
    one = most_fairs(event([_mk("alternate_totals_corners", PIN),
                            _mk(TEAM_CORNERS_HOME, {5.5: (1.90, 1.95)})]))
    assert [f.market_key for f in one] == ["most_corners"]


def test_an_extrapolated_row_says_so_in_its_label():
    """Beyond Pinnacle's quoted range is the same number of a different kind."""
    from app.cards.laddered import compute_laddered_value
    rows = compute_laddered_value(_joined(PIN, {13.5: (9.00, 1.11)}), -0.5)
    assert rows, "a generous extrapolated quote should still produce a row"
    for row in rows:
        assert "+" in row.market_label, "distance beyond the ladder must be visible"


def test_a_quote_that_only_beats_the_midpoint_is_rejected():
    """require_band=True is the default and is what keeps noise off the screen."""
    from app.cards.laddered import compute_laddered_value
    from app.pricing.counts.fit import fit_ladder
    from app.pricing.counts.price import price_line
    from app.cards.laddered import ladder_from
    from app.pricing.counts.config import CORNERS

    events = _joined(PIN, {10.5: (2.20, 1.68)})
    ladder = ladder_from(
        events[0]["bookmakers"][0]["markets"][0]["outcomes"], "e1",
        "alternate_totals_corners", "soccer_epl",
    )
    fair = price_line(fit_ladder(ladder, CORNERS), 10.5, ladder)
    # A quote a hair above fair but inside the band is NOT value: no row at a
    # fair-or-better floor, and below it (the acca tabs ask for legs under
    # fair) its edge is still measured against the band's far end, so it can
    # never read as positive.
    marginal = fair.fair_over + 0.005
    events = _joined(PIN, {10.5: (marginal, 1.68)})
    assert not any(r.side == "Over" and r.line == 10.5
                   for r in compute_laddered_value(events, 0.0))
    below = [r for r in compute_laddered_value(events, -0.5)
             if r.side == "Over" and r.line == 10.5]
    assert below and all(r.edge <= 0 for r in below)


def test_a_one_rung_match_ladder_is_never_fitted():
    """One rung pins nothing — the shape would come entirely from the prior, so
    no curve is fitted. The rung is still Pinnacle's own price at that line,
    so the ONE line Paddy shares is priced off it directly (basis "matched")
    and nothing else is."""
    from app.cards.laddered import compute_laddered_value, LAST_RUN
    events = _joined({9.5: (1.74, 2.10)}, {9.5: (2.60, 2.00), 10.5: (3.10, 1.40)})
    rows = compute_laddered_value(events, -0.5)
    assert {r.line for r in rows} == {9.5}
    assert all(r.basis == "matched" for r in rows)
    t = LAST_RUN["by_market"]["corners"]
    assert t["thin"] == 1 and t["fitted"] == 0 and t["hybrid"] == 0


# --- the empty screen must say WHICH kind of empty it is -------------------

def test_diagnostics_names_a_missing_client():
    from app.routers.cards import diagnose
    assert "ODDSPAPI_API_KEY" in diagnose(None)["verdict"]


def test_diagnostics_distinguishes_no_fixtures_from_no_value():
    """The two most common empty states, and both are NORMAL.

    Reporting them the same way is what makes a working tool look broken.
    """
    from app.routers.cards import diagnose

    quiet = CardsApiClient(_StubOddsApi({"soccer_epl": []}), sport_keys=["soccer_epl"])
    quiet.get_events()
    verdict = diagnose(quiet)["verdict"]
    assert "within" in verdict and "kickoff" in verdict

    # Fixtures fetched and priced, but nothing clears the guards.
    from app.cards import laddered
    laddered.LAST_RUN.update({
        "ladders_fitted": 3, "ladders_refused": 0,
        "priceable_paddy_lines": 12, "rows": 0,
    })
    busy = _valued_client()
    busy.get_events()
    verdict = diagnose(busy)["verdict"]
    assert verdict.startswith("WORKING"), verdict
    laddered.LAST_RUN.clear()


def test_diagnostics_reports_paddy_not_posting_the_market():
    from app.routers.cards import diagnose

    class _NoPaddy(_StubOddsApi):
        def fetch_event(self, *a, **k):
            event = super().fetch_event(*a, **k)
            event["bookmakers"] = []          # fixture exists, market not up
            return event

    client = CardsApiClient(
        _NoPaddy({"soccer_epl": [{"id": "e1", "commence_time": _iso(0.5)}]}),
        sport_keys=["soccer_epl"],
    )
    client.get_events()
    assert "not posting" in diagnose(client)["verdict"]


# --- three tiers: the window widened without the cadence following it ------

def test_the_far_tier_polls_coarsely():
    """72h of window at the 30-minute cadence would be ~308 credits a fixture,
    six times a 20k plan. Three days out the only question is whether the
    market is up yet, which does not need asking every half hour."""
    stub = _StubOddsApi({"soccer_epl": [
        {"id": "far", "commence_time": _iso(30)},     # well into the far tier
        {"id": "mid", "commence_time": _iso(3)},      # inside the slow tier
        {"id": "near", "commence_time": _iso(0.5)},   # inside the fast tier
    ]})
    CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    ttls = {c["event_id"]: c["ttl"] for c in stub.event_calls}

    assert ttls["near"] == settings.cards_fast_interval_minutes * 60
    assert ttls["mid"] == settings.cards_slow_interval_minutes * 60
    assert ttls["far"] == settings.cards_far_interval_minutes * 60
    assert ttls["near"] < ttls["mid"] < ttls["far"]


def test_the_window_reaches_two_days_out():
    """Read from config rather than hard-coded, so changing the window is one
    edit and the test still pins that the boundary is enforced."""
    inside = settings.cards_window_hours - 2
    outside = settings.cards_window_hours + 2
    stub = _StubOddsApi({"soccer_epl": [
        {"id": "in", "commence_time": _iso(inside)},
        {"id": "out", "commence_time": _iso(outside)},
    ]})
    CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    assert [c["event_id"] for c in stub.event_calls] == ["in"]


def test_a_budget_cut_off_still_keeps_the_soonest_fixtures():
    """With a 72h window far more fixtures are due at once, so the daily
    ceiling matters more — and what it drops must be the least urgent."""
    stub = _StubOddsApi({"soccer_epl": [
        {"id": "days_out", "commence_time": _iso(60)},
        {"id": "tomorrow", "commence_time": _iso(20)},
        {"id": "tonight", "commence_time": _iso(0.5)},
    ]})
    client = CardsApiClient(stub, sport_keys=["soccer_epl"])
    import app.cards.client as mod
    mod.settings.cards_daily_call_budget = 2
    try:
        client.get_events()
    finally:
        mod.settings.cards_daily_call_budget = 700
    assert [c["event_id"] for c in stub.event_calls] == ["tonight", "tomorrow"]


# --- the guard that binds is the vendor's own quota ------------------------

class _QuotaClient(_StubOddsApi):
    """A stub reporting quota the way TheOddsApi's headers do."""

    def __init__(self, fixtures, remaining, cost=1):
        super().__init__(fixtures)
        self.quota_remaining = remaining
        self.last_call_cost = cost


def _many(n):
    return {"soccer_epl": [
        {"id": f"e{i}", "commence_time": _iso(0.5 + i * 0.01)} for i in range(n)
    ]}


def test_the_sweep_stops_at_the_quota_reserve():
    """The reserve is left for the coupon fallback and the goalscorer scans.

    A locally counted budget cannot do this job: it is a guess at the per-call
    cost, and it resets on every restart. The vendor's remaining-quota header
    is right regardless.
    """
    stub = _QuotaClient(_many(10), remaining=settings.cards_quota_reserve)
    client = CardsApiClient(stub, sport_keys=["soccer_epl"])
    client.get_events()
    assert stub.event_calls == []


def test_the_sweep_spends_only_the_quota_above_the_reserve():
    stub = _QuotaClient(_many(10), remaining=settings.cards_quota_reserve + 3)
    CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    assert len(stub.event_calls) == 3


def test_the_real_per_call_cost_is_used_when_the_vendor_reports_it():
    """The (markets x regions) figure is unmeasured, so it is not assumed.

    At 2 credits a call, headroom of 6 buys 3 calls, not 6.
    """
    stub = _QuotaClient(_many(10), remaining=settings.cards_quota_reserve + 6, cost=2)
    CardsApiClient(stub, sport_keys=["soccer_epl"]).get_events()
    assert len(stub.event_calls) == 3


def test_the_daily_cap_still_applies_below_the_reserve_limit():
    """Two guards that fail differently: one bad day must not spend a month."""
    stub = _QuotaClient(_many(50), remaining=100_000)
    client = CardsApiClient(stub, sport_keys=["soccer_epl"])
    import app.cards.client as mod
    mod.settings.cards_daily_call_budget = 4
    try:
        client.get_events()
    finally:
        mod.settings.cards_daily_call_budget = 650
    assert len(stub.event_calls) == 4


def test_the_daily_cap_fits_inside_the_monthly_plan():
    """700/day was 21,000/month against a 20,000 plan — the guard permitted
    more than the budget it was guarding."""
    assert settings.cards_daily_call_budget * 30 < 20_000


# --- international football is covered, and a dead key is visible ------------
#
# An international window returned "No fixture kicks off within 48h" — true,
# and entirely because no international competition was on the key list. And a
# misspelled key would have looked exactly the same: list_events answers an
# unknown key with a 404 that reads as "no fixtures".

def test_the_internationals_are_on_the_sweep():
    from app.config import settings
    keys = settings.cards_sport_keys.split(",")
    assert "soccer_uefa_nations_league" in keys
    assert "soccer_fifa_world_cup_qualifiers_europe" in keys
    assert "soccer_uefa_euro_qualification" in keys


class _StubWithSports(_StubOddsApi):
    def __init__(self, fixtures, active):
        super().__init__(fixtures)
        self._active = active

    def list_sports(self):
        return [{"key": k} for k in self._active]


def test_a_key_the_vendor_does_not_list_is_named_in_the_sweep():
    stub = _StubWithSports({}, active=["soccer_epl"])
    c = CardsApiClient(stub, sport_keys=["soccer_epl", "soccer_uefa_nations_leage"])
    c.get_events()
    assert c.last_sweep["inactive_keys"] == ["soccer_uefa_nations_leage"]
    assert c.last_sweep["sport_keys"] == 2


def test_the_sweep_says_what_was_listed_and_when_the_next_game_is():
    """Listed-but-far and listed-nothing are different answers."""
    stub = _StubWithSports({"soccer_epl": [
        {"id": "far", "commence_time": _iso(100)},
        {"id": "farther", "commence_time": _iso(150)},
    ]}, active=["soccer_epl"])
    c = CardsApiClient(stub, sport_keys=["soccer_epl"])
    c.get_events()
    assert c.last_sweep["fixtures_in_window"] == 0
    assert c.last_sweep["fixtures_listed"] == 2
    assert c.last_sweep["next_kickoff"] is not None
    assert stub.event_calls == []                 # still spends nothing


def test_a_failed_key_check_does_not_break_the_sweep():
    c = _client({"soccer_epl": [{"id": "e1", "commence_time": _iso(0.5)}]})
    c.get_events()                                # stub has no list_sports
    assert c.last_sweep["inactive_keys"] is None
    assert c.last_sweep["called"] == 1


def test_the_empty_verdict_names_the_next_kickoff_and_the_dead_keys():
    from app.routers.cards import diagnose
    stub = _StubWithSports({"soccer_epl": [{"id": "far", "commence_time": _iso(100)}]},
                           active=["soccer_epl"])
    c = CardsApiClient(stub, sport_keys=["soccer_epl", "soccer_nope"])
    c.get_events()
    verdict = diagnose(c)["verdict"]
    assert "Next listed kickoff" in verdict
    assert "soccer_nope" in verdict
    # Nothing listed anywhere is said outright.
    empty = CardsApiClient(_StubWithSports({}, active=["soccer_epl"]),
                           sport_keys=["soccer_epl"])
    empty.get_events()
    assert "No upcoming fixture is listed" in diagnose(empty)["verdict"]


# --- probe: who carries corners/cards for one match --------------------------------

def test_the_probe_asks_by_region_with_no_book_filter_and_reports_every_book():
    from types import SimpleNamespace
    from app.routers.cards import probe_books

    calls = []

    class Raw(_StubOddsApi):
        quota_remaining, last_call_cost = 19000, 2

        def fetch_event(self, sport_key, event_id, markets, bookmakers=None,
                        ttl=None, regions=None):
            calls.append({"bookmakers": bookmakers, "regions": regions, "ttl": ttl})
            return {"home_team": "England", "away_team": "Spain",
                    "commence_time": _iso(20), "bookmakers": [
                        {"key": "williamhill", "markets": [{"key": "alternate_totals_corners"}]},
                        {"key": "paddypower", "markets": [{"key": "alternate_totals_cards"},
                                                          {"key": "alternate_totals_corners"}]},
                    ]}

    raw = Raw({"soccer_uefa_nations_league": [
        {"id": "e1", "commence_time": _iso(20), "home_team": "England", "away_team": "Spain"}]})
    cards = CardsApiClient(raw, sport_keys=["soccer_uefa_nations_league"])
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cards_client=cards)))
    out = probe_books(req)
    assert calls == [{"bookmakers": None, "regions": "uk", "ttl": 0}]
    assert out["match"] == "England vs Spain"
    assert out["paddypower_present"] is True
    assert out["paddypower_markets"] == ["alternate_totals_cards", "alternate_totals_corners"]
    assert "williamhill" in out["bookmakers"]


# --- match goals: the same ladder model over Pinnacle's goals ladder --------

def _goals_event(pin_lines, paddy_lines):
    def book(key, lines):
        outcomes = []
        for line, (over, under) in sorted(lines.items()):
            outcomes.append({"name": "Over", "point": line, "price": over})
            outcomes.append({"name": "Under", "point": line, "price": under})
        return {"key": key, "markets": [{"key": "totals_goals", "outcomes": outcomes}]}
    return [{
        "id": "g1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": _iso(0.5), "sport_key": "soccer_epl",
        "bookmakers": [book("pinnacle", pin_lines), book(PADDY_POWER, paddy_lines)],
    }]


# A realistic Pinnacle goals ladder around 2.7 goals.
PIN_GOALS = {1.5: (1.30, 3.75), 2.5: (1.95, 1.93), 3.5: (3.30, 1.36), 4.5: (6.25, 1.14)}


def test_goals_lines_paddy_quotes_are_priced_off_pinnacles_goals_ladder():
    from app.cards.laddered import compute_laddered_value
    # Paddy's 3.0 is a line Pinnacle does not quote; 2.5 over at 2.30 is long.
    rows = compute_laddered_value(_goals_event(PIN_GOALS, {2.5: (2.30, 1.62), 3.0: (2.40, 1.55)}), -0.5)
    assert rows and {r.market_key for r in rows} == {"totals_goals"}
    over25 = next(r for r in rows if r.line == 2.5 and r.side == "Over")
    assert over25.fair_odds == pytest.approx(1.97, abs=0.05)
    assert over25.edge > 0.10


def test_goals_lines_outside_the_bounds_are_never_priced():
    from app.cards.laddered import compute_laddered_value
    rows = compute_laddered_value(_goals_event(PIN_GOALS, {6.5: (15.0, 1.01)}), -1.0)
    assert rows == []


def test_the_feed_says_which_market_and_competition_each_bet_is():
    from app.routers.cards import build_feed

    class _Client:
        def get_events(self):
            return _goals_event(PIN_GOALS, {2.5: (2.30, 1.62)})

    feed = build_feed(_Client(), -0.5)
    assert feed[0].sport_key == "soccer_epl"
    bet = next(b for b in feed[0].bets if b.pick.startswith("Over"))
    assert (bet.market, bet.pick) == ("goals", "Over 2.5 goals")


def test_a_line_paddy_shops_do_not_carry_is_never_listed(monkeypatch):
    """corners:11.5 is excluded by default; Pinnacle's 11.5 rung still fits."""
    from app.cards.laddered import compute_laddered_value
    from app.config import settings
    events = _joined(PIN, {11.5: (9.0, 9.0), 9.5: (9.0, 9.0)})
    lines = {r.line for r in compute_laddered_value(events, -0.5)}
    assert 11.5 not in lines and 9.5 in lines
    monkeypatch.setattr(settings, "paddy_excluded_lines", "")
    assert 11.5 in {r.line for r in compute_laddered_value(events, -0.5)}
