"""Paddy Power corners/cards prices from OddsPapi.

WHY THIS VENDOR. TheOddsApi does not carry Paddy Power's corners or cards. A
probe on a Nations League fixture hours from kickoff (28 Sept 2026), asked by
region with NO bookmaker filter, returned one book with corners (LeoVegas),
none with cards, and no Paddy Power at all. OddsPapi's own Paddy Power feed
carries a family of two-way markets beyond the standard ones, which is where
the corners and cards lines live.

WHAT THIS FILE PRODUCES. TheOddsApi-shaped events — {id, home_team, away_team,
commence_time, sport_key, bookmakers: [{key: "paddypower", markets: [{key:
"alternate_totals_corners", outcomes: [{name: "Over", point: 9.5, price}]}]}]}
— so `merge_pinnacle`, `value.py`, `laddered.py` and the diagnostics consume it
unchanged. Swapping vendor is this file plus one branch in `main.py`.

HOW OddsPapi IS SHAPED (read off the trial responses, Aug–Sept 2026):
  - Ids are SOFASCORE's. Tournaments (LaLiga 8, Champions League 7, EFL Cup
    21) and teams (Barcelona 2817, Real Madrid 2829, Atletico 2836) all match
    Sofascore, which is how the default tournament list below was chosen.
  - A market id is ONE market at ONE line. Paddy's corner "ladder" is several
    market ids, each a two-way pair — 10803 / 10807 / 10811 priced 1.45/2.55,
    1.25/3.60, 1.11/5.50, which is one totals market stepping up its line. The
    line and the name live in the /markets CATALOGUE, not in the odds payload,
    so the catalogue is read to know what each id is.
  - Odds are `bookmakerOdds.<book>.markets.<marketId>.outcomes.<outcomeId>
    .players."0".price`, and fixtures carry participant IDS, not names.

WHAT IS NOT YET VERIFIED — the catalogue's and the participants list's field
names. oddspapi.io is egress-blocked from where this was written, so both are
parsed tolerantly (several plausible spellings), and GET /api/cards/oddspapi-
check shows exactly what the live catalogue says the corner/card markets are.
Read that once before trusting a price.

COST. OddsPapi bills per REQUEST, and one odds-by-tournaments request returns
every fixture in those tournaments with its full Paddy board — so a sweep is a
handful of requests however many fixtures are on, not one call per fixture as
TheOddsApi's per-event billing forced. Cached per chunk for a time set by how
soon its next fixture kicks off (`oddspapi_ttl_tiers`: 2 min near kickoff,
hourly when nothing is close), and capped per day by
`oddspapi_daily_request_budget`.
"""
from datetime import datetime, timedelta, timezone
import logging
import re
import threading
import time

import httpx

from ..config import settings
from ..ttl_cache import MISS, TTLCache

logger = logging.getLogger("uvicorn.error")

BASE_URL = "https://api.oddspapi.io/v4"
SOCCER = 10
PADDY_POWER = "paddypower"
PINNACLE = "pinnacle"
CORNERS = "alternate_totals_corners"
CARDS = "alternate_totals_cards"
# A SINGLE team's corners/cards — OddsPapi "Team 1/2" (full time). Team 1 is the
# home side (participant1), Team 2 the away. Kept apart from the match totals so
# each is priced against its own Pinnacle side and labelled with the team.
TEAM_CORNERS_HOME = "team_corners_home"
TEAM_CORNERS_AWAY = "team_corners_away"
TEAM_CARDS_HOME = "team_cards_home"
TEAM_CARDS_AWAY = "team_cards_away"
# marketName -> key, for the full-time team markets (the only ones priced).
_TEAM_MARKET_KEYS = {
    "Corners - Over Under Team 1": TEAM_CORNERS_HOME,
    "Corners - Over Under Team 2": TEAM_CORNERS_AWAY,
    "Bookings - Over Under Team 1": TEAM_CARDS_HOME,
    "Bookings - Over Under Team 2": TEAM_CARDS_AWAY,
}

# Sofascore tournament id -> the TheOddsApi-style key the counts model keys its
# per-league dispersion priors by. A tournament missing here still prices; it
# takes the default prior, which only matters on a sparse Pinnacle ladder.
TOURNAMENT_SPORT_KEYS = {
    17: "soccer_epl", 18: "soccer_efl_champ", 24: "soccer_england_league1",
    8: "soccer_spain_la_liga", 23: "soccer_italy_serie_a",
    35: "soccer_germany_bundesliga", 34: "soccer_france_ligue_one",
    238: "soccer_portugal_primeira_liga", 7: "soccer_uefa_champs_league",
    679: "soccer_uefa_europa_league", 23755: "soccer_uefa_nations_league",
    11: "soccer_fifa_world_cup_qualifiers_europe",
    27: "soccer_uefa_euro_qualification",
}

# OddsPapi rate-limits each endpoint: back-to-back requests are refused with a
# 429 saying "wait 0.80 seconds" (live sweep, 28 Sept 2026 — it cost a whole
# chunk of five competitions, Champions League included). Requests are spaced
# by this much, and a 429 is retried ONCE after the wait it names.
MIN_REQUEST_GAP = 1.1
_WAIT_RE = re.compile(r"wait\s+(\d+(?:\.\d+)?)\s*s", re.I)
# A 429 is ALSO what an exhausted monthly quota returns (docs: code
# REQUEST_LIMIT_EXCEEDED). That one must never be retried.
_QUOTA_RE = re.compile(r"REQUEST_LIMIT_EXCEEDED|request limit exceeded", re.I)
QUOTA_RECHECK_SECONDS = 1800
# A failed odds request is not re-sent for FAILED_HOLD (10 min), or for
# FAILED_HOLD_MAX (6 h) when the plan does not cover the book at all —
# re-asking cannot change that, and every attempt is billed.
FAILED_HOLD = 600
FAILED_HOLD_MAX = 6 * 3600
_RESTRICTED_RE = re.compile(r"RESTRICTED_ACCESS|Restricted bookmaker", re.I)

_CORNER_RE = re.compile(r"\bcorners?\b", re.I)
_CARD_RE = re.compile(r"\b(?:cards?|bookings?)\b", re.I)
# A full-match TOTAL is what Pinnacle's ladder prices. Halves, one team's
# count, handicaps and races are different bets, and pairing one with a match
# total would value it against the wrong thing.
_NOT_MATCH_TOTAL_RE = re.compile(
    r"\b(?:half|1st|2nd|first|second|team|home|away|handicap|asian|race|odd|"
    r"even|player|minute|interval|exact|range|winner|most|last|next)\b", re.I,
)
_LINE_IN_NAME_RE = re.compile(r"(\d+(?:\.\d+)?)")

# The other match-total markets the check reports on (not priced yet):
# goalkeeper SAVES and match GOALS. Any other counted stat is excluded first,
# so "Goal Kicks" can never pass as goals or "Shots on Target" as anything.
SAVES = "totals_saves"
GOALS = "totals_goals"
_SAVES_RE = re.compile(r"\bsaves?\b", re.I)
_GOALS_RE = re.compile(r"\bgoals?\b", re.I)
_OTHER_STAT_RE = re.compile(
    r"\b(?:shots?|fouls?|offsides?|throw[- ]?ins?|tackles?|passes|crosses|"
    r"free[- ]?kicks?|goal[- ]?kicks?|penalt\w*|points?|sets?|games?)\b", re.I,
)
# A bare "Over/Under", "Total" or "Totals" market with no stat named is the
# match goals line — the trial catalogue's "Over Under" at 2.5 is one.
_BARE_TOTAL_RE = re.compile(r"^\s*(?:totals?|over\s*/?\s*under|o/u)\b", re.I)


def _stat_of(name: str) -> str | None:
    """Which counted stat a market name totals, or None for anything else."""
    if _OTHER_STAT_RE.search(name):
        return None
    if _SAVES_RE.search(name):
        return SAVES
    if _CORNER_RE.search(name):
        return CORNERS
    if _CARD_RE.search(name):
        return CARDS
    if _GOALS_RE.search(name) or _BARE_TOTAL_RE.search(name):
        return GOALS
    return None
_FULL_TIME = {"", "0", "ft", "fulltime", "full time", "full-time", "match", "regular"}


def _first(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def _as_list(data) -> list:
    """The API may wrap a list, or key it by id. Accept all three."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        inner = _first(data, "data", "markets", "participants", "tournaments", "items")
        if isinstance(inner, (list, dict)):
            return _as_list(inner)
        # {id: {...}} or {id: "name"}
        return [
            ({**v, "_id": k} if isinstance(v, dict) else {"_id": k, "name": v})
            for k, v in data.items()
        ]
    return []


def corner_card_markets(catalogue) -> dict[str, tuple[str, float, dict[str, str]]]:
    """{marketId: (market_key, line, {outcomeId: "Over"|"Under"})} for every
    full-match corners or cards over/under market in the catalogue."""
    return match_total_markets(catalogue, kinds=(CORNERS, CARDS))


def match_total_markets(catalogue, kinds=(CORNERS, CARDS, SAVES, GOALS)
                        ) -> dict[str, tuple[str, float, dict[str, str]]]:
    """As corner_card_markets, for any of `kinds`. The sweep takes all four:
    corners, cards and goals priced off Pinnacle's ladder, saves off Bookie
    Bashing (cards/saves.py)."""
    out = {}
    for m in _as_list(catalogue):
        name = str(_first(m, "marketName", "name", "marketType", "type") or "")
        key = _stat_of(name)
        if key not in kinds:
            continue
        if _NOT_MATCH_TOTAL_RE.search(name):
            continue
        sport = _first(m, "sportId", "sport_id")
        if sport is not None and str(sport) != str(SOCCER):
            continue
        period = str(_first(m, "period", "periodName") or "").strip().lower()
        if period not in _FULL_TIME:
            continue
        line = _first(m, "handicap", "line", "value", "points", "total")
        try:
            line = float(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        if line is None:
            found = _LINE_IN_NAME_RE.findall(name)
            line = float(found[-1]) if found else None
        if line is None:
            continue
        sides: dict[str, str] = {}
        for o in _as_list(_first(m, "outcomes") or []):
            oid = _first(o, "outcomeId", "id", "_id")
            oname = str(_first(o, "outcomeName", "name") or "").lower()
            if oid is None:
                continue
            if "over" in oname:
                sides[str(oid)] = "Over"
            elif "under" in oname:
                sides[str(oid)] = "Under"
        mid = _first(m, "marketId", "id", "_id")
        if mid is not None and len(sides) == 2:
            out[str(mid)] = (key, line, sides)
    return out


def team_markets(catalogue) -> dict[str, tuple[str, float, dict[str, str]]]:
    """A single team's corners/cards (OddsPapi "Team 1/2", full time), keyed like
    match_total_markets so _book_markets/to_events carry them the same way — but
    under their own team key, so each prices against the team's Pinnacle side and
    labels with the team. Team 1 = home (participant1), Team 2 = away."""
    out = {}
    for m in _as_list(catalogue):
        key = _TEAM_MARKET_KEYS.get(str(_first(m, "marketName", "name") or "").strip())
        if key is None:
            continue
        sport = _first(m, "sportId", "sport_id")
        if sport is not None and str(sport) != str(SOCCER):
            continue
        if str(_first(m, "period", "periodName") or "").strip().lower() not in _FULL_TIME:
            continue
        line = _first(m, "handicap", "line", "value", "points", "total")
        try:
            line = float(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        if line is None:
            continue
        sides = {}
        for o in _as_list(_first(m, "outcomes") or []):
            oid = _first(o, "outcomeId", "id", "_id")
            oname = str(_first(o, "outcomeName", "name") or "").lower()
            if oid is None:
                continue
            if "over" in oname:
                sides[str(oid)] = "Over"
            elif "under" in oname:
                sides[str(oid)] = "Under"
        mid = _first(m, "marketId", "id", "_id")
        if mid is not None and len(sides) == 2:
            out[str(mid)] = (key, line, sides)
    return out


# The coupon score-grid's Pinnacle inputs: match result + goal totals + team
# goal totals. marketType -> the bettingiscool row "market" the grid expects.
_COUPON_MARKET_TYPES = {
    "1x2": "moneyline",
    "totals": "totals",
    "teamtotals-team1": "home_totals",
    "teamtotals-team2": "away_totals",
}
_MONEYLINE_ROLE = {"1": 1, "x": 0, "2": 2, "home": 1, "draw": 0, "away": 2}


def coupon_markets(catalogue) -> dict[str, tuple[str, float | None, dict[str, int]]]:
    """{marketId: (market, line, sides)} for the coupon grid's inputs, where
    `market` is moneyline/totals/home_totals/away_totals and `sides` maps an
    outcomeId to the row index the grid reads (moneyline 1=home 0=draw 2=away;
    over=1 under=0) — so _odds can emit rows in the format _grid_constraints
    expects, identical to bettingiscool's."""
    out = {}
    for m in _as_list(catalogue):
        market = _COUPON_MARKET_TYPES.get(str(_first(m, "marketType", "type") or "").lower())
        if market is None:
            continue
        if str(_first(m, "sportId", "sport_id") or SOCCER) != str(SOCCER):
            continue
        if str(_first(m, "period", "periodName") or "").strip().lower() not in _FULL_TIME:
            continue
        line = _first(m, "handicap", "line", "value", "points", "total")
        try:
            line = float(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        if market != "moneyline" and line is None:
            continue
        sides = {}
        for o in _as_list(_first(m, "outcomes") or []):
            oid = _first(o, "outcomeId", "id", "_id")
            name = str(_first(o, "outcomeName", "name") or "").strip().lower()
            if oid is None:
                continue
            role = (_MONEYLINE_ROLE.get(name) if market == "moneyline"
                    else 1 if "over" in name else 0 if "under" in name else None)
            if role is not None:
                sides[str(oid)] = role
        mid = _first(m, "marketId", "id", "_id")
        if mid is not None and len(sides) == (3 if market == "moneyline" else 2):
            out[str(mid)] = (market, line, sides)
    return out


def participant_names(data) -> dict[str, str]:
    out = {}
    for p in _as_list(data):
        pid = _first(p, "participantId", "id", "_id")
        name = _first(p, "participantName", "name", "shortName")
        if pid is not None and name:
            out[str(pid)] = str(name)
    return out


def _price(outcome: dict) -> float | None:
    """The main price of one outcome, or None if suspended or unreadable."""
    players = (outcome or {}).get("players") or {}
    entry = players.get("0") or next(iter(players.values()), None) if players else outcome
    if not isinstance(entry, dict) or entry.get("active") is False:
        return None
    try:
        price = float(entry.get("price"))
    except (TypeError, ValueError):
        return None
    return price if price > 1.0 else None


def _parse_start(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _book_markets(fx: dict, bookmaker: str, markets) -> dict[str, list[dict]]:
    """{market_key: outcomes} for one book's corners/cards lines on a fixture."""
    odds = fx.get("bookmakerOdds") or {}
    # A plan can grant a book under a variant slug ("pinnacle+30", the
    # delayed feed); the response may key it either way.
    book = odds.get(bookmaker) or odds.get(bookmaker.split("+")[0]) or {}
    by_key: dict[str, list[dict]] = {}
    for mid, market in (book.get("markets") or {}).items():
        known = markets.get(str(mid))
        if known is None or market.get("marketActive") is False:
            continue
        key, line, sides = known
        for oid, outcome in (market.get("outcomes") or {}).items():
            side = sides.get(str(oid))
            price = _price(outcome)
            if side and price:
                by_key.setdefault(key, []).append(
                    {"name": side, "point": line, "price": price}
                )
    return by_key


def merge_books(*fixture_lists) -> list[dict]:
    """Join per-bookmaker responses on OddsPapi's own fixture id.

    Each odds request names ONE bookmaker, so Paddy and Pinnacle arrive as two
    lists of the same fixtures. They share the fixture id, so the join is
    exact — no team-name matching, which is what the bettingiscool fair side
    needed and what could pair a fixture with the wrong one."""
    out: dict[str, dict] = {}
    for fixtures in fixture_lists:
        for fx in _as_list(fixtures):
            fid = str(_first(fx, "fixtureId", "id"))
            have = out.get(fid)
            if have is None:
                out[fid] = {**fx, "bookmakerOdds": dict(fx.get("bookmakerOdds") or {})}
            else:
                have["bookmakerOdds"].update(fx.get("bookmakerOdds") or {})
    return list(out.values())


def to_events(fixtures, markets, names, *, now=None, window_hours=None,
              fair_book: str | None = None) -> tuple[list[dict], dict]:
    """OddsPapi fixtures -> TheOddsApi-shaped events carrying Paddy's corners/cards.

    Returns (events, counts). Only fixtures still to kick off inside the window
    are kept, and only ones where Paddy prices at least one corners/cards line.
    With `fair_book` (Pinnacle), that book's lines on the SAME fixture are
    attached as a second bookmaker, RAW — value.py de-vigs.
    """
    now = now or datetime.now(timezone.utc)
    window = timedelta(hours=window_hours if window_hours is not None
                       else settings.oddspapi_window_hours)
    counts = {"fixtures_listed": 0, "fixtures_in_window": 0,
              "carried_paddy_prices": 0, "carried_pinnacle_prices": 0, "unnamed": 0}
    events = []
    for fx in _as_list(fixtures):
        counts["fixtures_listed"] += 1
        start = _parse_start(_first(fx, "startTime", "start_time", "commence_time"))
        if start is None or not (timedelta(0) < start - now <= window):
            continue
        counts["fixtures_in_window"] += 1
        by_key = _book_markets(fx, PADDY_POWER, markets)
        if not by_key:
            continue
        home = _first(fx, "participant1Name") or names.get(str(fx.get("participant1Id")))
        away = _first(fx, "participant2Name") or names.get(str(fx.get("participant2Id")))
        if not home or not away:
            # A row with no teams on it cannot be bet. Counted, not guessed.
            counts["unnamed"] += 1
            continue
        counts["carried_paddy_prices"] += 1
        books = [{
            "key": PADDY_POWER,
            "markets": [{"key": k, "outcomes": v} for k, v in by_key.items()],
        }]
        if fair_book:
            fair = _book_markets(fx, fair_book, markets)
            if fair:
                counts["carried_pinnacle_prices"] += 1
                books.append({
                    "key": "pinnacle",
                    "markets": [{"key": k, "outcomes": v} for k, v in fair.items()],
                })
        events.append({
            "id": str(_first(fx, "fixtureId", "id")),
            "home_team": home,
            "away_team": away,
            "commence_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sport_key": TOURNAMENT_SPORT_KEYS.get(fx.get("tournamentId"), "soccer"),
            # Set when the fair side came from OddsPapi, so merge_pinnacle does
            # not go looking for a second one on bettingiscool.
            "fair_source": "oddspapi" if fair_book else None,
            "bookmakers": books,
        })
    return events, counts


def parse_ttl_tiers(spec: str) -> list[tuple[float, float]]:
    """"2700:120,inf:3600" -> [(2700, 120), (inf, 3600)], tightest first."""
    tiers = []
    for part in (spec or "").split(","):
        if ":" in part:
            within, ttl = part.split(":", 1)
            tiers.append((float(within), float(ttl)))
    tiers.sort()
    return tiers or [(float("inf"), 1800.0)]


def ttl_for(fixtures, tiers, now=None) -> float:
    """The ttl for a chunk whose cached response is `fixtures`: the tier of
    its next fixture still to kick off. Nothing to come -> the loosest."""
    now = now or datetime.now(timezone.utc)
    soonest = None
    for fx in _as_list(fixtures):
        start = _parse_start(_first(fx, "startTime", "start_time", "commence_time"))
        if start is not None and start > now:
            gap = (start - now).total_seconds()
            soonest = gap if soonest is None else min(soonest, gap)
    for within, ttl in tiers:
        if soonest is not None and soonest <= within:
            return ttl
    return tiers[-1][1]


class OddsPapiClient:
    """HTTP + cache. Every request is counted, because OddsPapi bills per request."""

    def __init__(self, api_key: str, *, ttl: float | None = None):
        self._key = api_key
        # A fixed ttl (tests) or the kickoff-tiered one (live).
        self._flat_ttl = ttl
        self._tiers = parse_ttl_tiers(settings.oddspapi_ttl_tiers)
        self._ttl = ttl if ttl is not None else max(t for _, t in self._tiers)
        # Reference data (catalogue, names) changes rarely; odds per TTL.
        self._ref = TTLCache(ttl=24 * 3600, maxsize=16)
        self._odds = TTLCache(ttl=self._ttl, maxsize=64)
        self.requests_today = 0
        self._day = None
        self._last_request = 0.0
        # Set when a call came back REQUEST_LIMIT_EXCEEDED; see _quota_spent.
        self._exhausted_at: float | None = None
        # Failed odds requests, held so the 2-minute sweep does not re-send
        # them: OddsPapi bills 4xx/5xx like a success. {key: (error, hold_s)}.
        self._failed = TTLCache(ttl=FAILED_HOLD_MAX, maxsize=64)
        # ONE request in flight at a time. The 2-minute sweep (scheduler
        # thread) and a page load (request thread) can fetch at the same
        # moment, and OddsPapi answers the second 429 RATE_LIMITED — seen live
        # on 15 Oct, costing a whole chunk. Reentrant: get() and the retry
        # inside it both hold it.
        self._lock = threading.RLock()

    def _roll_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day, self.requests_today = today, 0

    def budget_left(self) -> int:
        self._roll_day()
        return max(0, settings.oddspapi_daily_request_budget - self.requests_today)

    def account(self) -> dict:
        """GET /account: the plan's request_limit and request_count. UNMETERED
        (OddsPapi docs, "Understanding API Requests & Quota"): never counted,
        never blocked even with the quota spent — so it is how an exhausted
        client finds out the period has reset, at no cost."""
        with self._lock:
            resp = self._send("/account", {})
            if resp.status_code == 429:
                m = _WAIT_RE.search(resp.text or "")
                time.sleep(min(5.0, float(m.group(1)) + 0.2 if m else MIN_REQUEST_GAP))
                resp = self._send("/account", {})
        if resp.status_code != 200:
            raise RuntimeError(f"OddsPapi /account: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def _quota_spent(self) -> bool:
        """True while the plan's monthly quota is known to be used up.

        Once a call has come back REQUEST_LIMIT_EXCEEDED, nothing billable is
        sent; every QUOTA_RECHECK_SECONDS the free /account is asked whether
        the period has reset."""
        if self._exhausted_at is None:
            return False
        if time.monotonic() - self._exhausted_at < QUOTA_RECHECK_SECONDS:
            return True
        try:
            acct = self.account()
            limit = _first(acct, "request_limit", "requestLimit")
            count = _first(acct, "request_count", "requestCount")
            if limit is not None and count is not None and float(count) < float(limit):
                self._exhausted_at = None
                return False
        except Exception as exc:
            logger.warning("cards (oddspapi): /account check failed: %s", exc)
        self._exhausted_at = time.monotonic()
        return True

    def get(self, path: str, **params):
        with self._lock:
            return self._get(path, **params)

    def _get(self, path: str, **params):
        self._roll_day()
        if self._quota_spent():
            raise RuntimeError("OddsPapi monthly request quota exhausted (REQUEST_LIMIT_EXCEEDED)")
        if self.requests_today >= settings.oddspapi_daily_request_budget:
            raise RuntimeError(
                f"OddsPapi daily request budget of "
                f"{settings.oddspapi_daily_request_budget} reached"
            )
        self.requests_today += 1
        resp = self._send(path, params)
        if resp.status_code == 429 and _QUOTA_RE.search(resp.text or ""):
            # The MONTH's quota, not the rate limit: retrying cannot help.
            # Rejected calls are not counted (docs), but there is no point
            # sending any until /account says the period has reset.
            self._exhausted_at = time.monotonic()
            self.requests_today -= 1          # rejected before the endpoint: not billed
            logger.error("cards (oddspapi): monthly request quota exhausted — "
                         "pausing billable calls, rechecking /account every %d min",
                         QUOTA_RECHECK_SECONDS // 60)
            raise RuntimeError("OddsPapi monthly request quota exhausted (REQUEST_LIMIT_EXCEEDED)")
        if resp.status_code == 429:
            # Once, after the wait it names (capped): a rate limit is the API
            # asking for spacing, not refusing the request.
            m = _WAIT_RE.search(resp.text or "")
            time.sleep(min(5.0, float(m.group(1)) + 0.2 if m else MIN_REQUEST_GAP))
            resp = self._send(path, params)
        if resp.status_code == 404 and "FIXTURE_NOT_FOUND" in (resp.text or ""):
            # Nothing scheduled in these competitions (e.g. the internationals
            # outside a window) — an EMPTY answer, not a failure. Returned as
            # such so it is cached at the loosest gear (hourly) rather than
            # retried as an error every 10 minutes; each retry is billed.
            return []
        if resp.status_code != 200:
            raise RuntimeError(f"OddsPapi {path}: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def _send(self, path, params):
        gap = MIN_REQUEST_GAP - (time.monotonic() - self._last_request)
        if gap > 0:
            time.sleep(gap)
        try:
            return httpx.get(f"{BASE_URL}{path}",
                             params={**params, "apiKey": self._key}, timeout=30)
        finally:
            self._last_request = time.monotonic()

    def _cached(self, cache, key, path, **params):
        hit = cache.get(key)
        if hit is not MISS:
            return hit
        data = self.get(path, **params)
        cache.set(key, data)
        return data

    def catalogue(self):
        return self._cached(self._ref, "markets", "/markets")

    def participants(self):
        return self._cached(self._ref, "participants", "/participants", sportId=SOCCER)

    def tournaments(self):
        return self._cached(self._ref, "tournaments", "/tournaments", sportId=SOCCER)

    def resolve_book(self, wanted: str) -> str:
        """The slug this plan actually grants for `wanted`. A plan lists
        e.g. "pinnacle+30" (Pinnacle on a delay), and asking for plain
        "pinnacle" is refused 403 RESTRICTED_ACCESS — billed. Read off the
        free /account, cached a day; falls back to `wanted`."""
        try:
            hit = self._ref.get("account")
            acct = self.account() if hit is MISS else hit
            if hit is MISS:
                self._ref.set("account", acct)
        except Exception:
            return wanted
        granted: set[str] = set()
        for sub in acct.get("subscriptions") or []:
            if sub.get("is_active"):
                granted.update((sub.get("bookmakers") or {}).keys())
        if not granted or wanted in granted:
            return wanted
        for slug in sorted(granted):
            if slug.split("+")[0] == wanted:
                return slug
        return wanted

    def odds_by_tournaments(self, ids: list[int], bookmaker: str = PADDY_POWER):
        key = ("odds", tuple(ids), bookmaker)
        held = self._failed.peek(key)
        if held is not MISS and self._failed.get(key, ttl=held[1]) is not MISS:
            raise RuntimeError(f"{held[0]} (held {int(held[1] // 60)} min, not re-sent)")
        try:
            return self._odds_by_tournaments(key, ids, bookmaker)
        except Exception as exc:
            msg = str(exc)
            # A plan refusal holds for hours; a rate limit only for a minute
            # (it is about spacing, not the request); anything else 10 min.
            hold = (FAILED_HOLD_MAX if _RESTRICTED_RE.search(msg)
                    else 60 if "RATE_LIMITED" in msg else FAILED_HOLD)
            self._failed.set(key, (str(exc), hold))
            raise

    def _odds_by_tournaments(self, key, ids, bookmaker):
        if self._flat_ttl is None:
            # The gear: how soon the cached chunk's next fixture kicks off.
            cached = self._odds.peek(key)
            if cached is not MISS:
                hit = self._odds.get(key, ttl=ttl_for(cached, self._tiers))
                if hit is not MISS:
                    return hit
        return self._cached(self._odds, key, "/odds-by-tournaments",
                            tournamentIds=",".join(str(i) for i in ids),
                            bookmakers=bookmaker)


def tournament_names(data) -> dict[int, str]:
    out = {}
    for t in _as_list(data):
        tid = _first(t, "tournamentId", "id", "_id")
        name = _first(t, "tournamentName", "name")
        try:
            out[int(tid)] = str(name or "")
        except (TypeError, ValueError):
            continue
    return out


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split())


def resolve_tournaments(entries: list[str], known: dict[int, str]) -> tuple[list[int], list[str]]:
    """Config entries -> tournament ids. An entry is an id, or a competition's
    NAME matched exactly (case and punctuation aside) against OddsPapi's own
    list — a name cannot be a wrong guess the way an id can: the Nations League
    id taken from Sofascore (10783) turned out not to be in OddsPapi's list at
    all. Returns (ids, entries that resolved to nothing)."""
    by_name = {_norm(n): i for i, n in known.items() if n}
    ids, unresolved = [], []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        if entry.isdigit():
            ids.append(int(entry))
        elif _norm(entry) in by_name:
            ids.append(by_name[_norm(entry)])
        else:
            unresolved.append(entry)
    return list(dict.fromkeys(ids)), unresolved


def _tournament_entries() -> list[str]:
    return [x.strip() for x in settings.oddspapi_tournament_ids.split(",") if x.strip()]


def _tournament_ids() -> list[int]:
    """The numeric entries only — what can be known without a request."""
    return [int(x) for x in _tournament_entries() if x.isdigit()]


def catalogue_index(catalogue) -> dict[str, dict]:
    """Every catalogue market by id: {type, name, line, period}. For coverage
    reports — the pricing path uses `corner_card_markets`."""
    out = {}
    for m in _as_list(catalogue):
        mid = _first(m, "marketId", "id", "_id")
        if mid is None:
            continue
        line = _first(m, "handicap", "line", "value", "points", "total")
        out[str(mid)] = {
            "type": str(_first(m, "marketType", "type") or _first(m, "marketName", "name") or "?"),
            "name": str(_first(m, "marketName", "name") or ""),
            "line": line,
            "period": str(_first(m, "period", "periodName") or ""),
        }
    return out


def fixture_lines(fixtures, index: dict[str, dict], bookmaker: str, names: dict[str, str],
                  types=("totals-corners", "totals-bookings")) -> list[dict]:
    """Per fixture: its OddsPapi id, teams, kick-off and the full-match lines
    this bookmaker prices (two-sided) for each of `types`. For naming the exact
    fixture a feed is missing a market on — `coverage` only gives totals."""
    out = []
    for fx in _as_list(fixtures):
        book = ((fx.get("bookmakerOdds") or {}).get(bookmaker)) or {}
        lines: dict[str, list] = {t: [] for t in types}
        for mid, market in (book.get("markets") or {}).items():
            info = index.get(str(mid))
            if (info is None or info["type"] not in lines
                    or info["period"].lower() not in _FULL_TIME or info["line"] is None):
                continue
            if sum(1 for o in (market.get("outcomes") or {}).values() if _price(o)) >= 2:
                lines[info["type"]].append(float(info["line"]))
        out.append({
            "fixtureId": _first(fx, "fixtureId", "id"),
            "start": _first(fx, "startTime", "start_time"),
            "home": _first(fx, "participant1Name") or names.get(str(fx.get("participant1Id"))),
            "away": _first(fx, "participant2Name") or names.get(str(fx.get("participant2Id"))),
            **{t: sorted(v) for t, v in lines.items()},
        })
    return sorted(out, key=lambda r: str(r["start"] or ""))


def coverage(fixtures, index: dict[str, dict], bookmaker: str) -> dict:
    """How many LINES a bookmaker quotes per fixture, by market type.

    The question it answers: can this book stand in for another — e.g. does
    Pinnacle on OddsPapi carry team totals and a corners ladder deep enough
    for the counts model (2+ two-sided lines; 4+ to fit dispersion on its own)?
    """
    per_type: dict[str, list[int]] = {}
    names: dict[str, str] = {}
    lines_seen: dict[str, set] = {}
    n = 0
    for fx in _as_list(fixtures):
        book = ((fx.get("bookmakerOdds") or {}).get(bookmaker)) or {}
        markets = book.get("markets") or {}
        if not markets:
            continue
        n += 1
        counts: dict[str, int] = {}
        for mid, market in markets.items():
            info = index.get(str(mid))
            if info is None:
                continue
            key = f"{info['type']} [{info['period'] or '?'}]"
            priced = sum(1 for o in (market.get("outcomes") or {}).values() if _price(o))
            if priced >= 2:
                counts[key] = counts.get(key, 0) + 1
                names.setdefault(key, info["name"])
                if info["line"] is not None:
                    lines_seen.setdefault(key, set()).add(info["line"])
        for key, c in counts.items():
            per_type.setdefault(key, []).append(c)
    report = {}
    for key, cs in sorted(per_type.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        cs = sorted(cs)
        report[key] = {
            "name": names.get(key, ""),
            "fixtures": len(cs),
            "two_sided_lines_per_fixture": {"min": cs[0], "median": cs[len(cs) // 2], "max": cs[-1]},
            "lines_seen": sorted(lines_seen.get(key, set()), key=lambda x: float(x))[:30],
        }
    return {"bookmaker": bookmaker, "fixtures_with_prices": n, "market_types": report}


class OddsPapiPaddySource:
    """The cards tool's Paddy Power side from OddsPapi — and, with
    `oddspapi_fair_book` set (default "pinnacle"), its fair side too. Same interface as
    `CardsApiClient`: `get_events()` and `last_sweep`."""

    def __init__(self, client: OddsPapiClient):
        self._sweep_lock = threading.Lock()
        self._client = client
        self.last_sweep: dict = {}

    def get_events(self) -> list[dict]:
        # One sweep at a time: a page load arriving mid-sweep waits and then
        # reads the cache the sweep just filled, instead of fetching beside it.
        with self._sweep_lock:
            return self._get_events()

    def _get_events(self) -> list[dict]:
        entries = _tournament_entries()
        unresolved: list[str] = []
        if all(e.isdigit() for e in entries):
            ids = _tournament_ids()
        else:
            try:
                ids, unresolved = resolve_tournaments(
                    entries, tournament_names(self._client.tournaments())
                )
            except Exception as exc:
                logger.error("cards (oddspapi): tournament list failed: %s", exc)
                ids, unresolved = _tournament_ids(), [e for e in entries if not e.isdigit()]
            if unresolved:
                logger.warning("cards (oddspapi): competition(s) not found by name: %s",
                               ", ".join(unresolved))
        per = max(1, settings.oddspapi_tournaments_per_request)
        errors = []
        try:
            # Saves ride along on Paddy's side; their fair price comes from
            # Bookie Bashing (cards/saves.py), never from a book here. Goals
            # are priced off Pinnacle's goals ladder like corners and cards.
            markets = match_total_markets(self._client.catalogue(),
                                          kinds=(CORNERS, CARDS, SAVES, GOALS))
            markets.update(team_markets(self._client.catalogue()))  # one team's corners/cards
            names = participant_names(self._client.participants())
        except Exception as exc:
            logger.error("cards (oddspapi): reference data failed: %s", exc)
            self.last_sweep = {"source": "oddspapi", "error": str(exc),
                               "fixtures_in_window": 0, "called": 0,
                               "carried_paddy_prices": 0, "failed": 1}
            return []
        if not markets:
            logger.error(
                "cards (oddspapi): the market catalogue matched NO corners/cards "
                "totals — see /api/paddy/oddspapi-check before trusting this feed"
            )
        fair_book = (settings.oddspapi_fair_book or "").strip() or None
        if fair_book:
            fair_book = self._client.resolve_book(fair_book)
        per_book: dict[str, list] = {}
        for book in [PADDY_POWER] + ([fair_book] if fair_book else []):
            got = per_book.setdefault(book, [])
            for i in range(0, len(ids), per):
                chunk = ids[i:i + per]
                try:
                    got.extend(_as_list(self._client.odds_by_tournaments(chunk, bookmaker=book)))
                except Exception as exc:
                    errors.append(f"{book} {chunk}: {exc}")
                    logger.error("cards (oddspapi): %s odds for %s failed: %s", book, chunk, exc)
        fixtures = merge_books(*per_book.values())
        events, counts = to_events(fixtures, markets, names, fair_book=fair_book)
        self.last_sweep = {
            "source": "oddspapi",
            "fair_source": f"oddspapi:{fair_book}" if fair_book else "bettingiscool",
            "window_hours": settings.oddspapi_window_hours,
            "tournaments": len(ids),
            "unresolved_tournaments": unresolved,
            **counts,
            "called": counts["fixtures_in_window"],
            "failed": len(errors),
            "errors": errors,
            "catalogue_total_markets": len(markets),
            "names_known": len(names),
            "requests_today": self._client.requests_today,
            "request_budget": settings.oddspapi_daily_request_budget,
        }
        log = logger.info if not errors else logger.warning
        log("cards sweep (oddspapi): %d fixture(s) in window, %d carried Paddy "
            "corners/cards, %d of those with %s's, %d request(s) today",
            counts["fixtures_in_window"], counts["carried_paddy_prices"],
            counts["carried_pinnacle_prices"], fair_book or "no fair book",
            self._client.requests_today)
        return events
