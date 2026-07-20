"""Shared TheOddsApi HTTP+cache client used by all three services.

Free tier: 500 credits/month. The cache TTL (default 24 h, set via
Settings.odds_cache_ttl) keeps usage well under that limit regardless of how
often the services are queried (all reads hit the cache). Each service fetches
different sport_key+market combinations, so cache entries are naturally isolated
and there is no cross-service interference.
"""
import logging
import time

import httpx

logger = logging.getLogger("uvicorn.error")

BASE_URL = "https://api.the-odds-api.com"
DEFAULT_CACHE_TTL = 86400  # 24 hours


class TheOddsApiClient:
    def __init__(self, api_key: str, cache_ttl: int = DEFAULT_CACHE_TTL):
        self._api_key = api_key
        self._cache_ttl = cache_ttl
        # keyed by (sport_key, markets, bookmakers) → (fetched_at, events)
        self._cache: dict[tuple[str, str, str], tuple[float, list]] = {}

    def fetch(
        self,
        sport_key: str,
        markets: str,
        bookmakers: str | None = None,
        ttl: int | None = None,
    ) -> list[dict]:
        """Return events for a sport/market combo, cached for `ttl` seconds.

        sport_key:  e.g. "soccer_epl", "tennis_atp"
        markets:    comma-separated, e.g. "h2h,totals" or "alternate_totals"
        bookmakers: optional comma-separated filter, e.g. "pinnacle,paddypower"
        ttl:        override the client-wide cache TTL for THIS read (seconds).
                    The coupon tracker passes its own 30-min TTL so its full
                    per-sport pull refreshes on a tighter cadence than the
                    24-h default the other tools rely on.
        """
        cache_key = (sport_key, markets, bookmakers or "")
        effective_ttl = self._cache_ttl if ttl is None else ttl
        now = time.monotonic()

        if cache_key in self._cache:
            fetched_at, events = self._cache[cache_key]
            if now - fetched_at < effective_ttl:
                return events

        params: dict = {
            "apiKey": self._api_key,
            "regions": "eu",
            "markets": markets,
            "oddsFormat": "decimal",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers

        resp = httpx.get(
            f"{BASE_URL}/v4/sports/{sport_key}/odds",
            params=params,
            timeout=15,
        )
        if resp.status_code == 404:
            self._cache[cache_key] = (now, [])
            return []
        resp.raise_for_status()
        # TheOddsApi reports quota in response headers — log every real call.
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        logger.info(
            "OddsApi call: %s [%s] — quota used=%s remaining=%s",
            sport_key, markets, used, remaining,
        )
        events = resp.json()
        self._cache[cache_key] = (now, events)
        return events

    def list_sports(self) -> list[dict]:
        """All sport keys from /v4/sports, cached like odds responses.

        This endpoint is free — it does not count against the monthly quota —
        but is cached anyway to keep latency down.
        """
        cache_key = ("__sports__", "", "")
        now = time.monotonic()

        if cache_key in self._cache:
            fetched_at, sports = self._cache[cache_key]
            if now - fetched_at < self._cache_ttl:
                return sports

        resp = httpx.get(
            f"{BASE_URL}/v4/sports",
            params={"apiKey": self._api_key},
            timeout=15,
        )
        resp.raise_for_status()
        sports = resp.json()
        self._cache[cache_key] = (now, sports)
        return sports
