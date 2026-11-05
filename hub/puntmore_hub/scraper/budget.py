"""Request budget: ~60 pages/hour, ~400/day, random pauses. A hard rule from the PP handover."""
from __future__ import annotations

import random
import time
from datetime import timedelta

from ..store import Store, utcnow


class RequestBudget:
    def __init__(self, store: Store, per_hour: int = 60, per_day: int = 400):
        self.store = store
        self.per_hour = per_hour
        self.per_day = per_day

    def used(self) -> tuple[int, int]:
        now = utcnow()
        return (self.store.page_loads_since(now - timedelta(hours=1)),
                self.store.page_loads_since(now - timedelta(days=1)))

    def remaining(self) -> tuple[int, int]:
        h, d = self.used()
        return max(0, self.per_hour - h), max(0, self.per_day - d)

    def allow(self) -> bool:
        h, d = self.remaining()
        return h > 0 and d > 0

    def record(self, source: str, url: str, status: int | None) -> None:
        self.store.log_page_load(source, url, status)


def human_pause(lo: float = 1.5, hi: float = 4.0) -> None:
    time.sleep(random.uniform(lo, hi))
