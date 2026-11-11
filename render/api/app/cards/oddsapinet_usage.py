"""odds-api.net's credits used today, kept in SQLite so a deploy or a restart
does not zero the daily cap (final review, Important 3). One row, id 1:
`(utc_day, count)`. `OddsApiNetClient` loads it once when built and saves it
after every counted request; a failure here is the client's to log, never a
reason to stop a fetch."""
from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from ..models import OddsApiNetUsage

_ROW = 1


class SqlUsageStore:
    """The client's `store`: `load() -> (utc_day, count) | None` and
    `save(utc_day, count)`. Each call opens and closes its own session, so it
    is safe from the request threads and the background pass alike."""

    def __init__(self, session_factory: Callable[[], Session] | None = None):
        if session_factory is None:
            from ..database import SessionLocal
            session_factory = SessionLocal
        self._session = session_factory

    def load(self) -> tuple[str, int] | None:
        with self._session() as db:
            row = db.get(OddsApiNetUsage, _ROW)
            return (row.utc_day, int(row.count)) if row is not None else None

    def save(self, utc_day: str, count: int) -> None:
        with self._session() as db:
            row = db.get(OddsApiNetUsage, _ROW)
            if row is None:
                db.add(OddsApiNetUsage(id=_ROW, utc_day=utc_day, count=count))
            else:
                row.utc_day, row.count = utc_day, count
            db.commit()
