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
import re

from app.cards.oddspapi import (
    CARDS, CARDS_HANDICAP, CORNERS, CORNERS_HANDICAP, GOALS, TEAM_CARDS_AWAY,
    TEAM_CARDS_HOME, TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME, TOURNAMENT_SPORT_KEYS,
    WILLIAM_HILL,
)

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
    in kick-off order. A game listed twice (the two Championship names) is kept
    once, on the same teams and kick-off."""
    end = now + timedelta(hours=window_hours)
    seen: set[tuple[str, str, datetime]] = set()
    out: list[Listing] = []
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
        if key in seen:
            continue
        seen.add(key)
        books = item.get("bookmakers")
        out.append(Listing(
            event_id=str(eid), home=str(home), away=str(away), start=start,
            league=league,
            books=frozenset(str(b) for b in books) if isinstance(books, list) else frozenset(),
        ))
    out.sort(key=lambda l: l.start)
    return out


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
        complete=bool(body.get("complete", True)),
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
    claims fresher prices than its oldest side."""
    stamps = [lines.as_of[b] for b in _BOOKS if b in lines.as_of]
    return min(stamps) if stamps else None


def snapshot_ttl(lines: SnapshotLines) -> float:
    """How long a board is reused: the vendor's own refresh interval, clamped
    to 120-3,600 s (asking sooner than it refreshes buys nothing)."""
    if lines.refresh_seconds is None:
        return TTL_MAX
    return min(TTL_MAX, max(TTL_MIN, lines.refresh_seconds))
