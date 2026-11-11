"""Hills on odds-api.net: William Hill's corners, cards and goals lines, with
Pinnacle's as the fair side, mapped into the events the counts pipeline
(`feed.price_events`) already reads. This module is the pure part: no HTTP,
no clock of its own. The client is `oddsapinet_client.py`.

WHAT ODDS-API.NET IS (checked live 11 Nov 2026; do not re-probe):
- Base https://api.odds-api.net/v1, key in the `X-API-Key` header. Growth plan:
  250,000 credits a month, 120 requests a minute, ONE credit per call, failed
  calls included. `/usage` reports the spend.
- `GET /events?sport=soccer&start_from&start_to&limit=1000` lists about six
  days ahead: `event_id`, `league` (a NAME, no id), `start_time` (unix seconds),
  `home_team`, `away_team`, `bookmakers` (the books that price it). Started and
  finished events drop off. The Championship is listed under two names
  ("English Championship", "EFL Championship"); the same game can appear under
  both. Euro qualifiers have no name there.
- `GET /events/{id}/odds/snapshot?bookmakers=williamhill,pinnacle&price_fields=odds
  &types=total,team total,handicap&periods=full time` gives both books in one
  call (~160 KB, ~1.4 s). Items: `bookmaker`, `bet_type`, `metric`, `period`,
  `line` (a STRING: "over 9.5", "+2.5", "odd"), `side` (home/away on team
  totals and handicaps), `odds`, `is_available`. `market_key` is inconsistent
  and never read. Betfair's `metric` comes upper-case, so metrics are compared
  lower-case. Top level: `bookmaker_as_of_ts_ms` per book,
  `target_refresh_interval_seconds` (60 s inside six hours of kick-off,
  ~2,559-3,599 s further out), `stale_bookmakers` (a list of books older than
  twice the target, or a bare 0 when none), `complete`.
- Freshness seen: William Hill 1.5-1.7 min old inside six hours, 64-108 min
  old on games 6-31 hours out; Pinnacle always under five minutes.
- William Hill: corners, cards and goals totals and team totals at every half
  line. Pinnacle: a 5-rung corners ladder, a 5-rung cards ladder, goals totals
  (with quarter lines), one team-total line per side, corners and cards
  handicaps. Saves come ONLY as per-keeper William Hill player props: there is
  no match saves total, so Hills on odds-api.net has no saves.
- `GET /events/{id}/results`: `status: pending` until settled, then
  `score_records` by side x metric (corners, cards, standard = goals, ...) x
  period, each `status: final`. No saves, no red/yellow split.

BB BOOST'S LEGS (hills/legs.py, hills/settle.py): a leg is one of William
Hill's half lines in the snapshot, id'd `<market key>:<line>` with the side
("Over" / "Under") as its outcome id, so it settles by reading the results'
full-time, final counts: a team key off its side's record, a match key off
both sides' (and not before both are final). Only a result whose
`status_detail` is "FT" settles: the lines are 90 minutes and the vendor
does not say whether "full time" includes extra time, so "AET", "Pen" or no
detail leaves the legs for a hand result. No saves leg is offered, so none
settles itself.

THE MAPPING (spec "Market mapping"): full time, available, odds > 1.0, William
Hill and Pinnacle only. Totals read "<over|under> <n>"; odd/even and quarter
lines (x.25, x.75) are dropped. A handicap (Pinnacle only) becomes
{name: Home|Away, point: the HOME side's handicap}: the home item's line as
given, the away item's negated, as pricing/counts/split.py reads it. Prices
stay raw; value.py and laddered.py de-vig. The vendor's own fair fields are
never read. Event ids are namespaced `oan:<vendor id>`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import math
import re
import threading
import time
from typing import Callable

from app.cards.feed import market_name
from app.cards.oddspapi import (
    CARDS, CARDS_HANDICAP, CORNERS, CORNERS_HANDICAP, GOALS, TEAM_CARDS_AWAY,
    TEAM_CARDS_HOME, TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME, TOURNAMENT_SPORT_KEYS,
    WILLIAM_HILL,
)
from app.cards.oddsapinet_client import BudgetSpent, RateLimited
from app.hills.legs import OFFERED, _label
from app.pricing.counts.lines import is_half_line

logger = logging.getLogger("uvicorn.error")

OAN_PREFIX = "oan:"
PINNACLE = "pinnacle"
VENDOR = "odds-api.net"
_BOOKS = (WILLIAM_HILL, PINNACLE)

# odds-api.net's league name -> the Sofascore tournament id the rest of the
# code keys on (target.py's competitions, TOURNAMENT_SPORT_KEYS). The same set
# Hills swept on OddsPapi; the Championship goes by two names on the vendor.
HILLS_LEAGUES: dict[str, int] = {
    "EPL": 17, "English FA Cup": 19, "EFL Cup": 21, "UEFA Champions League": 7,
    "English Championship": 18, "EFL Championship": 18, "League 1": 24,
    "La Liga": 8, "Serie A": 23, "Bundesliga": 35, "Ligue 1": 34,
    "Portuguese Primeira Liga": 238, "UEFA Europa League": 679,
    "UEFA Nations League": 23755, "World Cup Qualifiers Europe": 11,
}

_TOTAL_KEYS = {"corners": CORNERS, "cards": CARDS, "standard": GOALS}
_TEAM_KEYS = {
    ("corners", "home"): TEAM_CORNERS_HOME, ("corners", "away"): TEAM_CORNERS_AWAY,
    ("cards", "home"): TEAM_CARDS_HOME, ("cards", "away"): TEAM_CARDS_AWAY,
}
_HANDICAP_KEYS = {"corners": CORNERS_HANDICAP, "cards": CARDS_HANDICAP}
_TOTAL_LINE_RE = re.compile(r"^\s*(over|under)\s+(\d+(?:\.\d+)?)\s*$", re.I)
_HANDICAP_LINE_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*$")

TTL_MIN = 120
TTL_MAX = 3600


@dataclass(frozen=True)
class Listing:
    """One listed game in a Hills competition. `event_id` is the vendor's,
    bare (no `oan:`)."""
    event_id: str
    home: str
    away: str
    start: datetime
    league: str
    books: frozenset[str]


@dataclass
class SnapshotLines:
    """One snapshot, parsed. `markets` is {book: {market_key: outcomes}} for the
    books that priced anything; `as_of` is each book's age stamp."""
    markets: dict[str, dict[str, list[dict]]]
    as_of: dict[str, datetime]
    stale_books: frozenset[str]
    refresh_seconds: float | None
    complete: bool
    # The vendor's `oldest_bookmaker_as_of_ts_ms`: the age to fall back on
    # when William Hill's own stamp is missing.
    oldest_as_of: datetime | None = None


def _items(body) -> list[dict]:
    items = body.get("items") if isinstance(body, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _start(raw) -> datetime | None:
    if isinstance(raw, bool):
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _from_ms(raw) -> datetime | None:
    if isinstance(raw, bool):
        return None
    try:
        ms = int(raw)
        return (datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
                + timedelta(milliseconds=ms % 1000))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def listed_events(body: dict, *, now: datetime, window_hours: float) -> list[Listing]:
    """The listed games in Hills' competitions kicking off in (now, now + window],
    in kick-off order. A game listed twice (the two Championship names; the same
    teams and kick-off) is kept once: the row naming William Hill, else the
    smaller event id, so the choice never follows the vendor's order."""
    end = now + timedelta(hours=window_hours)
    kept: dict[tuple[str, str, datetime], Listing] = {}
    for item in _items(body):
        league = item.get("league")
        if league not in HILLS_LEAGUES:
            continue
        start = _start(item.get("start_time"))
        home, away, eid = item.get("home_team"), item.get("away_team"), item.get("event_id")
        if start is None or not home or not away or eid in (None, ""):
            continue
        if not (now < start <= end):
            continue
        key = (str(home), str(away), start)
        books = item.get("bookmakers")
        listing = Listing(
            event_id=str(eid), home=str(home), away=str(away), start=start,
            league=league,
            books=frozenset(str(b) for b in books) if isinstance(books, list) else frozenset(),
        )
        if key not in kept or _preferred(listing) < _preferred(kept[key]):
            kept[key] = listing
    return sorted(kept.values(), key=lambda l: l.start)


def _preferred(listing: Listing) -> tuple:
    """Sort key among duplicate rows: William Hill first, then the smaller
    event id (numerically when it is a number, as the vendor's are)."""
    eid = listing.event_id
    return (WILLIAM_HILL not in listing.books, not eid.isdigit(),
            int(eid) if eid.isdigit() else 0, eid)


def fixture_of(listing: Listing) -> dict:
    """The fixture shape `/hills/fixtures` and the target read. The vendor
    sends no round name, so `round` is None."""
    return {"id": OAN_PREFIX + listing.event_id, "home": listing.home,
            "away": listing.away, "start": listing.start,
            "tournament_id": HILLS_LEAGUES.get(listing.league), "round": None}


def _half_or_whole(point: float) -> bool:
    """False for quarter lines (x.25, x.75), which the pipeline does not price."""
    return point * 2 == int(point * 2)


def _outcome(item: dict) -> tuple[str, dict] | None:
    """(market_key, outcome) for one snapshot item, or None when it is not a
    line Hills prices."""
    if item.get("period") != "full time" or item.get("is_available") is not True:
        return None
    try:
        price = float(item.get("odds"))
    except (TypeError, ValueError):
        return None
    if not price > 1.0:
        return None
    bet_type = item.get("bet_type")
    metric = str(item.get("metric") or "").lower()
    side = str(item.get("side") or "").lower()
    line = item.get("line")
    if not isinstance(line, str):
        return None
    if bet_type in ("total", "team total"):
        key = (_TOTAL_KEYS.get(metric) if bet_type == "total"
               else _TEAM_KEYS.get((metric, side)))
        m = _TOTAL_LINE_RE.match(line)
        if key is None or m is None:
            return None
        point = float(m.group(2))
        if not _half_or_whole(point):
            return None
        return key, {"name": m.group(1).capitalize(), "point": point, "price": price}
    if bet_type == "handicap":
        key = _HANDICAP_KEYS.get(metric)
        m = _HANDICAP_LINE_RE.match(line)
        if key is None or m is None or side not in ("home", "away"):
            return None
        given = float(m.group(1))
        if not _half_or_whole(given):
            return None
        # Always the HOME side's handicap: the away item's line negated.
        point = given if side == "home" else -given
        return key, {"name": side.capitalize(), "point": point + 0.0, "price": price}
    return None


def parse_snapshot(body: dict) -> SnapshotLines:
    """One snapshot response -> the William Hill and Pinnacle lines Hills
    prices, each book's age, and the vendor's refresh interval."""
    markets: dict[str, dict[str, list[dict]]] = {}
    for item in _items(body):
        book = item.get("bookmaker")
        if book not in _BOOKS:
            continue
        mapped = _outcome(item)
        if mapped is None:
            continue
        key, outcome = mapped
        if key in _HANDICAP_KEYS.values() and book != PINNACLE:
            continue
        markets.setdefault(book, {}).setdefault(key, []).append(outcome)
    body = body if isinstance(body, dict) else {}
    stamps = body.get("bookmaker_as_of_ts_ms")
    as_of = {}
    if isinstance(stamps, dict):
        for book in _BOOKS:
            when = _from_ms(stamps.get(book))
            if when is not None:
                as_of[book] = when
    stale = body.get("stale_bookmakers")
    refresh = body.get("target_refresh_interval_seconds")
    try:
        refresh_seconds = None if isinstance(refresh, bool) else float(refresh)
    except (TypeError, ValueError):
        refresh_seconds = None
    return SnapshotLines(
        markets=markets, as_of=as_of,
        # A list of books when any are stale; the vendor sends a bare 0 when none.
        stale_books=frozenset(str(b) for b in stale) if isinstance(stale, list) else frozenset(),
        refresh_seconds=refresh_seconds,
        # A `next_cursor` is more of the snapshot, never followed: incomplete too.
        complete=bool(body.get("complete", True)) and not body.get("next_cursor"),
        oldest_as_of=_from_ms(body.get("oldest_bookmaker_as_of_ts_ms")),
    )


def to_event(listing: Listing, lines: SnapshotLines) -> dict | None:
    """The counts model's event: William Hill first, then Pinnacle when it has
    lines (raw prices). None when William Hill prices nothing."""
    wh = lines.markets.get(WILLIAM_HILL)
    if not wh:
        return None
    books = [{"key": WILLIAM_HILL,
              "markets": [{"key": k, "outcomes": v} for k, v in wh.items()]}]
    pin = lines.markets.get(PINNACLE)
    if pin:
        books.append({"key": PINNACLE,
                      "markets": [{"key": k, "outcomes": v} for k, v in pin.items()]})
    return {
        "id": OAN_PREFIX + listing.event_id,
        "home_team": listing.home,
        "away_team": listing.away,
        "commence_time": listing.start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sport_key": TOURNAMENT_SPORT_KEYS.get(HILLS_LEAGUES.get(listing.league), "soccer"),
        "fair_source": VENDOR,
        "bookmakers": books,
    }


def priced_at(lines: SnapshotLines) -> datetime | None:
    """The older of William Hill's and Pinnacle's stamps: the board never
    claims fresher prices than its oldest side. Only a book that carried lines
    counts: a stamp behind no price would only make the board look older.
    William Hill carrying lines with no stamp of its own takes the vendor's
    oldest stamp (None when that is missing too), never Pinnacle's fresher
    one, which would pass hour-old prices off as minutes old."""
    if lines.markets.get(WILLIAM_HILL) and WILLIAM_HILL not in lines.as_of:
        return lines.oldest_as_of
    stamps = [lines.as_of[b] for b in _BOOKS if b in lines.as_of and lines.markets.get(b)]
    return min(stamps) if stamps else None


def snapshot_ttl(lines: SnapshotLines) -> float:
    """How long a board is reused: the vendor's own refresh interval, clamped
    to 120-3,600 s (asking sooner than it refreshes buys nothing). An
    incomplete snapshot is reused 120 s only, whatever else would apply."""
    if not lines.complete:
        return TTL_MIN
    if lines.refresh_seconds is None:
        return TTL_MAX
    return min(TTL_MAX, max(TTL_MIN, lines.refresh_seconds))


# --- BB Boost: legs and results -------------------------------------------------

# Market key -> the results' metric and the sides whose counts it adds.
_SETTLE_METRICS: dict[str, tuple[str, tuple[str, ...]]] = {
    CORNERS: ("corners", ("home", "away")), CARDS: ("cards", ("home", "away")),
    GOALS: ("standard", ("home", "away")),
    TEAM_CORNERS_HOME: ("corners", ("home",)), TEAM_CORNERS_AWAY: ("corners", ("away",)),
    TEAM_CARDS_HOME: ("cards", ("home",)), TEAM_CARDS_AWAY: ("cards", ("away",)),
}
_SIDES = ("Over", "Under")


def leg_id(key: str, line: float) -> str:
    """A BB Boost leg's market id: "alternate_totals_corners:9.5"."""
    return f"{key}:{float(line)}"


def parse_leg_id(market_id: str) -> tuple[str, float] | None:
    """(key, line) from `leg_id`'s form; None for anything else (an OddsPapi
    market id, a line that is not a number)."""
    if not isinstance(market_id, str):
        return None
    key, sep, raw = market_id.rpartition(":")
    if not sep or not key:
        return None
    try:
        line = float(raw)
    except ValueError:
        return None
    return (key, line) if math.isfinite(line) else None


def legs_of(lines: SnapshotLines, home: str, away: str) -> list[dict]:
    """William Hill's half lines on one board as BB Boost legs, in the shape
    and order of `hills.legs.legs_for`."""
    out: dict[tuple[str, str], dict] = {}
    for key, outcomes in (lines.markets.get(WILLIAM_HILL) or {}).items():
        if key not in OFFERED:
            continue
        for o in outcomes:
            side, line = o.get("name"), o.get("point")
            if side not in _SIDES or line is None or not is_half_line(line):
                continue
            mid = leg_id(key, line)
            out.setdefault((mid, side), {
                "market_id": mid, "outcome_id": side, "key": key,
                "market": market_name(key), "side": side, "line": line,
                "label": _label(key, side, line, home, away)})
    return sorted(out.values(),
                  key=lambda leg: (OFFERED.index(leg["key"]), leg["line"], leg["side"]))


def _count(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return None


def results_counts(body: dict) -> dict[tuple[str, str], int] | None:
    """{(metric, side): score} from a /results body: full-time, final, team
    records only. None until the vendor has the result (`status: available`)
    and the match is finished in 90 minutes (`status_detail` "FT"), and None
    unless the vendor matched the result to its own event (`identity_match`
    "matched"): a fuzzy or unmatched identity may be another game's counts."""
    if not isinstance(body, dict) or body.get("status") != "available":
        return None
    result = body.get("result")
    if not isinstance(result, dict) or result.get("event_status") != "finished":
        return None
    if result.get("identity_match") != "matched":
        return None
    # William Hill's lines are 90 minutes, and the vendor does not say whether
    # "full time" counts extra time: only a plain "FT" is settled. "AET",
    # "Pen", a blank or a missing detail waits for a hand result, so a wrong
    # result is never written.
    detail = result.get("status_detail")
    if not isinstance(detail, str) or detail.strip().upper() != "FT":
        return None
    out: dict[tuple[str, str], int] = {}
    for rec in result.get("score_records") or []:
        if not isinstance(rec, dict) or rec.get("player_name"):
            continue
        if rec.get("period") != "full time" or rec.get("status") != "final":
            continue
        side = str(rec.get("side") or "").lower()
        score = _count(rec.get("score"))
        if side not in ("home", "away") or score is None:
            continue
        key = (str(rec.get("metric") or "").lower(), side)
        # Two final records that disagree: neither is trusted, so the whole
        # result waits for a hand one. An identical repeat is harmless.
        if out.get(key, score) != score:
            return None
        out[key] = score
    return out


def leg_result(counts: dict, market_id: str, outcome_id: str) -> str | None:
    """"won" or "lost" for one leg off the final counts; None when it cannot
    be read (an unknown key, a whole line, a side or record missing)."""
    parsed = parse_leg_id(market_id)
    if not counts or parsed is None or outcome_id not in _SIDES:
        return None
    key, line = parsed
    spec = _SETTLE_METRICS.get(key)
    if spec is None or not is_half_line(line):
        return None
    metric, sides = spec
    if any((metric, side) not in counts for side in sides):
        return None
    over = sum(counts[(metric, side)] for side in sides) > line
    return "won" if over == (outcome_id == "Over") else "lost"


# --- the source ---------------------------------------------------------------

LISTING_TTL = 600        # s the /events listing is reused (a failed one is held as long)
FAILURE_HOLD = 600       # s an event whose snapshot failed is not asked again
LISTING_PAGES = 3        # /events pages followed at most
RATE_HOLD = 60           # s every fetch waits after a 429 naming no usable Retry-After
RATE_HOLD_MAX = 600      # s at most, whatever Retry-After says
FLOOR_REACHED = ("daily credit budget spent for the board: the last {floor} are kept "
                 "for legs and settling")
SNAPSHOT_PARAMS = {"bookmakers": f"{WILLIAM_HILL},{PINNACLE}", "price_fields": "odds",
                   "types": "total,team total,handicap", "periods": "full time"}


def rate_hold_seconds(retry_after) -> float:
    """How long a 429 holds every fetch: its Retry-After, capped at
    RATE_HOLD_MAX; RATE_HOLD when it is missing, not finite or negative."""
    try:
        wait = float(retry_after)
    except (TypeError, ValueError):
        return RATE_HOLD
    if not math.isfinite(wait) or wait < 0:
        return RATE_HOLD
    return min(wait, RATE_HOLD_MAX)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _thread_runner(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="hills-oan", daemon=True).start()


class OddsApiNetHillsSource:
    """Hills' William Hill board and Pinnacle fair side from odds-api.net, at
    `app.state.hills_client` (spec "The seam", "Budget, caching and freshness").

    `get_events()` refreshes the listing (one /events call, reused
    LISTING_TTL), serves the boards it holds and, when any listed William Hill
    game is due, hands ONE pass to `runner` (a daemon thread by default; never
    two at once). A cold board is 40-60 snapshots at ~1.4 s each, too slow for
    a page request, so only the first request after a boot waits for the pass
    (up to `first_wait`); later ones serve what is held and the pass catches up
    behind them.

    A WARMISH BOARD (Callum, 11 Nov 2026): `warm()`, the scheduler's
    `hills_warm` job, runs the same pass on the job's thread at a slower pace
    (`oddsapinet_warm_*`), so the page opens on boards that are hours old at
    worst rather than none. It and a page pass share `_refreshing`, so they
    never run together, and it fetches nothing below the credit reserve.

    Due: listed, naming williamhill, not kicked off, with no board or a board
    older than the vendor's own refresh interval (`snapshot_ttl`), and not
    held after a failure. Nearest kick-off first, `max_per_pass` a pass. A
    failed snapshot is held FAILURE_HOLD (billed, so not re-sent each page
    load); a spent budget or a 429 ends the pass and holds no game, and a 429
    holds EVERY fetch (listing, pass, warm, target, results) for its
    Retry-After (`rate_hold_seconds`), serving the boards held. The page's
    pass, the listing and the warm job send nothing once `floor` credits or
    fewer are left today; only legs and results spend below it. Every pass
    drops boards whose game is no longer listed, so the boards are bounded by
    the window. Only parsed lines are kept, never a response."""

    vendor = VENDOR
    book = WILLIAM_HILL
    label = "William Hill"

    def __init__(self, client, *, window_hours: float | None = None, max_per_pass: int = 60,
                 first_wait: float = 15.0,
                 near_ttl: float | None = None, floor: int | None = None,
                 runner: Callable[[Callable[[], None]], None] | None = None,
                 now: Callable[[], datetime] | None = None,
                 monotonic: Callable[[], float] = time.monotonic):
        from app.config import settings
        if window_hours is None:
            window_hours = settings.oddsapinet_window_hours
        self._client = client
        self.window_hours = window_hours
        # The page's reuse of a near game's board, and the credits it and the
        # warm job leave for legs and settling (final review, Important 2).
        self.near_ttl = (settings.oddsapinet_page_near_ttl_seconds if near_ttl is None
                         else near_ttl)
        self.floor = settings.oddsapinet_settle_floor if floor is None else floor
        self.max_per_pass = max_per_pass
        self.first_wait = first_wait
        self._runner = runner or _thread_runner
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._mono = monotonic
        self._lock = threading.Lock()            # guards every field below
        self._listing_lock = threading.Lock()    # one /events fetch at a time
        self._refreshing = False
        self._passes_done = 0
        self._pass_done = threading.Event()
        self._listings: list[Listing] = []
        self._listed_at: float | None = None     # mono of the last listing ATTEMPT
        self._listing_error: str | None = None
        self._fetched: dict[str, float] = {}     # board key -> mono it was fetched
        self._held: dict[str, float] = {}        # board key -> mono its hold ends
        self._pass_errors: list[str] = []
        self._pass_failed = 0
        self._rate_held_until: float | None = None   # mono a 429's hold ends
        self.fixtures: list[dict] = []
        self.boards: dict[str, SnapshotLines] = {}
        self.last_sweep: dict = {}
        self.priced_at: datetime | None = None

    # -- reading ---------------------------------------------------------------

    def get_events(self) -> list[dict]:
        self._refresh_listing()
        # Read outside `_lock` (the client's own lock is held across a
        # request); it also rolls the client's day at 00:00 UTC.
        page_may_spend = self._page_may_spend()
        start = False
        with self._lock:
            # Pruned here too, not only by a pass: a listing that dropped a
            # game needs no pass for its board to go.
            self._prune()
            start = bool(page_may_spend and not self._rate_held()
                         and self._due() and not self._refreshing)
            self._summarise()
            # Set only once nothing here can raise, so a failure above never
            # leaves the source "busy" with no pass to clear it.
            if start:
                self._refreshing = True
                self._pass_done.clear()
        if start:
            try:
                self._runner(self._pass)
            except Exception as exc:     # a thread that would not start
                logger.error("hills (odds-api.net): pass not started: %s", exc)
                with self._lock:
                    self._refreshing = False
                    self._pass_done.set()
        with self._lock:
            waiting = self._refreshing and self._passes_done == 0
        if waiting:
            self._pass_done.wait(self.first_wait)
        with self._lock:
            events = []
            for listing in self._listed():
                board = self.boards.get(OAN_PREFIX + listing.event_id)
                event = to_event(listing, board) if board is not None else None
                if event is not None:
                    events.append(event)
            return events

    def priced_at_for(self, event_id: str) -> datetime | None:
        """When the prices behind one match were fetched (the older of its two
        books); None unless William Hill carried lines on it."""
        with self._lock:
            board = self.boards.get(event_id)
            if board is None or not board.markets.get(WILLIAM_HILL):
                return None
            return priced_at(board)

    # -- BB Boost --------------------------------------------------------------

    def owns_fixture(self, fixture_id: str) -> bool:
        """True for an odds-api.net fixture (`oan:<id>`): one this source settles."""
        return str(fixture_id).startswith(OAN_PREFIX)

    def hills_legs(self, fixture_id: str, home: str, away: str) -> list[dict]:
        """William Hill's lines on one match as BB Boost legs. The target is
        picked to be bet on, so its board is fetched in the request when it
        is missing or due (one snapshot); a failure is held like the pass's.
        [] for a fixture that is not odds-api.net's, with no request."""
        if not self.owns_fixture(fixture_id):
            return []
        board = self._target_board(str(fixture_id))
        return legs_of(board, home, away) if board is not None else []

    def _target_board(self, key: str) -> SnapshotLines | None:
        with self._lock:
            board = self.boards.get(key)
            if self._fresh(key) or self._held_now(key) or self._rate_held():
                return board
        event_id = key[len(OAN_PREFIX):]
        try:
            lines = parse_snapshot(self._client.get(f"/events/{event_id}/odds/snapshot",
                                                    **SNAPSHOT_PARAMS))
        except (BudgetSpent, RateLimited) as exc:
            self._note_rate_limit(exc)
            logger.warning("hills (odds-api.net): target board %s not fetched: %s", event_id, exc)
            return board
        except Exception as exc:
            logger.warning("hills (odds-api.net): target board %s failed: %s", event_id, exc)
            with self._lock:
                self._held[key] = self._mono() + FAILURE_HOLD
            return board
        with self._lock:
            self.boards[key] = lines
            self._fetched[key] = self._mono()
            self._held.pop(key, None)
        return lines

    def leg_results(self, fixture_id: str,
                    legs: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
        """{(market_id, outcome_id): won | lost} for the legs the match's final
        counts decide: one /results call. {} until the result is in, and for
        a fixture that is not odds-api.net's (no request). Raises on a failed
        call (settle.check logs it and waits for the next window)."""
        if not self.owns_fixture(fixture_id):
            return {}
        with self._lock:
            if self._rate_held():
                raise RateLimited("odds-api.net: held after a 429; results not asked")
        try:
            body = self._client.get(f"/events/{str(fixture_id)[len(OAN_PREFIX):]}/results")
        except RateLimited as exc:
            self._note_rate_limit(exc)
            raise
        counts = results_counts(body)
        if counts is None:
            return {}
        out = {}
        for mid, oid in legs:
            got = leg_result(counts, mid, oid)
            if got is not None:
                out[(mid, oid)] = got
        return out

    # -- the listing -----------------------------------------------------------

    def _page_may_spend(self) -> bool:
        """True while more than `floor` credits are left today: the page's pass,
        the listing and the warm job send nothing below that."""
        return self._client.budget_left() > self.floor

    def _refresh_listing(self) -> None:
        with self._listing_lock:
            mono = self._mono()
            if self._listed_at is not None and mono - self._listed_at < LISTING_TTL:
                return
            if not self._page_may_spend():
                return                   # the last credits are for legs and settling
            with self._lock:
                if self._rate_held():
                    return               # asked again once the 429's hold ends
            self._listed_at = mono
            now = self._now()
            params = {"sport": "soccer", "start_from": int(now.timestamp()),
                      "start_to": int(now.timestamp()) + int(self.window_hours * 3600),
                      "limit": 1000}
            try:
                items: list = []
                cursor = None
                for _ in range(LISTING_PAGES):
                    body = self._client.get("/events", **params,
                                            **({"cursor": cursor} if cursor else {}))
                    body = body if isinstance(body, dict) else {}
                    page = body.get("items")
                    items.extend(page if isinstance(page, list) else [])
                    cursor = body.get("next_cursor")
                    if not cursor:
                        break
                listings = listed_events({"items": items}, now=now,
                                         window_hours=self.window_hours)
            except Exception as exc:
                # Keep the last listing; the failure is held like a good one,
                # since a failed call is billed too.
                self._note_rate_limit(exc)
                logger.warning("hills (odds-api.net): listing failed: %s", exc)
                with self._lock:
                    self._listing_error = f"listing: {exc}"
                return
            with self._lock:
                self._listings = listings
                self._listing_error = None

    def _listed(self) -> list[Listing]:
        """The listing, less the games that have kicked off since it was read."""
        now = self._now()
        return [l for l in self._listings if now < l.start]

    def _wh(self) -> list[Listing]:
        return [l for l in self._listed() if WILLIAM_HILL in l.books]

    def _fresh(self, key: str) -> bool:
        board = self.boards.get(key)
        return board is not None and self._mono() - self._fetched[key] < snapshot_ttl(board)

    def _held_now(self, key: str) -> bool:
        return self._held.get(key, float("-inf")) > self._mono()

    def _rate_held(self) -> bool:
        """True while a 429's hold runs: nothing is sent, so nothing is billed.
        Called under `_lock`."""
        return self._rate_held_until is not None and self._mono() < self._rate_held_until

    def _note_rate_limit(self, exc: Exception) -> None:
        """A 429 holds every fetch (page pass, listing, warm, target, results)
        for its Retry-After (`rate_hold_seconds`). Anything else: no-op."""
        if not isinstance(exc, RateLimited):
            return
        with self._lock:
            until = self._mono() + rate_hold_seconds(exc.retry_after)
            self._rate_held_until = max(until, self._rate_held_until or until)

    def _page_fresh(self, listing: Listing) -> bool:
        """The page's reuse rule: the vendor's refresh interval (`snapshot_ttl`),
        but at least `near_ttl` on a game inside `oddsapinet_warm_near_hours`
        of kick-off, where the vendor's 60 s would cost 30 snapshots an hour.
        An incomplete snapshot keeps `snapshot_ttl`'s 120 s."""
        from app.config import settings
        key = OAN_PREFIX + listing.event_id
        board = self.boards.get(key)
        if board is None:
            return False
        ttl = snapshot_ttl(board)
        if board.complete and (listing.start - self._now()
                               <= timedelta(hours=settings.oddsapinet_warm_near_hours)):
            ttl = max(ttl, self.near_ttl)
        return self._mono() - self._fetched[key] < ttl

    def _due(self) -> list[Listing]:
        """William Hill games with no fresh board and no hold, nearest first."""
        return [l for l in self._wh()
                if not self._page_fresh(l)
                and not self._held_now(OAN_PREFIX + l.event_id)]

    # -- the warmish board ------------------------------------------------------

    def warm(self) -> dict:
        """One slow pre-load pass, on the caller's (the scheduler's) thread.
        Refreshes the listing as `get_events` does, then fetches the games
        `_warm_due` names, nearest kick-off first, at most `max_per_pass` and
        never into the last `oddsapinet_warm_reserve` credits of the day.
        Skipped (no request at all) below the reserve, or while a page pass
        runs. {"due", "fetched", "skipped": None | "reserve" | "busy"}."""
        from app.config import settings
        # The warm's reserve sits above the settling floor; never below it.
        reserve = max(settings.oddsapinet_warm_reserve, self.floor)

        def skipped(reason: str) -> dict:
            return {"due": 0, "fetched": 0, "skipped": reason}

        with self._lock:
            if self._refreshing:
                return skipped("busy")
            if self._rate_held():
                return skipped("held")
        if self._client.budget_left() < reserve:
            return skipped("reserve")
        self._refresh_listing()
        room = self._client.budget_left() - reserve      # the listing may have spent some
        with self._lock:
            self._prune()
            if self._refreshing:                         # a page pass started meanwhile
                self._summarise()
                return skipped("busy")
            if room <= 0:
                self._summarise()
                return skipped("reserve")
            due = self._warm_due()
            if not due:
                self._summarise()
                return {"due": len(due), "fetched": 0, "skipped": None}
            self._refreshing = True
            self._pass_done.clear()
        fetched = self._pass(due[:min(self.max_per_pass, room)], kind="warm")
        return {"due": len(due), "fetched": fetched, "skipped": None}

    def _warm_due(self) -> list[Listing]:
        """William Hill games the warm job fetches, nearest first: no board, or
        a board over `oddsapinet_warm_far_s` old on a game more than
        `oddsapinet_warm_near_hours` away, over `oddsapinet_warm_near_s`
        inside that, over TTL_MIN on an incomplete snapshot. Held games are
        skipped. Called under `_lock`."""
        from app.config import settings
        now, mono = self._now(), self._mono()
        near = timedelta(hours=settings.oddsapinet_warm_near_hours)
        out = []
        for listing in self._wh():
            key = OAN_PREFIX + listing.event_id
            if self._held_now(key):
                continue
            if key in self.boards:
                limit = (TTL_MIN if not self.boards[key].complete
                         else settings.oddsapinet_warm_far_s if listing.start - now > near
                         else settings.oddsapinet_warm_near_s)
                if mono - self._fetched[key] <= limit:
                    continue
            out.append(listing)
        return out

    # -- the pass --------------------------------------------------------------

    def _pass(self, due: list[Listing] | None = None, kind: str = "sweep") -> int:
        """Fetch `due` (default: the page's `_due`, capped) and store each
        board; the page's background pass and `warm` alike. The caller has
        set `_refreshing`; this clears it. Returns the snapshots fetched."""
        errors: list[str] = []
        failed = fetched = 0
        sweep: dict = {}
        try:
            if due is None:
                with self._lock:
                    due = self._due()[:self.max_per_pass]
            for listing in due:
                key = OAN_PREFIX + listing.event_id
                if not self._page_may_spend():
                    errors.append(FLOOR_REACHED.format(floor=self.floor))
                    break
                with self._lock:
                    held = self._rate_held()
                if held:
                    errors.append("held after a 429")
                    break
                try:
                    body = self._client.get(f"/events/{listing.event_id}/odds/snapshot",
                                            **SNAPSHOT_PARAMS)
                    lines = parse_snapshot(body)
                except (BudgetSpent, RateLimited) as exc:
                    # Nothing sent past here would be answered: end the pass,
                    # hold no game (they are still pending); a 429 holds
                    # every fetch for its Retry-After.
                    self._note_rate_limit(exc)
                    errors.append(str(exc))
                    failed += 1
                    break
                except Exception as exc:
                    errors.append(f"{listing.event_id}: {exc}")
                    failed += 1
                    with self._lock:
                        self._held[key] = self._mono() + FAILURE_HOLD
                    continue
                if not lines.complete:
                    errors.append(f"{listing.event_id}: snapshot incomplete")
                fetched += 1
                with self._lock:
                    self.boards[key] = lines
                    self._fetched[key] = self._mono()
                    self._held.pop(key, None)
        except Exception as exc:         # never let the thread die unexplained
            logger.exception("hills (odds-api.net): pass failed")
            errors.append(f"pass failed: {exc}")
            failed += 1
        finally:
            try:
                with self._lock:
                    try:
                        self._pass_errors, self._pass_failed = errors, failed
                        self._prune()
                        self._summarise()
                        sweep = dict(self.last_sweep)
                    except Exception as exc:
                        logger.exception("hills (odds-api.net): pass summary failed")
                        errors.append(f"pass summary failed: {exc}")
                    finally:
                        # Whatever failed above, the next pass can start.
                        self._refreshing = False
                        self._passes_done += 1
            finally:
                self._pass_done.set()
        log = logger.info if not errors else logger.warning
        log("hills %s (odds-api.net): %d snapshot(s), %d William Hill game(s) listed, "
            "%d carried, %d with Pinnacle, %d pending, %d error(s), %d request(s) today",
            kind, fetched, sweep.get("fixtures_in_window", 0), sweep.get("carried_paddy_prices", 0),
            sweep.get("carried_pinnacle_prices", 0), sweep.get("pending", 0), len(errors),
            sweep.get("requests_today", 0))
        return fetched

    def _prune(self) -> None:
        """Drop boards and holds for games no longer listed (or kicked off)."""
        keep = {OAN_PREFIX + l.event_id for l in self._listed()}
        for store in (self.boards, self._fetched, self._held):
            for key in [k for k in store if k not in keep]:
                del store[key]

    def _summarise(self) -> None:
        """`fixtures`, `priced_at` and `last_sweep` from what is held now.
        Called under `_lock`; no I/O."""
        listed = self._listed()
        wh = [l for l in listed if WILLIAM_HILL in l.books]
        boards = [self.boards[OAN_PREFIX + l.event_id] for l in wh
                  if OAN_PREFIX + l.event_id in self.boards]
        carried = [b for b in boards if b.markets.get(WILLIAM_HILL)]
        stamps = [t for t in (priced_at(b) for b in carried) if t is not None]
        self.fixtures = [fixture_of(l) for l in listed]
        self.priced_at = min(stamps, default=None)
        errors = ([self._listing_error] if self._listing_error else []) + list(self._pass_errors)
        used = getattr(self._client, "requests_today", None)
        budget = getattr(self._client, "daily_budget", None)
        self.last_sweep = {
            "source": VENDOR,
            "book": WILLIAM_HILL,
            "fair_source": f"{VENDOR}:{PINNACLE}",
            "window_hours": self.window_hours,
            "fixtures_in_window": len(wh),
            "called": len(boards),
            "carried_paddy_prices": len(carried),
            "carried_pinnacle_prices": sum(1 for b in carried if b.markets.get(PINNACLE)),
            "pending": len(self._due()),
            "stale_williamhill": sum(1 for b in boards if WILLIAM_HILL in b.stale_books),
            "failed": self._pass_failed + (1 if self._listing_error else 0),
            "errors": errors,
            "requests_today": used,
            "request_budget": budget,
            "credit_floor": self.floor,
            # The page fetches nothing more today (the client's own count,
            # read without its lock).
            "budget_spent": (isinstance(used, int) and isinstance(budget, int)
                             and budget - used <= self.floor),
            "next_kickoff": _iso(wh[0].start) if wh else None,
        }
