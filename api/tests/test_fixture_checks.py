"""A coupon row is matched to the fixture the coupon PRINTED — see fixture_checks.py.

U19 fixtures kept reaching the tracker after the team-name markers were fixed,
because the board can print a youth game's runners with no marker at all
("England vs Spain" under "UEFA U19 Championship") and because every board row
after the first with a given name was thrown away. These pin the checks that
separate them, and the tolerance the checks must keep for delays and
time-zone-confused morning kickoffs.
"""
from datetime import datetime, timedelta
import time

from app.fixture_checks import (
    KICKOFF_EXCLUDE_HOURS,
    excluded_reason,
    kickoff_distance_min,
    league_is_non_senior,
    on_the_printed_day,
    query_is_senior,
)
from app.providers.base import MatchQuery
from app.providers.bettingiscool import BettingIsCoolProvider

SAT_1945 = datetime(2026, 10, 10, 19, 45)      # a Saturday


# --- the competition ---------------------------------------------------------

def test_youth_and_womens_competitions_are_recognised_whatever_they_call_themselves():
    for league in ("UEFA U19 Championship", "Euro U-21 Qualification",
                   "UEFA Youth League", "Italy - Primavera 1", "Premier League 2",
                   "England - Professional Development League",
                   "Spain - Division de Honor Juvenil", "Brazil - Campeonato Sub-20",
                   "England - Women's Super League", "Germany - Frauen Bundesliga",
                   "Netherlands - Reserve League"):
        assert league_is_non_senior(league), league


def test_senior_competitions_are_not():
    for league in ("England - Premier League", "UEFA Nations League",
                   "World Cup Qualification UEFA", "Germany - 2. Bundesliga",
                   "Hannover 96 Friendlies", "International Friendlies", None, ""):
        assert not league_is_non_senior(league), league


def test_a_row_is_senior_unless_it_carries_a_marker():
    assert query_is_senior("England v Spain")
    assert not query_is_senior("England U19 v Spain U19")
    assert not query_is_senior("Arsenal Women v Chelsea Women")


def test_a_senior_row_never_matches_a_youth_competition():
    assert excluded_reason(True, SAT_1945, "UEFA U19 Championship", SAT_1945) == "youth_league"
    # ...but a youth row may: a coupon that genuinely prints a youth game.
    assert excluded_reason(False, SAT_1945, "UEFA U19 Championship", SAT_1945) is None


# --- the day -------------------------------------------------------------------

def test_a_fixture_on_another_day_is_not_the_printed_one():
    assert excluded_reason(True, SAT_1945, "Friendlies", SAT_1945 - timedelta(days=1)) == "wrong_day"
    assert excluded_reason(True, SAT_1945, "Friendlies", SAT_1945 + timedelta(days=2)) == "wrong_day"


def test_a_delayed_kickoff_is_still_the_printed_fixture():
    """Delays happen, sometimes unannounced. Hours late is still the fixture."""
    for late in (15, 90, 180, 360, 11 * 60):
        assert on_the_printed_day(SAT_1945, SAT_1945 + timedelta(minutes=late)), late


def test_a_morning_kickoff_with_a_time_zone_mix_up_is_still_the_printed_fixture():
    """Early fixtures (Asia, Oceania) are where the printed time and the board's
    disagree about the zone — by hours, and across midnight."""
    printed = datetime(2026, 10, 10, 2, 0)                 # SAT 02:00 as printed
    for board in (datetime(2026, 10, 9, 17, 0),            # FRI 17:00: -9h (AEST-ish)
                  datetime(2026, 10, 9, 23, 30),            # FRI 23:30: across midnight
                  datetime(2026, 10, 10, 11, 0)):           # SAT 11:00: +9h
        assert on_the_printed_day(printed, board), board


def test_the_day_check_is_by_weekday_so_a_late_upload_does_not_break_it():
    """"SAT 19:45" read on a Sunday upload resolves to NEXT Saturday. The board
    only spans a week, so weekday-and-time is what identifies the slot."""
    printed_a_week_late = SAT_1945 + timedelta(days=7)
    assert kickoff_distance_min(printed_a_week_late, SAT_1945) == 0


def test_distance_is_circular_across_the_end_of_the_week():
    sun_2330 = datetime(2026, 10, 11, 23, 30)
    mon_0030 = datetime(2026, 10, 12, 0, 30)
    assert kickoff_distance_min(sun_2330, mon_0030) == 60


def test_an_unknown_kickoff_on_either_side_refuses_nothing():
    assert on_the_printed_day(None, SAT_1945)
    assert on_the_printed_day(SAT_1945, None)


def test_the_window_is_wide_on_purpose():
    assert KICKOFF_EXCLUDE_HOURS >= 12, (
        "narrowing this unmatches delayed and time-zone-shifted real fixtures"
    )


# --- through the provider ----------------------------------------------------------

class _Board(BettingIsCoolProvider):
    def __init__(self, board):
        super().__init__("test-key")
        self._board = board

    def _fixtures(self):
        return [dict(r) for r in self._board]

    def _odds(self, event_id):
        return [{"period": 0, "market": "moneyline", "line": None,
                 "odds0": 3.5, "odds1": 2.0, "odds2": 4.0 + event_id / 1000}]


def _row(event_id, home, away, league, starts):
    return {"event_id": event_id, "league_name": league,
            "runner_home": home, "runner_away": away, "starts": starts}


def _win(name, kickoff):
    return MatchQuery(1, name, name.split(" v ")[0] + " Win", kickoff=kickoff)


def test_the_senior_fixture_wins_when_the_youth_one_kicks_off_first():
    """THE bug. Both read "England vs Spain"; the U19 game is Thursday, the
    senior Saturday. The board is fetched from today, so the youth row came
    first — and was the only one kept."""
    p = _Board([
        _row(19, "England", "Spain", "Friendlies", "2026-10-08T14:00:00"),   # U19, Thu
        _row(1, "England", "Spain", "UEFA Nations League", "2026-10-10T19:45:00"),
    ])
    res = p.get_fair_odds([_win("England v Spain", SAT_1945)])[0]
    assert res.kickoff == datetime(2026, 10, 10, 19, 45)
    assert res.fair_odds is not None


def test_same_day_youth_game_is_refused_by_its_competition():
    """Same day, three hours apart: the day check allows both, so the
    competition is what separates them."""
    p = _Board([
        _row(19, "England", "Spain", "UEFA U19 Championship", "2026-10-10T16:45:00"),
        _row(1, "England", "Spain", "UEFA Nations League", "2026-10-10T19:45:00"),
    ])
    res = p.get_fair_odds([_win("England v Spain", SAT_1945)])[0]
    assert res.kickoff == datetime(2026, 10, 10, 19, 45)


def test_only_the_youth_game_on_the_board_leaves_the_leg_unmatched_and_says_why():
    p = _Board([_row(19, "England", "Spain", "UEFA U19 Championship",
                     "2026-10-10T19:45:00")])
    res = p.get_fair_odds([_win("England v Spain", SAT_1945)])[0]
    assert res.matched_fixture is None and res.fair_odds is None
    assert p.last_pass["no_match"] == 1
    assert p.last_pass["refused_youth"] == 1


def test_only_a_game_on_another_day_leaves_the_leg_unmatched_and_says_why():
    p = _Board([_row(1, "England", "Spain", "Friendlies", "2026-10-08T14:00:00")])
    res = p.get_fair_odds([_win("England v Spain", SAT_1945)])[0]
    assert res.matched_fixture is None
    assert p.last_pass["refused_day"] == 1


def test_a_delayed_fixture_alone_on_the_board_still_prices():
    p = _Board([_row(1, "England", "Spain", "Friendlies", "2026-10-10T22:15:00")])
    res = p.get_fair_odds([_win("England v Spain", SAT_1945)])[0]
    assert res.fair_odds is not None


def test_two_same_named_fixtures_in_the_window_go_to_the_nearest():
    """Neither refused (same day, both senior-looking) — the nearest wins."""
    p = _Board([
        _row(2, "England", "Spain", "Friendlies", "2026-10-10T13:00:00"),
        _row(1, "England", "Spain", "Friendlies", "2026-10-10T20:00:00"),
    ])
    res = p.get_fair_odds([_win("England v Spain", SAT_1945)])[0]
    assert res.kickoff == datetime(2026, 10, 10, 20, 0)


def test_a_leg_with_no_printed_kickoff_is_matched_as_before():
    p = _Board([_row(1, "England", "Spain", "Friendlies", "2026-10-08T14:00:00")])
    res = p.get_fair_odds([_win("England v Spain", None)])[0]
    assert res.fair_odds is not None


def test_the_checks_do_not_materially_slow_a_full_pricing_pass():
    """A busy day's board against a full sheet. The checks are arithmetic on
    rows already fetched; the pass must stay the same order of cost."""
    board = [
        _row(i, f"Home Club {i}", f"Away Club {i}",
             "UEFA U19 Championship" if i % 10 == 0 else f"League {i % 40}",
             f"2026-10-{8 + i % 5:02d}T{12 + i % 9:02d}:00:00")
        for i in range(1000)
    ]
    queries = [MatchQuery(i, f"Home Club {i * 6} v Away Club {i * 6}",
                          f"Home Club {i * 6} Win",
                          kickoff=datetime(2026, 10, 8 + (i * 6) % 5, 12 + (i * 6) % 9))
               for i in range(150)]
    p = _Board(board)
    p._odds = lambda event_id: []       # time the MATCHING, not de-vigging
    t0 = time.perf_counter()
    p.get_fair_odds(queries)
    elapsed = time.perf_counter() - t0
    assert elapsed < 20, f"{elapsed:.1f}s for 150 legs against 1000 fixtures"
