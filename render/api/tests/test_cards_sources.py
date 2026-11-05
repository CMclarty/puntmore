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

from app.cards.oddspapi import PADDY_POWER
from app.config import settings


def _iso(delta_hours):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


class _SweepStub:
    """What the sweep needs of a Paddy source: get_events() and last_sweep."""

    def __init__(self, events, last_sweep=None):
        self._events = events
        self.last_sweep = last_sweep or {}

    def get_events(self):
        return [dict(e) for e in self._events]


def _valued_event():
    """One Paddy Power event with real value, the fair side on the SAME event
    as OddsPapi delivers it: Pinnacle 1.90/1.95 at 10.5 de-vigs to ~2.03/1.98,
    so Paddy's 2.10 over is value."""
    return {
        "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": _iso(1), "sport_key": "soccer_epl",
        "bookmakers": [
            {"key": PADDY_POWER, "markets": [{"key": "alternate_totals_corners", "outcomes": [
                {"name": "Over", "point": 10.5, "price": 2.10},
                {"name": "Under", "point": 10.5, "price": 1.80},
            ]}]},
            {"key": "pinnacle", "markets": [{"key": "alternate_totals_corners", "outcomes": [
                {"name": "Over", "point": 10.5, "price": 1.90},
                {"name": "Under", "point": 10.5, "price": 1.95},
            ]}]},
        ],
    }


# --- the sweep, and not re-alerting the same bet every tick -----------------

class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


def _valued_client():
    return _SweepStub([_valued_event()])


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
    _reset_dedupe()
    sched.sweep_cards(_valued_client(), _Notifier())
    assert sched._cards_notified

    # Next sweep finds nothing (fixtures kicked off, markets down).
    sched.sweep_cards(_SweepStub([]), _Notifier())
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
    """No Paddy source (no ODDSPAPI_API_KEY) should mean no job, rather than a
    job that wakes every two minutes to do nothing."""
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
        # OddsPapi's tightest gear: every 2 minutes, its cache deciding what
        # is actually fetched.
        assert job.trigger.interval.total_seconds() == 120
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

    # Fair at 7.5 is short and at 12.5 ~4.22; both beat the band comfortably.
    events = _joined(PIN, {7.5: (1.33, 3.40), 12.5: (4.80, 1.24)})
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
    # An INTEGER home rung (5.0) and an integer handicap (-1.0) are quoted too:
    # information for the share, read with the push rule, never shown.
    events = _joined_split(PIN, home={5.0: (1.95, 1.90), 5.5: (1.90, 1.95)}, away={4.5: (1.95, 1.90)},
                           handicap={-0.5: (1.80, 2.05), 0.5: (1.50, 2.60), -1.0: (2.05, 1.80)})
    split = team_split(events[0], _get_bookmaker(events[0], "pinnacle"), "team_corners_home", "soccer_epl")
    assert split is not None
    _, point, totals, share, quoted = split
    assert share.kinds == {"home_line", "away_line", "handicap"}
    assert set(share.sources) == {"home_line@5", "home_line@5.5", "away_line@4.5",
                                  "handicap@-0.5", "handicap@0.5", "handicap@-1"}
    assert 0.5 < share.p < 0.7 and not share.disagree
    assert len(totals) == 3                      # the match law and its band
    assert quoted == {"home": (5.0, 5.5), "away": (4.5, 4.5)}


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

    quiet = _SweepStub([], {"source": "oddspapi", "fixtures_in_window": 0, "window_hours": 168})
    verdict = diagnose(quiet)["verdict"]
    assert "within" in verdict and "kickoff" in verdict

    # Fixtures fetched and priced, but nothing clears the guards.
    from app.cards import laddered
    laddered.LAST_RUN.update({
        "ladders_fitted": 3, "ladders_refused": 0,
        "priceable_paddy_lines": 12, "rows": 0,
    })
    busy = _SweepStub([_valued_event()], {
        "source": "oddspapi", "fair_source": "oddspapi:pinnacle", "fixtures_in_window": 1,
        "called": 1, "carried_paddy_prices": 1, "carried_pinnacle_prices": 1,
    })
    verdict = diagnose(busy)["verdict"]
    assert verdict.startswith("WORKING"), verdict
    laddered.LAST_RUN.clear()


def test_diagnostics_reports_paddy_not_posting_the_market():
    from app.routers.cards import diagnose
    # The fixture exists and was called; Paddy has not posted the market.
    client = _SweepStub([], {"source": "oddspapi", "fixtures_in_window": 1,
                             "called": 1, "carried_paddy_prices": 0})
    assert "not posting" in diagnose(client)["verdict"]


# --- the empty verdict names the next kickoff ------------------------------

def test_the_empty_verdict_names_the_next_kickoff():
    from app.routers.cards import diagnose
    far = _SweepStub([], {"source": "oddspapi", "fixtures_in_window": 0, "window_hours": 168,
                          "next_kickoff": "2026-12-01T15:00:00Z"})
    assert "Next listed kickoff" in diagnose(far)["verdict"]
    # Nothing listed anywhere is said outright.
    empty = _SweepStub([], {"source": "oddspapi", "fixtures_in_window": 0, "window_hours": 168})
    assert "No upcoming fixture is listed" in diagnose(empty)["verdict"]


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
