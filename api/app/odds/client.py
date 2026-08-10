"""Shared TheOddsApi HTTP+cache client used by all three services.

Free tier: 500 credits/month. The cache TTL (default 24 h, set via
Settings.odds_cache_ttl) keeps usage well under that limit regardless of how
often the services are queried (all reads hit the cache). Each service fetches
different sport_key+market combinations, so cache entries are naturally isolated
and there is no cross-service interference.
"""
import logging

import httpx

from ..ttl_cache import MISS, TTLCache

logger = logging.getLogger("uvicorn.error")

BASE_URL = "https://api.the-odds-api.com"
DEFAULT_CACHE_TTL = 86400  # 24 hours

# Ceilings on the two caches below. The sport/market cache has a naturally tiny
# key set, but the PER-EVENT one is keyed by event id and was a plain dict that
# never dropped anything — every fixture ever scanned for first goalscorer stayed
# in memory until the process died. Bounded now; see ttl_cache.py.
CACHE_MAX = 128
EVENT_CACHE_MAX = 256


class TheOddsApiClient:
    def __init__(self, api_key: str, cache_ttl: int = DEFAULT_CACHE_TTL):
        self._api_key = api_key
        self._cache_ttl = cache_ttl
        # keyed by (sport_key, markets, bookmakers)
        self._cache = TTLCache(cache_ttl, CACHE_MAX)
        # per-event odds cache, keyed by ("__event__", event_id, markets, books)
        self._event_cache = TTLCache(cache_ttl, EVENT_CACHE_MAX)

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

        cached = self._cache.get(cache_key, ttl=effective_ttl)
        if cached is not MISS:
            return cached

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
            self._cache.set(cache_key, [])
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
        self._cache.set(cache_key, events)
        return events

    def fetch_event(
        self,
        sport_key: str,
        event_id: str,
        markets: str,
        bookmakers: str | None = None,
        ttl: int | None = None,
    ) -> dict:
        """Return one event's odds from the PER-EVENT endpoint, cached.

        Player-prop and alternate markets (e.g. player_first_goal_scorer) are
        NOT served by the bulk /odds endpoint — it rejects them with
        INVALID_MARKET (see docs/provider-verification.md). They live only on
        /v4/sports/{sport}/events/{eventId}/odds, billed per event as
        (markets returned × regions).

        Returns the event dict, or {} if the event/market is absent (404).
        """
        cache_key = ("__event__", event_id, markets, bookmakers or "")
        effective_ttl = self._cache_ttl if ttl is None else ttl

        cached = self._event_cache.get(cache_key, ttl=effective_ttl)
        if cached is not MISS:
            return cached

        params: dict = {
            "apiKey": self._api_key,
            "regions": "eu",
            "markets": markets,
            "oddsFormat": "decimal",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers

        resp = httpx.get(
            f"{BASE_URL}/v4/sports/{sport_key}/events/{event_id}/odds",
            params=params,
            timeout=15,
        )
        if resp.status_code == 404:
            self._event_cache.set(cache_key, {})
            return {}
        resp.raise_for_status()
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        logger.info(
            "OddsApi per-event call: %s/%s [%s] — quota used=%s remaining=%s",
            sport_key, event_id, markets, used, remaining,
        )
        event = resp.json()
        self._event_cache.set(cache_key, event)
        return event

    def list_events(self, sport_key: str, ttl: int | None = None) -> list[dict]:
        """Today-and-upcoming fixtures for a sport (id, teams, commence_time).

        Uses /v4/sports/{sport}/events, which is FREE (no quota) — it carries no
        odds, just the fixture list the watchlist is built from.
        """
        cache_key = ("__events__", sport_key, "")
        effective_ttl = self._cache_ttl if ttl is None else ttl

        cached = self._cache.get(cache_key, ttl=effective_ttl)
        if cached is not MISS:
            return cached

        resp = httpx.get(
            f"{BASE_URL}/v4/sports/{sport_key}/events",
            params={"apiKey": self._api_key},
            timeout=15,
        )
        if resp.status_code == 404:
            self._cache.set(cache_key, [])
            return []
        resp.raise_for_status()
        events = resp.json()
        self._cache.set(cache_key, events)
        return events

    def list_sports(self) -> list[dict]:
        """All sport keys from /v4/sports, cached like odds responses.

        This endpoint is free — it does not count against the monthly quota —
        but is cached anyway to keep latency down.
        """
        cache_key = ("__sports__", "", "")

        cached = self._cache.get(cache_key)
        if cached is not MISS:
            return cached

        resp = httpx.get(
            f"{BASE_URL}/v4/sports",
            params={"apiKey": self._api_key},
            timeout=15,
        )
        resp.raise_for_status()
        sports = resp.json()
        self._cache.set(cache_key, sports)
        return sports
