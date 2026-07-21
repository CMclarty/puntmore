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
from datetime import date, timedelta


@dataclass(frozen=True)
class CouponPeriod:
    code: str
    label: str
    # Weekdays the coupon's fixtures play on. Monday = 0, matching
    # date.weekday(), so no conversion is needed anywhere.
    weekdays: tuple[int, ...]
    # The weekday the coupon is normally uploaded on. Used only to preselect
    # the right period in the UI — an upload on any day is still accepted.
    upload_weekday: int


COUPON_PERIODS: tuple[CouponPeriod, ...] = (
    CouponPeriod("MON_WED", "Mon–Wed", (0, 1, 2), upload_weekday=0),
    CouponPeriod("WED_FRI", "Wed–Fri", (2, 3, 4), upload_weekday=2),
    CouponPeriod("SAT", "Saturday", (5,), upload_weekday=4),
    CouponPeriod("SUN", "Sunday", (6,), upload_weekday=4),
)

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
    """
    period = get(code)
    if period is None:
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


def suggest(today: date) -> str:
    """The period to preselect when uploading on a given day.

    Picks the next window whose upload day has arrived — on Friday that is
    Saturday's coupon, since Sunday's shares the same upload day and Saturday
    comes first.
    """
    weekday = today.weekday()
    for period in COUPON_PERIODS:
        if period.upload_weekday == weekday:
            return period.code
    # Not an upload day: offer the next window that starts from here.
    for period in COUPON_PERIODS:
        if any(wd >= weekday for wd in period.weekdays):
            return period.code
    return COUPON_PERIODS[0].code
