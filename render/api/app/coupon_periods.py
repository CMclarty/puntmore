"""Coupon periods — the fixed schedule coupons are issued on.

A coupon does not cover a single match day: it covers a window, and is uploaded
on a set weekday. Four windows repeat every week:

    Mon–Wed   uploaded Monday
    Wed–Fri   uploaded Wednesday
    Saturday  uploaded Friday
    Sunday    uploaded Friday

Wednesday deliberately appears in two windows — that is how the coupons are
issued, not an off-by-one.

This is an attribute of a coupon, NOT a return of the old sets/versions model
that PLAN.md rules out. There is no set navigation and no per-period entity;
`Coupon.period` is a label with a derivable date range.
"""
from dataclasses import dataclass
import re
from datetime import date, datetime, timedelta, time as dt_time


@dataclass(frozen=True)
class CouponPeriod:
    code: str
    label: str
    # Weekdays the coupon's fixtures play on. Monday = 0, matching
    # date.weekday(), so no conversion is needed anywhere. Empty when the
    # window is variable.
    weekdays: tuple[int, ...]
    # The weekday the coupon is normally uploaded on. Used only to preselect
    # the right period in the UI — an upload on any day is still accepted.
    upload_weekday: int | None = None
    # Variable windows (GG Bonus / GG No Draw) do NOT run to a fixed schedule:
    # the issue prints its own dates, which differ week to week. Their real
    # range is taken from the fixtures OCR reads off the coupon, never assumed.
    variable: bool = False


# Fixed weekly windows — the five priced coupon types run to this schedule.
FIXED_PERIODS: tuple[CouponPeriod, ...] = (
    CouponPeriod("MON_WED", "Mon–Wed", (0, 1, 2), upload_weekday=0),
    CouponPeriod("WED_FRI", "Wed–Fri", (2, 3, 4), upload_weekday=2),
    CouponPeriod("SAT", "Saturday", (5,), upload_weekday=4),
    CouponPeriod("SUN", "Sunday", (6,), upload_weekday=4),
)

# Variable windows — GG coupons are issued as one midweek and one weekend
# edition whose dates move. The observed midweek issue ran Mon 20 – Thu 23 Jul,
# which is not any of the fixed windows; that is why these are separate.
VARIABLE_PERIODS: tuple[CouponPeriod, ...] = (
    CouponPeriod("MIDWEEK", "Midweek", (), variable=True),
    CouponPeriod("WEEKEND", "Weekend", (), variable=True),
)

COUPON_PERIODS: tuple[CouponPeriod, ...] = FIXED_PERIODS + VARIABLE_PERIODS

BY_CODE = {p.code: p for p in COUPON_PERIODS}


def get(code: str) -> CouponPeriod | None:
    return BY_CODE.get(code)


def is_valid(code: str) -> bool:
    return code in BY_CODE


def date_range(code: str, uploaded_on: date) -> tuple[date, date] | None:
    """First and last fixture date for a period uploaded on a given day.

    Resolves forwards: the window starting on or after the upload date. A
    Saturday coupon uploaded on Friday covers tomorrow; the same coupon
    uploaded late on Saturday still covers that day rather than jumping a week.

    Returns None for variable windows — their dates come from the coupon's own
    fixtures (see `range_from_kickoffs`), because there is no schedule to
    derive them from.
    """
    period = get(code)
    if period is None or period.variable:
        return None
    first_weekday = period.weekdays[0]
    delta = (first_weekday - uploaded_on.weekday()) % 7
    start = uploaded_on + timedelta(days=delta)
    # Uploaded mid-window: keep the window we are inside rather than skipping
    # to next week's.
    if uploaded_on.weekday() in period.weekdays:
        start = uploaded_on - timedelta(
            days=uploaded_on.weekday() - first_weekday
        )
    return start, start + timedelta(days=len(period.weekdays) - 1)


def range_from_kickoffs(kickoffs: list[date]) -> tuple[date, date] | None:
    """The window a variable coupon actually covers, from its own fixtures.

    GG coupons print their dates rather than following a schedule, so the only
    reliable source is the fixtures themselves.
    """
    dates = [k for k in kickoffs if k is not None]
    if not dates:
        return None
    return min(dates), max(dates)


def suggest(today: date) -> str:
    """The period to preselect when uploading on a given day.

    Picks the next window whose upload day has arrived — on Friday that is
    Saturday's coupon, since Sunday's shares the same upload day and Saturday
    comes first.
    """
    weekday = today.weekday()
    for period in FIXED_PERIODS:
        if period.upload_weekday == weekday:
            return period.code
    # Not an upload day: offer the next window that starts from here.
    for period in FIXED_PERIODS:
        if any(wd >= weekday for wd in period.weekdays):
            return period.code
    return FIXED_PERIODS[0].code


def suggest_variable(today: date) -> str:
    """Midweek or weekend, by where in the week we are. Fri–Sun reads weekend."""
    return "WEEKEND" if today.weekday() >= 4 else "MIDWEEK"


# Coupons print a weekday and time ("MON 17:00"), never a date — the year and
# month come from context. Parsed against the upload date so a coupon read on
# Monday resolves MON to that day, not a week later.
#
# THE PRINTED TIME IS GMT (Callum, July 2026). It is therefore stored as-is,
# naive, and read back as UTC — by the frontend's countdown and by the
# kicked-off sweep alike. There is deliberately NO local-time conversion here:
# adding one would shift every kickoff by an hour through British Summer Time
# and make both of those wrong. Do not "fix" this.
_WEEKDAY_ABBR = {
    "MON": 0, "TUE": 1, "TUES": 1, "WED": 2, "THU": 3, "THUR": 3, "THURS": 3,
    "FRI": 4, "SAT": 5, "SUN": 6,
}
_KICKOFF_RE = re.compile(
    r"^\s*([A-Za-z]{3,5})\s*,?\s*(\d{1,2})[:.](\d{2})\s*$"
)


def parse_kickoff(text: str | None, reference: date) -> datetime | None:
    """"MON 17:00" -> a datetime, resolved forward from `reference`.

    Returns None rather than a guess when the format is not recognised: a wrong
    kickoff would silently misorder the list and mislabel how urgent a fixture
    is.
    """
    if not text:
        return None
    m = _KICKOFF_RE.match(text)
    if not m:
        return None
    abbr, hh, mm = m.group(1).upper(), int(m.group(2)), int(m.group(3))
    weekday = _WEEKDAY_ABBR.get(abbr)
    if weekday is None or hh > 23 or mm > 59:
        return None
    delta = (weekday - reference.weekday()) % 7
    return datetime.combine(
        reference + timedelta(days=delta), dt_time(hour=hh, minute=mm)
    )
