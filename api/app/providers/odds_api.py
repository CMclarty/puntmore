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
from ..picks import (
    OVER_RE, UNDER_RE, WIN_RE, WIN_TO_NIL_RE, TTS_RE, GG_RE,
    is_team_specific, side_of,
)
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

# Reading a pick's market and team is shared with the live provider
# (app/picks.py). It used to be duplicated here, and the two copies diverged:
# this one had no word boundary on the over-line, no Win-to-Nil branch and no
# side resolution at all, so the same coupon priced differently depending on
# which provider a deploy happened to be running.
_OVER_RE = OVER_RE
_UNDER_RE = UNDER_RE
_WIN_RE = WIN_RE

# What this provider CANNOT price, and why refusing beats approximating.
#
# TheOddsApi's free tier serves h2h + totals for the whole fixture. It has no
# team totals and no score grid, so three of the coupon markets have no honest
# route to a price here:
#
#   TTS1/2/3    needs team totals            -> the vendor does not serve them
#   Win to Nil  needs the joint distribution -> moneyline x clean sheet is the
#                                               correlation trap (SPEC 8.5)
#   GG / GGND   needs the joint distribution -> same trap
#
# "Win to Nil" is the dangerous one: it contains "win", so before this it fell
# through to the moneyline branch and came back with the STRAIGHT WIN price on
# a materially shorter bet — a confident wrong price, which is the one outcome
# every rule in this codebase is written to avoid. An unpriced leg is visible
# and correctable; a wrong one reads as value and fires Discord alerts.
_UNSUPPORTED = (
    ("Team to Score", TTS_RE),
    ("Win to Nil", WIN_TO_NIL_RE),
    ("GG / both teams to score", GG_RE),
)


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


def unsupported_reason(market_str: str) -> str | None:
    """The market this pick names, if this provider cannot honestly price it.

    Returned rather than raised so callers can report it: a leg that is
    unpriced BECAUSE THE PROVIDER CANNOT PRICE THIS MARKET is a different
    problem from one whose fixture did not match, and the two looked identical
    (both simply had no fair odds) until this existed. On a coupon whose whole
    market is unsupported that is the difference between "every fixture failed
    to match" and "this deploy is on the fallback provider".
    """
    for label, pattern in _UNSUPPORTED:
        if pattern.search(market_str or ""):
            return label
    return None


def _extract_fair_odds(event: dict, market_str: str) -> float | None:
    bm = _pick_bookmaker(event.get("bookmakers", []))
    if not bm:
        return None

    # Refuse before parsing anything. Win-to-Nil in particular MUST be caught
    # here: it contains "win" and would otherwise take the moneyline price.
    if unsupported_reason(market_str) is not None:
        return None

    home = event.get("home_team", "")
    away = event.get("away_team", "")
    key = market_str.strip().lower()

    has_win = bool(_WIN_RE.search(key))
    over_m = _OVER_RE.search(key)
    under_m = _UNDER_RE.search(key)

    # WHICH TEAM the pick names. This provider used to hardcode the home team,
    # so "Omonia Nicosia Win" came back with Kairat's price — an away selection
    # priced as its opponent. Home is usually the favourite, so that overstates
    # value: it INVENTS edge rather than hiding it. Unresolved stays unpriced.
    side = side_of(market_str, home, away) if is_team_specific(market_str) else None
    if is_team_specific(market_str) and side is None:
        return None
    team = home if side else away

    if has_win and over_m:
        # LEGACY, and NOT the live path — COUPON_ODDS_SOURCE is bettingiscool,
        # which derives this market off the fitted score grid (pricing/grid.py).
        # The product of two de-vigged prices assumes result and total are
        # independent. They are not — a winning team scores more — so this
        # OVERSTATES fair odds and UNDERSTATES value. The bias is conservative
        # (misses value rather than inventing it). This provider now has no
        # grid of its own, so if it is ever made live again, wire it to
        # pricing.grid the way bettingiscool is rather than shipping this.
        win_price = _fair_h2h_price(bm, team)
        over_price = _fair_totals_price(bm, "Over", float(over_m.group(1)))
        if win_price is not None and over_price is not None:
            return round(win_price * over_price, 4)
        return None

    if has_win and not over_m and not under_m:
        return _fair_h2h_price(bm, team)

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
        refused: dict[str, int] = {}
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
            if fair is None:
                reason = unsupported_reason(q.market)
                if reason is not None:
                    refused[reason] = refused.get(reason, 0) + 1
            results.append(
                OddsResult(match_id=q.match_id, fair_odds=fair, matched_fixture=fixture)
            )

        # ONE line per pricing pass, not one per leg. A coupon of 154 legs in an
        # unsupported market is 154 identical failures, and the useful fact is
        # the market and the count — the thing that says "this deploy is on the
        # fallback provider", which every other symptom hides.
        for reason, count in refused.items():
            logger.warning(
                "TheOddsApi cannot price %s — %d selection(s) left unpriced. "
                "This provider serves h2h + totals only; %s needs the live "
                "bettingiscool source (set BETTINGISCOOL_API_KEY).",
                reason, count, reason,
            )
        return results
