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
TheOddsApi's per-event billing forced. Cached for `oddspapi_ttl_seconds`, and
capped per day by `oddspapi_daily_request_budget`.
"""
from datetime import datetime, timedelta, timezone
import logging
import re

import httpx

from ..config import settings
from ..ttl_cache import MISS, TTLCache

logger = logging.getLogger("uvicorn.error")

BASE_URL = "https://api.oddspapi.io/v4"
SOCCER = 10
PADDY_POWER = "paddypower"
CORNERS = "alternate_totals_corners"
CARDS = "alternate_totals_cards"

# Sofascore tournament id -> the TheOddsApi-style key the counts model keys its
# per-league dispersion priors by. A tournament missing here still prices; it
# takes the default prior, which only matters on a sparse Pinnacle ladder.
TOURNAMENT_SPORT_KEYS = {
    17: "soccer_epl", 18: "soccer_efl_champ", 24: "soccer_england_league1",
    8: "soccer_spain_la_liga", 23: "soccer_italy_serie_a",
    35: "soccer_germany_bundesliga", 34: "soccer_france_ligue_one",
    238: "soccer_portugal_primeira_liga", 7: "soccer_uefa_champs_league",
    679: "soccer_uefa_europa_league", 10783: "soccer_uefa_nations_league",
}

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
    out = {}
    for m in _as_list(catalogue):
        name = str(_first(m, "marketName", "name", "marketType", "type") or "")
        if _CORNER_RE.search(name):
            key = CORNERS
        elif _CARD_RE.search(name):
            key = CARDS
        else:
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


def to_events(fixtures, markets, names, *, now=None, window_hours=None) -> tuple[list[dict], dict]:
    """OddsPapi fixtures -> TheOddsApi-shaped events carrying Paddy's corners/cards.

    Returns (events, counts). Only fixtures still to kick off inside the window
    are kept, and only ones where Paddy prices at least one corners/cards line.
    """
    now = now or datetime.now(timezone.utc)
    window = timedelta(hours=window_hours if window_hours is not None
                       else settings.cards_window_hours)
    counts = {"fixtures_listed": 0, "fixtures_in_window": 0,
              "carried_paddy_prices": 0, "unnamed": 0}
    events = []
    for fx in _as_list(fixtures):
        counts["fixtures_listed"] += 1
        start = _parse_start(_first(fx, "startTime", "start_time", "commence_time"))
        if start is None or not (timedelta(0) < start - now <= window):
            continue
        counts["fixtures_in_window"] += 1
        book = ((fx.get("bookmakerOdds") or {}).get(PADDY_POWER)) or {}
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
        if not by_key:
            continue
        home = _first(fx, "participant1Name") or names.get(str(fx.get("participant1Id")))
        away = _first(fx, "participant2Name") or names.get(str(fx.get("participant2Id")))
        if not home or not away:
            # Without names the Pinnacle side cannot be joined, and a row with
            # no teams on it cannot be bet. Counted, not guessed.
            counts["unnamed"] += 1
            continue
        counts["carried_paddy_prices"] += 1
        events.append({
            "id": str(_first(fx, "fixtureId", "id")),
            "home_team": home,
            "away_team": away,
            "commence_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sport_key": TOURNAMENT_SPORT_KEYS.get(fx.get("tournamentId"), "soccer"),
            "bookmakers": [{
                "key": PADDY_POWER,
                "markets": [{"key": k, "outcomes": v} for k, v in by_key.items()],
            }],
        })
    return events, counts


class OddsPapiClient:
    """HTTP + cache. Every request is counted, because OddsPapi bills per request."""

    def __init__(self, api_key: str, *, ttl: float | None = None):
        self._key = api_key
        self._ttl = ttl if ttl is not None else settings.oddspapi_ttl_seconds
        # Reference data (catalogue, names) changes rarely; odds per TTL.
        self._ref = TTLCache(ttl=24 * 3600, maxsize=16)
        self._odds = TTLCache(ttl=self._ttl, maxsize=64)
        self.requests_today = 0
        self._day = None

    def _roll_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day, self.requests_today = today, 0

    def budget_left(self) -> int:
        self._roll_day()
        return max(0, settings.oddspapi_daily_request_budget - self.requests_today)

    def get(self, path: str, **params):
        self._roll_day()
        if self.requests_today >= settings.oddspapi_daily_request_budget:
            raise RuntimeError(
                f"OddsPapi daily request budget of "
                f"{settings.oddspapi_daily_request_budget} reached"
            )
        self.requests_today += 1
        resp = httpx.get(f"{BASE_URL}{path}", params={**params, "apiKey": self._key},
                         timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"OddsPapi {path}: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

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

    def odds_by_tournaments(self, ids: list[int]):
        key = ("odds", tuple(ids))
        return self._cached(self._odds, key, "/odds-by-tournaments",
                            tournamentIds=",".join(str(i) for i in ids),
                            bookmaker=PADDY_POWER)


def _tournament_ids() -> list[int]:
    return [int(x) for x in settings.oddspapi_tournament_ids.split(",") if x.strip()]


class OddsPapiPaddySource:
    """The cards tool's Paddy Power side, from OddsPapi. Same interface as
    `CardsApiClient`: `get_events()` and `last_sweep`."""

    def __init__(self, client: OddsPapiClient):
        self._client = client
        self.last_sweep: dict = {}

    def get_events(self) -> list[dict]:
        ids = _tournament_ids()
        per = max(1, settings.oddspapi_tournaments_per_request)
        errors = []
        try:
            markets = corner_card_markets(self._client.catalogue())
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
                "totals — see /api/cards/oddspapi-check before trusting this feed"
            )
        fixtures = []
        for i in range(0, len(ids), per):
            chunk = ids[i:i + per]
            try:
                fixtures.extend(_as_list(self._client.odds_by_tournaments(chunk)))
            except Exception as exc:
                errors.append(f"{chunk}: {exc}")
                logger.error("cards (oddspapi): odds for %s failed: %s", chunk, exc)
        events, counts = to_events(fixtures, markets, names)
        self.last_sweep = {
            "source": "oddspapi",
            "window_hours": settings.cards_window_hours,
            "tournaments": len(ids),
            **counts,
            "called": counts["fixtures_in_window"],
            "failed": len(errors),
            "errors": errors,
            "catalogue_corner_card_markets": len(markets),
            "names_known": len(names),
            "requests_today": self._client.requests_today,
            "request_budget": settings.oddspapi_daily_request_budget,
        }
        log = logger.info if not errors else logger.warning
        log("cards sweep (oddspapi): %d fixture(s) in window, %d carried Paddy "
            "corners/cards, %d request(s) today", counts["fixtures_in_window"],
            counts["carried_paddy_prices"], self._client.requests_today)
        return events
