"""The odds-api.net HTTP client: Hills' William Hill and Pinnacle prices.

THE PLAN (Nov 2026): 250,000 credits a month, 120 requests a minute, and one
credit per call whatever the answer, so a failed call is billed like a good
one. Every request is therefore counted, failures included, against a daily
cap (`oddsapinet_daily_credit_budget`), and a spent cap sends nothing.

THE KEY rides in the `X-API-Key` header, never in the URL, so it is in no
logged URL and no error message. Only the path and the params are logged.

SPACING. One request in flight at a time, at least MIN_REQUEST_GAP apart
(the sweep and a page load can fetch at the same moment). A 429 raises
`RateLimited` with the Retry-After it named and is NOT retried here: the
caller decides whether a retry is worth another credit.

The clock, the sleep, the UTC day and the transport are injectable so the
tests run with no network and no waiting.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import logging
import threading
import time
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.odds-api.net/v1"
# 120/min allows one every 0.5 s.
MIN_REQUEST_GAP = 0.5


class BudgetSpent(RuntimeError):
    """The day's credit budget is used up; nothing was sent."""


class RateLimited(RuntimeError):
    """odds-api.net answered 429. `retry_after` is its Retry-After in seconds,
    or None when it named none."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None


class OddsApiNetClient:
    """HTTP only: counts, caps and spaces requests. No cache here."""

    vendor = "odds-api.net"

    def __init__(
        self,
        api_key: str,
        *,
        daily_budget: int = 5000,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = _utc_today,
    ):
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"X-API-Key": api_key},
            timeout=20,
            transport=transport,
        )
        self.daily_budget = daily_budget
        self.requests_today = 0
        self._sleep = sleep
        self._monotonic = monotonic
        self._today = today
        self._day: date | None = None
        self._last_request: float | None = None
        self._lock = threading.Lock()

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self.requests_today = today, 0

    def budget_left(self) -> int:
        with self._lock:
            self._roll_day()
            return max(0, self.daily_budget - self.requests_today)

    def get(self, path: str, **params) -> dict:
        with self._lock:
            self._roll_day()
            if self.requests_today >= self.daily_budget:
                raise BudgetSpent("daily credit budget spent")
            if self._last_request is not None:
                gap = MIN_REQUEST_GAP - (self._monotonic() - self._last_request)
                if gap > 0:
                    self._sleep(gap)
            self.requests_today += 1
            logger.debug("odds-api.net GET %s %s", path, params)
            try:
                resp = self._http.get(path, params=params)
            finally:
                self._last_request = self._monotonic()
            if resp.status_code == 429:
                retry_after = _retry_after(resp)
                raise RateLimited(f"odds-api.net {path}: HTTP 429", retry_after)
            resp.raise_for_status()
            return resp.json()
