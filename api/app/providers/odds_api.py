"""TheOddsApi OddsProvider for coupon fair-odds lookup.

Fetches h2h + totals for major European soccer leagues via the shared
TheOddsApiClient (HTTP, caching, quota management all live there).

To add a new sport or league: append its key to SOCCER_SPORT_KEYS below.
To swap the preferred bookmaker: change PREFERRED_BOOKMAKER.

Market support: "win", "over X.X", "under X.X", "win and over X.X" (compound).
Compound fair odds = h2h_price × totals_price (independence approximation).
"""
import re
import difflib

from .base import OddsProvider, MatchQuery, OddsResult
from ..normalise import normalise_match
from ..odds.client import TheOddsApiClient

PREFERRED_BOOKMAKER = "pinnacle"

SOCCER_SPORT_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
]

_OVER_RE = re.compile(r'over\s+([\d.]+)', re.IGNORECASE)
_UNDER_RE = re.compile(r'under\s+([\d.]+)', re.IGNORECASE)
_WIN_RE = re.compile(r'\bwin\b', re.IGNORECASE)


def _pick_bookmaker(bookmakers: list[dict]) -> dict | None:
    for bm in bookmakers:
        if bm["key"] == PREFERRED_BOOKMAKER:
            return bm
    return bookmakers[0] if bookmakers else None


def _h2h_price(bm: dict, team_name: str) -> float | None:
    for market in bm.get("markets", []):
        if market["key"] != "h2h":
            continue
        for outcome in market["outcomes"]:
            if outcome["name"] == team_name:
                return outcome["price"]
    return None


def _totals_price(bm: dict, side: str, points: float) -> float | None:
    for market in bm.get("markets", []):
        if market["key"] != "totals":
            continue
        for outcome in market["outcomes"]:
            if outcome["name"].lower() == side.lower():
                try:
                    if abs(float(outcome.get("point", 0)) - points) < 0.05:
                        return outcome["price"]
                except (ValueError, TypeError):
                    continue
    return None


def _extract_fair_odds(event: dict, market_str: str) -> float | None:
    bm = _pick_bookmaker(event.get("bookmakers", []))
    if not bm:
        return None

    home = event.get("home_team", "")
    key = market_str.strip().lower()

    has_win = bool(_WIN_RE.search(key))
    over_m = _OVER_RE.search(key)
    under_m = _UNDER_RE.search(key)

    if has_win and over_m:
        win_price = _h2h_price(bm, home)
        over_price = _totals_price(bm, "Over", float(over_m.group(1)))
        if win_price is not None and over_price is not None:
            return round(win_price * over_price, 4)
        return None

    if has_win and not over_m and not under_m:
        return _h2h_price(bm, home)

    if over_m:
        return _totals_price(bm, "Over", float(over_m.group(1)))

    if under_m:
        return _totals_price(bm, "Under", float(under_m.group(1)))

    return None


class TheOddsApiProvider(OddsProvider):
    def __init__(self, client: TheOddsApiClient, sport_keys: list[str] | None = None):
        self._client = client
        self._sport_keys = sport_keys or SOCCER_SPORT_KEYS

    def _all_events(self) -> list[dict]:
        events = []
        for sport_key in self._sport_keys:
            try:
                events.extend(self._client.fetch(sport_key, "h2h,totals"))
            except Exception:
                continue
        return events

    @staticmethod
    def _best_match(query: str, candidates: list[str]) -> str | None:
        q_norm = normalise_match(query)
        norm_to_orig: dict[str, str] = {}
        for c in candidates:
            norm_to_orig.setdefault(normalise_match(c), c)
        scored = [
            (difflib.SequenceMatcher(None, q_norm, c_norm).ratio(), orig)
            for c_norm, orig in norm_to_orig.items()
        ]
        scored.sort(reverse=True)
        if scored and scored[0][0] >= 0.6:
            return scored[0][1]
        return None

    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        events = self._all_events()

        name_to_event: dict[str, dict] = {}
        for e in events:
            key = f"{e.get('home_team', '')} vs {e.get('away_team', '')}"
            name_to_event[key] = e

        results = []
        for q in queries:
            fair = None
            matched = self._best_match(q.match_name, list(name_to_event.keys()))
            if matched:
                fair = _extract_fair_odds(name_to_event[matched], q.market)
            results.append(OddsResult(match_id=q.match_id, fair_odds=fair))
        return results
