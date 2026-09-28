"""Is this board fixture the one the coupon printed? Checks beyond the name.

Name similarity alone cannot tell a senior international from the same two
nations' under-19 side when the board prints the runners WITHOUT a marker —
"England vs Spain" under a league called "UEFA U19 Championship". Both sides
match exactly, the fixture prices, and it is the wrong game. That is how U19
fixtures kept reaching the tracker after the team-name markers were fixed
(Sept 2026). Three checks, in order of how much they can be trusted:

  COMPETITION  A league that names an age group, youth, reserve or women's
               competition makes every fixture in it a non-senior game,
               whatever its runners are called. A coupon row with no marker of
               its own is a senior fixture and never matches one. Independent
               of time, so it is the check that catches a youth game played
               on the same day as the senior one.
  DAY          The coupon prints a weekday and time. A candidate more than
               KICKOFF_EXCLUDE_HOURS from that slot is on another day and is
               not the printed fixture. Deliberately WIDE: matches are delayed,
               sometimes without notice, and early-morning fixtures (Asia,
               Oceania) are where the printed time and the board's disagree
               about the time zone — sometimes by several hours, across
               midnight. A tight window would unmatch those real fixtures.
  CLOSEST      Among candidates the names cannot separate — the senior game and
               the youth game both reading "England vs Spain" — the one nearest
               the printed slot wins. A tie-break, never an exclusion, so a
               delayed or time-shifted fixture is still found when it is the
               only one of its name.

The first two are exclusions: they remove candidates BEFORE the name match, so
a row that fails them goes UNMATCHED rather than to the next-best name. An
unpriced leg beats a confidently wrong one.

Cost: arithmetic on data already fetched — the board rows carry league and
kickoff. The competition verdict is cached per league name. No vendor calls.
"""
from datetime import datetime
from functools import lru_cache
import re

from .normalise import _squad_markers, normalise_team, split_sides

# Outside this distance from the printed slot a candidate is on another day.
# Twelve hours tolerates any delay short of a postponement and any time-zone
# confusion a morning kickoff produces, and still separates a youth fixture
# played a day either side of the senior one — the common international-window
# pattern. A postponement to another day leaves the leg unmatched, which is
# right: the printed bet is void or re-struck.
KICKOFF_EXCLUDE_HOURS = 12

_WEEK_MIN = 7 * 24 * 60

# Youth and reserve competitions that name NO age group. Primavera (Italy),
# Juvenil (Spain), Premier League 2 and the Professional Development League
# (England's U21 tier) all carry first-team club names on U19–U21 sides.
_YOUTH_LEAGUE_RE = re.compile(
    r"\b(?:youth|primavera|juvenil|juniors?|junioren|academy|reserves?"
    r"|premier league 2|professional development|development league)\b"
)


def _minute_of_week(dt: datetime) -> int:
    return dt.weekday() * 24 * 60 + dt.hour * 60 + dt.minute


def kickoff_distance_min(printed: datetime | None, board: datetime | None) -> int | None:
    """Minutes between the printed slot and the board kickoff, or None.

    Compared by MINUTE OF THE WEEK, not by date. The coupon prints a weekday,
    and its date is resolved forward from the upload — so a Sunday upload of a
    Saturday-to-Monday sheet dates "SAT" a week late. The board spans only
    seven days, so weekday and time are unambiguous on it, and comparing them
    directly sidesteps the date resolution. Circular, so 23:30 SAT against
    00:30 SUN is one hour, not a day.
    """
    if printed is None or board is None:
        return None
    d = abs(_minute_of_week(printed) - _minute_of_week(board))
    return min(d, _WEEK_MIN - d)


def on_the_printed_day(printed: datetime | None, board: datetime | None) -> bool:
    """Unknown on either side passes: a kickoff the read could not see is not
    evidence of the wrong fixture."""
    d = kickoff_distance_min(printed, board)
    return d is None or d <= KICKOFF_EXCLUDE_HOURS * 60


@lru_cache(maxsize=4096)
def league_is_non_senior(league_name: str | None) -> bool:
    """A competition whose every fixture is a youth, reserve or women's game."""
    if not league_name:
        return False
    name = normalise_team(league_name)
    return bool(_squad_markers(name)) or bool(_YOUTH_LEAGUE_RE.search(name))


def query_is_senior(match_name: str) -> bool:
    """No squad marker on either side of the printed fixture."""
    sides = split_sides(match_name) or (match_name, "")
    return not any(_squad_markers(normalise_team(s)) for s in sides if s)


def excluded_reason(
    senior: bool, printed_kickoff: datetime | None,
    league_name: str | None, board_kickoff: datetime | None,
) -> str | None:
    """Why this candidate cannot be the printed fixture, or None if it can.
    `senior` is `query_is_senior(match_name)`, computed once per query."""
    if senior and league_is_non_senior(league_name):
        return "youth_league"
    if not on_the_printed_day(printed_kickoff, board_kickoff):
        return "wrong_day"
    return None
