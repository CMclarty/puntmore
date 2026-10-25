"""AW 26 value snapshots: what value was on offer, kept.

The Paddy sweep (scheduler.sweep_cards) prices every line every two minutes
and, until this, kept none of it. `record` stores the sweep's OWN rows — no
vendor call of its own, ever (the OddsPapi cap is 3,000 requests a day) — at
most every AW26_SNAPSHOT_MINUTES, so a slip can be married to the line it came
from (aw26/marry.py) and the day's "value on offer" reported.

Rows are anything shaped like cards/value.py::CardValueRow. Another source
(coupon legs with value, `/market` rows, Bookie Bashing) posts into the same
two tables under its own `source`.

The rows are what the sweep returns, which is already filtered at
CARDS_MIN_EDGE (default -2%): a line priced worse than that is not on file.
"""
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..models import ValueLine, ValueSnapshot
from ..time_utils import utcnow

KEEP_DAYS = 30
SOURCES = ("paddy", "coupon", "bb", "market")

# source -> when it was last written, this process. A restart writes one
# snapshot straight away, which is harmless; the DB is not asked every tick.
_last: dict[str, datetime] = {}


def due(source: str, now: datetime | None = None) -> bool:
    """Whether `source` may write a snapshot now (the throttle)."""
    now = now or utcnow()
    last = _last.get(source)
    return last is None or now - last >= timedelta(minutes=settings.aw26_snapshot_minutes)


def _kickoff(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def _line(row) -> ValueLine:
    from ..routers.cards import describe, market_name   # the Paddy page's names
    key = row.market_key
    team = None
    if key.startswith("team_"):
        team = "home" if key.endswith("_home") else "away" if key.endswith("_away") else None
    return ValueLine(
        event_id=str(row.event_id) if row.event_id is not None else None,
        home=row.home_team, away=row.away_team,
        kickoff=_kickoff(row.commence_time),
        sport_key=getattr(row, "sport_key", None),
        market=market_name(key), team=team,
        line=row.line, side=(row.side or "").lower() or None,
        pick=describe(row),
        offered_odds=row.paddy_odds, fair_odds=row.fair_odds,
        edge=row.edge, basis=getattr(row, "basis", None),
    )


def record(db, rows, source: str = "paddy", now: datetime | None = None) -> ValueSnapshot:
    """Store one snapshot of `rows` and prune what is past KEEP_DAYS. Commits."""
    now = now or utcnow()
    lines = [_line(r) for r in rows]
    positive = [ln.edge for ln in lines if ln.edge >= 0]
    snap = ValueSnapshot(
        at=now, source=source, n_lines=len(lines), n_value=len(positive),
        best_edge=max((ln.edge for ln in lines), default=None),
        sum_edge_positive=round(sum(positive), 6),
    )
    snap.lines = lines
    db.add(snap)
    db.flush()
    prune(db, now=now, commit=False)
    db.commit()
    _last[source] = now
    return snap


def latest(db, source: str | None = "paddy") -> ValueSnapshot | None:
    q = db.query(ValueSnapshot)
    if source:
        q = q.filter(ValueSnapshot.source == source)
    return q.order_by(ValueSnapshot.at.desc(), ValueSnapshot.id.desc()).first()


def recent(db, hours: float = 24, now: datetime | None = None,
           source: str | None = None) -> list[ValueSnapshot]:
    """Snapshots in the last `hours`, oldest first."""
    cutoff = (now or utcnow()) - timedelta(hours=hours)
    q = db.query(ValueSnapshot).filter(ValueSnapshot.at >= cutoff)
    if source:
        q = q.filter(ValueSnapshot.source == source)
    return q.order_by(ValueSnapshot.at, ValueSnapshot.id).all()


def prune(db, keep_days: int = KEEP_DAYS, now: datetime | None = None,
          commit: bool = True) -> int:
    """Delete snapshots older than `keep_days`, lines first (SQLite does not
    cascade without the foreign-key pragma). Returns snapshots deleted."""
    cutoff = (now or utcnow()) - timedelta(days=keep_days)
    old = db.query(ValueSnapshot.id).filter(ValueSnapshot.at < cutoff)
    db.query(ValueLine).filter(ValueLine.snapshot_id.in_(old.scalar_subquery())) \
        .delete(synchronize_session=False)
    n = db.query(ValueSnapshot).filter(ValueSnapshot.at < cutoff) \
        .delete(synchronize_session=False)
    if commit:
        db.commit()
    return n


def snapshot_sweep(rows, session_factory, now: datetime | None = None) -> bool:
    """The sweep's hook: record `rows` under 'paddy' if the throttle allows.
    Own session. Returns whether a snapshot was written. Raises on failure —
    the caller (sweep_cards) catches, so the sweep is never broken by it."""
    if not due("paddy", now):
        return False
    db = session_factory()
    try:
        record(db, rows, "paddy", now)
    finally:
        db.close()
    return True
