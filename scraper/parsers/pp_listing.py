"""Kick-off times off a Paddy Power competition listing (the page a matches
job keeps open), so the runner opens only the matches kicking off within the
job's window (schedule.within_h; Callum, 12 Nov 2026). Not a row parser: the
runner calls it on its listing tabs, and it reads the DOM the page already
has - no request.

Written from the Premier League listing the Scraper's page held on 12 Nov
2026 (`python -m scraper dump pp-epl-most`, three dumps that morning, the same
fixtures in each; trimmed into tests/fixtures/pp_listing/epl.html):

- The fixtures are one card (`avb-coupon-card-items`) of items
  (`.avb-coupon-card-items-item`), one per day. Each item's sub-header title
  (`.subheader__text--title`) is the day: "Saturday 21", "Sunday 22",
  "Monday 23" - the weekday and the day of the month, no month.
- Under it each fixture is an `avb-item`: a scoreboard link
  (`a.avb-item__scoreboard`, href "/football/english-premier-league/
  man-city-v-fulham-36293092") holding the two team names
  (`.ui-scoreboard-runner__home` / `__away`), the three prices, and a footer
  whose left label (`.line-footer__left-label`) is the kick-off time, "12:30".
- The time is London time: 12:30 on Saturday 21 Nov is the 12:30Z the page's
  own competition-page payload gives that game (openDate), as are the rest.

The La Liga and Serie A listings (`pp-epl-most_list_1`, `_list_2` in the
samples of 12 Nov 2026) were seen only as what they received: the same page
document and the same card type (COMPETITION_AVB) with their fixtures'
names and openDates, not as a drawn page.

NOT read, because no real page has shown them: whatever Paddy writes over
today's, tomorrow's or in-play games ("Today", "Tomorrow", "In-Play" or
anything else that is not "<Weekday> <day of month>"), and a footer label that
is not a time. A fixture under such a header, or with such a label, has no
kick-off: the runner does not open it, and says so when no fixture on the
job's listings has one.

A day header is the first date from yesterday on (London) with that weekday
and day of the month, so "Tuesday 1" read on Sat 28 Nov is 1 Dec.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_DAY = re.compile(r"^(" + "|".join(WEEKDAYS) + r") (\d{1,2})$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
LOOK_BACK_DAYS = 1           # a header for yesterday still names yesterday
LOOK_AHEAD_DAYS = 62         # ...and the first match within about two months ahead

# Each fixture's scoreboard link, its day (its item's sub-header) and its time
# (its row's footer label), as the page draws them. Raw text; Python reads it.
_READ = """() => Array.from(
  document.querySelectorAll('.avb-coupon-card-items-item a.avb-item__scoreboard[href]'), a => {
    const text = (root, sel) => {
      const el = root && root.querySelector(sel);
      return el ? el.textContent.trim() : '';
    };
    return {url: a.href,
            day: text(a.closest('.avb-coupon-card-items-item'), '.subheader__text--title'),
            time: text(a.closest('.avb-item'), '.line-footer__left-label'),
            home: text(a, '.ui-scoreboard-runner__home'),
            away: text(a, '.ui-scoreboard-runner__away')};
  })"""


@dataclass(frozen=True)
class Fixture:
    url: str                     # absolute, as runner.match_links gives it
    name: str                    # "Home v Away" as the listing names them, else the url's slug
    kickoff: float | None        # epoch seconds; None when it cannot be read


def day_date(header: str, today: date) -> date | None:
    """"Saturday 21" -> the first date from LOOK_BACK_DAYS before `today` with
    that weekday and day of the month; None for any other header."""
    m = _DAY.match(" ".join(header.split()))
    if not m:
        return None
    weekday, dom = WEEKDAYS.index(m.group(1)), int(m.group(2))
    for ahead in range(-LOOK_BACK_DAYS, LOOK_AHEAD_DAYS + 1):
        d = today + timedelta(days=ahead)
        if d.day == dom and d.weekday() == weekday:
            return d
    return None


def kickoff_at(header: str, label: str, now: float) -> float | None:
    """A fixture's kick-off from its day header and time label, London time,
    as epoch seconds; None when either is not one this reads."""
    t = _TIME.match(label.strip())
    d = day_date(header, datetime.fromtimestamp(now, LONDON).date())
    if t is None or d is None:
        return None
    return datetime(d.year, d.month, d.day, int(t.group(1)), int(t.group(2)), tzinfo=LONDON).timestamp()


def _slug(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or url


def from_rows(rows, now: float) -> dict[str, Fixture]:
    """What _READ gave, as url -> Fixture (the first row for a url wins)."""
    out: dict[str, Fixture] = {}
    for r in rows or ():
        if not isinstance(r, dict) or not isinstance(r.get("url"), str):
            continue
        url = r["url"].split("#")[0]
        if url in out:
            continue
        home, away = str(r.get("home") or ""), str(r.get("away") or "")
        name = f"{home} v {away}" if home and away else _slug(url)
        out[url] = Fixture(url, name, kickoff_at(str(r.get("day") or ""), str(r.get("time") or ""), now))
    return out


def fixtures(page, now: float) -> dict[str, Fixture]:
    """Every fixture row on the listing, url -> Fixture. {} when the page
    cannot be read (closed, navigating)."""
    try:
        rows = page.evaluate(_READ)
    except Exception:
        return {}
    return from_rows(rows, now)


def kickoffs(page, now: float) -> dict[str, float]:
    """Match url -> kick-off (epoch seconds), for the fixtures whose kick-off
    could be read."""
    return {u: f.kickoff for u, f in fixtures(page, now).items() if f.kickoff is not None}
