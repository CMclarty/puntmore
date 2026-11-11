"""Recorded odds-api.net responses and the fakes the Hills tests run on.

The fixtures under tests/fixtures/odds_api_net/ were recorded live on
11 Nov 2026 (`NOW` is just after): the listing, England v Croatia's snapshot
(William Hill and Pinnacle), and two results (one final, one pending).
Armenia v Cyprus is listed with William Hill but no Pinnacle; its snapshot
came back empty, which `ARMENIA_EMPTY` stands in for."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "odds_api_net"

NOW = datetime(2026, 11, 11, 12, 40, tzinfo=timezone.utc)

ARMENIA_EMPTY = {"event_id": "4229698994", "bookmaker_as_of_ts_ms": {"williamhill": 1794399000000},
                 "stale_bookmakers": [], "target_refresh_interval_seconds": 3599,
                 "complete": True, "items": []}


def load(name: str) -> dict:
    """A recorded response, parsed fresh each call (tests may edit it)."""
    return json.loads((FIXTURES / name).read_text())


def routes() -> dict[str, dict | Exception]:
    """path -> the recorded answer, for FakeOan."""
    return {
        "/events": load("events.json"),
        "/events/1417142635/odds/snapshot": load("snapshot_1417142635.json"),
        "/events/4229698994/odds/snapshot": copy.deepcopy(ARMENIA_EMPTY),
        "/events/1595650036/results": load("results_1595650036_final.json"),
        "/events/2161612418/results": load("results_2161612418_pending.json"),
    }


class FakeOan:
    """Stands in for OddsApiNetClient: answers from `routes`, counts and
    records every call. A route holding an exception raises it; a path with no
    route raises RuntimeError("404 <path>")."""

    vendor = "odds-api.net"
    daily_budget = 5000

    def __init__(self, routes_: dict[str, dict | Exception] | None = None):
        self.routes = routes() if routes_ is None else routes_
        self.calls: list[tuple[str, dict]] = []
        self.requests_today = 0

    def budget_left(self) -> int:
        return max(0, self.daily_budget - self.requests_today)

    def get(self, path: str, **params) -> dict:
        self.calls.append((path, params))
        self.requests_today += 1
        if path not in self.routes:
            raise RuntimeError(f"404 {path}")
        answer = self.routes[path]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


class Clock:
    """A wall clock starting at NOW and a monotonic one starting at 0, moved
    together by `advance`."""

    def __init__(self, start: datetime = NOW):
        self._start = start
        self._elapsed = 0.0

    def now(self) -> datetime:
        return self._start + timedelta(seconds=self._elapsed)

    def mono(self) -> float:
        return self._elapsed

    def advance(self, seconds: float) -> None:
        self._elapsed += seconds
