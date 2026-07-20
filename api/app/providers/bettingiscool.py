"""BettingIsCool OddsProvider — Pinnacle-referenced coupon fair odds.

The coupon tracker's intended odds source (SPEC §5, user decision July 2026):
team totals are a standard market and raw prices are exposed on every snapshot,
which is what in-house de-vig (§7) and the derived score-grid model (§8) need.

STATUS: transport NOT IMPLEMENTED — see `_fetch` below.

What is known about the vendor API:
  Base URL   https://api.bettingiscool.com
  Auth       X-API-Key header
  Endpoints  /api/odds (odds snapshots), /api/fixtures (fixtures; live=1 for
             in-play, each live event carries parent_id -> prematch event)
  Billing    1 token per request + 1 token per row; empty responses still cost 1
  Quota      responses carry X-Quota-Limit / X-Quota-Remaining / X-Quota-Cost /
             X-Rows headers
  Fields     todds1 / todds0 / todds2 = vendor de-vigged fair odds for home /
             draw / away

What is NOT known, and must not be guessed:
  - /api/odds query parameters (how to select sport, league, fixture, market)
  - the RAW price field names — the pricing path depends on these, not on
    todds*, which SPEC §7.1 restricts to cross-check tests only
  - how team totals are represented (market key, line encoding, per-team split)
  - the fixtures schema and how to map an OCR'd match name onto an event id

Filling those in requires either the vendor docs or a key to probe the live API.
Until then this class raises on use and `main.py` does not select it.

To implement:
  1. `_fetch` — HTTP against /api/odds with X-API-Key, honouring the quota
     headers (log X-Quota-Remaining; back off rather than burn the daily cap).
  2. `get_fair_odds` — map queries to fixtures, pull raw prices, de-vig
     IN-HOUSE via odds/utils.py (never return todds* as fair odds), return
     OddsResult with matched_fixture set so a wrong fuzzy match stays visible.
  3. Add a cross-check test asserting in-house de-vigged probabilities agree
     with vendor todds* within tolerance — that is what todds* is for.
"""
import logging

from .base import OddsProvider, MatchQuery, OddsResult

logger = logging.getLogger("uvicorn.error")

BASE_URL = "https://api.bettingiscool.com"
ODDS_PATH = "/api/odds"
FIXTURES_PATH = "/api/fixtures"


class BettingIsCoolProviderNotConfigured(RuntimeError):
    """Raised when the provider is selected before its transport exists."""


class BettingIsCoolProvider(OddsProvider):
    def __init__(self, api_key: str, cache_ttl: int | None = None):
        if not api_key:
            raise BettingIsCoolProviderNotConfigured(
                "BETTINGISCOOL_API_KEY is not set"
            )
        self._api_key = api_key
        self._cache_ttl = cache_ttl

    def _fetch(self, *args, **kwargs):
        raise BettingIsCoolProviderNotConfigured(
            "BettingIsCoolProvider transport is not implemented: the vendor's "
            "/api/odds request and response schema is not known. Supply the "
            "vendor docs or an API key to probe the live endpoint. Do NOT "
            "guess field names — the pricing path depends on raw prices, and "
            "silently wrong ones produce plausible-looking fair odds."
        )

    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        raise BettingIsCoolProviderNotConfigured(
            "BettingIsCoolProvider is not implemented — see module docstring."
        )
