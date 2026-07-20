"""TheOddsApi OddsProvider for coupon fair-odds lookup.

Fetches h2h + totals for major European soccer leagues via the shared
TheOddsApiClient (HTTP, caching, quota management all live there).

To add a new sport or league: append its key to SOCCER_SPORT_KEYS below.
To swap the preferred bookmaker: change PREFERRED_BOOKMAKER.

Fair odds are computed IN-HOUSE: raw bookmaker prices are de-vigged with the
power method (`odds/utils.py`) before anything is returned. Vendor prices are
never passed through as fair odds — the overround would show up as phantom
value and fire false Discord alerts.

De-vigging needs the complete outcome set (both sides of a total, all of 1X2).
Where that isn't available the provider returns None: an unpriced leg is
correct, a vigged one is not.

Market support: "win", "over X.X", "under X.X", "win and over X.X" (compound).
Compound legs are still an independence approximation — see the note at the
call site; PLAN V3 replaces them with the score-grid model.
"""
import re
import difflib
import logging

from .base import OddsProvider, MatchQuery, OddsResult
from ..normalise import normalise_match
from ..odds.client import TheOddsApiClient
from ..odds.utils import devigd, devigd_multi

logger = logging.getLogger("uvicorn.error")

PREFERRED_BOOKMAKER = "pinnacle"

# Fuzzy-match thresholds: below MATCH_THRESHOLD a query is unmatched; between
# the two, the match is accepted but logged for human review (a wrong fixture
# silently produces a wrong fair price).
MATCH_THRESHOLD = 0.6
CONFIDENT_THRESHOLD = 0.75

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


def _valid_price(price) -> float | None:
    """Decimal odds must be a number > 1.0 — anything else is unusable."""
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    return float(price) if price > 1.0 else None


def _fair_h2h_price(bm: dict, team_name: str) -> float | None:
    """De-vigged price for one 1X2 outcome (power method, over the whole market).

    The FULL outcome set must be priced to de-vig it — the margin is a property
    of the market, not of a single selection. A partial or malformed market
    yields None rather than a vigged price masquerading as fair odds.
    """
    for market in bm.get("markets", []):
        if market.get("key") != "h2h":
            continue
        prices: list[float] = []
        target_idx: int | None = None
        for outcome in market.get("outcomes", []):
            price = _valid_price(outcome.get("price"))
            if price is None:
                return None
            if outcome.get("name") == team_name:
                target_idx = len(prices)
            prices.append(price)
        if target_idx is None or len(prices) < 2:
            return None
        fair_prices, _margin = devigd_multi(prices)
        return round(fair_prices[target_idx], 4)
    return None


def _fair_totals_price(bm: dict, side: str, points: float) -> float | None:
    """De-vigged price for Over or Under at `points`.

    Both sides of the line are required: de-vigging a two-outcome market needs
    both prices. A one-sided line yields None.
    """
    for market in bm.get("markets", []):
        if market.get("key") != "totals":
            continue
        over = under = None
        for outcome in market.get("outcomes", []):
            try:
                if abs(float(outcome.get("point")) - points) >= 0.05:
                    continue
            except (ValueError, TypeError):
                continue
            price = _valid_price(outcome.get("price"))
            if price is None:
                continue
            name = str(outcome.get("name", "")).lower()
            if name == "over":
                over = price
            elif name == "under":
                under = price
        if over is None or under is None:
            return None
        fair_over, fair_under, _margin = devigd(over, under)
        return round(fair_over if side.lower() == "over" else fair_under, 4)
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
        # LEGACY, and NOT the live path — COUPON_ODDS_SOURCE is bettingiscool,
        # which derives this market off the fitted score grid (pricing/grid.py).
        # The product of two de-vigged prices assumes result and total are
        # independent. They are not — a winning team scores more — so this
        # OVERSTATES fair odds and UNDERSTATES value. The bias is conservative
        # (misses value rather than inventing it). This provider now has no
        # grid of its own, so if it is ever made live again, wire it to
        # pricing.grid the way bettingiscool is rather than shipping this.
        win_price = _fair_h2h_price(bm, home)
        over_price = _fair_totals_price(bm, "Over", float(over_m.group(1)))
        if win_price is not None and over_price is not None:
            return round(win_price * over_price, 4)
        return None

    if has_win and not over_m and not under_m:
        return _fair_h2h_price(bm, home)

    if over_m:
        return _fair_totals_price(bm, "Over", float(over_m.group(1)))

    if under_m:
        return _fair_totals_price(bm, "Under", float(under_m.group(1)))

    return None


class TheOddsApiProvider(OddsProvider):
    def __init__(
        self,
        client: TheOddsApiClient,
        sport_keys: list[str] | None = None,
        cache_ttl: int | None = None,
    ):
        self._client = client
        self._sport_keys = sport_keys or SOCCER_SPORT_KEYS
        # Coupon-specific cache TTL: one full /odds response per sport is pulled
        # and reused for this long. None = fall back to the client-wide TTL.
        self._cache_ttl = cache_ttl

    def _all_events(self) -> list[dict]:
        events = []
        for sport_key in self._sport_keys:
            try:
                events.extend(
                    self._client.fetch(sport_key, "h2h,totals", ttl=self._cache_ttl)
                )
            except Exception:
                continue
        return events

    @staticmethod
    def _best_match(query: str, candidates: list[str]) -> tuple[str, float] | None:
        q_norm = normalise_match(query)
        norm_to_orig: dict[str, str] = {}
        for c in candidates:
            norm_to_orig.setdefault(normalise_match(c), c)
        scored = [
            (difflib.SequenceMatcher(None, q_norm, c_norm).ratio(), orig)
            for c_norm, orig in norm_to_orig.items()
        ]
        scored.sort(reverse=True)
        if scored and scored[0][0] >= MATCH_THRESHOLD:
            score, orig = scored[0]
            return orig, score
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
            fixture = None
            match = self._best_match(q.match_name, list(name_to_event.keys()))
            if match:
                fixture, score = match
                if score < CONFIDENT_THRESHOLD:
                    logger.warning(
                        "Borderline fixture match (%.2f): %r -> %r — verify in UI",
                        score, q.match_name, fixture,
                    )
                fair = _extract_fair_odds(name_to_event[fixture], q.market)
            results.append(
                OddsResult(match_id=q.match_id, fair_odds=fair, matched_fixture=fixture)
            )
        return results
