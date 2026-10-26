"""Typed rows for the parts of the API a bet-placer reads every few seconds.

Hand-written from /api/openapi.json (`ValueSelection`, `ValueFeedOut`). The API
only ever ADDS fields within a version, so unknown keys are ignored here
rather than rejected — a newer server never breaks an older client.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any


def _when(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(frozen=True)
class ValueSelection:
    """One selection a bookmaker is offering above its fair price.

    edge = odds / fair - 1. `line` is None where the market has none and is
    always an x.5 where it has one. Times are UTC.
    """
    source: str
    bookmaker: str
    event: str
    market: str
    selection: str
    odds: float
    fair: float
    edge: float
    seen_at: datetime
    event_id: str | None = None
    home: str | None = None
    away: str | None = None
    kickoff: datetime | None = None
    sport: str | None = None
    line: float | None = None
    side: str | None = None
    basis: str | None = None
    url: str | None = None

    @classmethod
    def from_json(cls, data: dict) -> "ValueSelection":
        known = {f.name for f in fields(cls)}
        row = {k: v for k, v in data.items() if k in known}
        row["seen_at"] = _when(row.get("seen_at"))
        row["kickoff"] = _when(row.get("kickoff"))
        return cls(**row)


@dataclass(frozen=True)
class ValueFeed:
    generated_at: datetime
    sources: list[str]
    selections: list[ValueSelection]
    # A source that FAILED (not merely empty) → its reason. Check it before
    # reading an empty list as "nothing on offer".
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict) -> "ValueFeed":
        return cls(
            generated_at=_when(data["generated_at"]),
            sources=list(data.get("sources", [])),
            selections=[ValueSelection.from_json(r) for r in data.get("selections", [])],
            errors=dict(data.get("errors") or {}),
        )
