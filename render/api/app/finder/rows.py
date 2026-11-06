"""Finder rows: the table `finder_rows`, the status machine, the duplicate
rule, expiry and the 30-day sweep (docs/finder-spec.md, "The row").

Status words, defined in the spec and nowhere else:
    asked   → offered | rejected | expired
    offered → placed | gone | expired
    placed, gone, rejected, expired are final.
Hermes posts `asked | offered | rejected` (upsert, idempotent on `tip_id`);
the placer posts `placed | gone` (set_status); Render sets `expired` on its
own clock (expire). A row with no `fair` is never `offered` (posted so, it
is stored `asked`; a `rejected` post stays rejected). One live bet
per (event_id or event, market, line, selection): a second row while one is
`asked` or `offered` is stored `rejected`, reason `duplicate of <tip_id>`.

Callers pass aware UTC datetimes; the table holds naive UTC.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import FinderRow

STATUSES = ("asked", "offered", "rejected", "placed", "gone", "expired")
FINAL = frozenset({"rejected", "placed", "gone", "expired"})
LIVE = ("asked", "offered")
HERMES = ("asked", "offered", "rejected")      # the words a post may carry
PLACER = ("placed", "gone")                    # the words the placer may report

# Legal moves of a re-post. Same status is an update (a new reason, a new
# price); a final row is refused before this is read.
_MOVES = {"asked": {"asked", "offered", "rejected"}, "offered": {"offered"}}

# The columns a post may set.
_FIELDS = ("origin", "sport", "bookmaker", "event", "home", "away", "event_id", "market",
           "selection", "line", "side", "odds", "fair", "advised_stake", "reason")


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def _key(event_id, event, market, line, selection) -> tuple:
    return (_norm(event_id) or _norm(event), _norm(market),
            None if line is None else float(line), _norm(selection))


def _live_twin(db: Session, row: FinderRow) -> FinderRow | None:
    """Another live row for the same bet, if there is one (the oldest)."""
    key = _key(row.event_id, row.event, row.market, row.line, row.selection)
    for other in (db.query(FinderRow).filter(FinderRow.status.in_(LIVE),
                                             FinderRow.tip_id != row.tip_id)
                  .order_by(FinderRow.created_at, FinderRow.tip_id)):
        if _key(other.event_id, other.event, other.market, other.line, other.selection) == key:
            return other
    return None


def upsert(db: Session, rows: list[dict], now: datetime) -> dict:
    """Store or update each posted row (on `tip_id`). Returns
    {"stored": n, "updated": n, "refused": [(tip_id, reason)], "duplicates": [tip_id]}.

    Refused, the row unchanged: a post against a final row ("final"), a word
    Hermes may not post ("status"), and a move the machine does not have
    ("offered → rejected"). Rows past their expiry are expired first, so a
    post arriving after kick-off finds a final row and changes nothing."""
    at = _naive(now)
    expire(db, now)
    out = {"stored": 0, "updated": 0, "refused": [], "duplicates": []}
    for posted in rows:
        tip_id = posted["tip_id"]
        status = posted.get("status")
        if status not in HERMES:
            out["refused"].append((tip_id, "status"))
            continue
        if posted.get("fair") is None and status == "offered":
            status = "asked"               # no fair price: never offered (a "no" stays rejected)
        row = db.get(FinderRow, tip_id)
        if row is not None:
            if row.status in FINAL:
                out["refused"].append((tip_id, "final"))
                continue
            if status not in _MOVES[row.status]:
                out["refused"].append((tip_id, f"{row.status} → {status}"))
                continue
            out["updated"] += 1
        else:
            row = FinderRow(tip_id=tip_id, created_at=at)
            db.add(row)
            out["stored"] += 1
        for field in _FIELDS:
            setattr(row, field, posted.get(field))
        row.kickoff = _naive(posted["kickoff"])
        expiry = _naive(posted.get("expiry"))
        row.expires_at = min(row.kickoff, expiry) if expiry is not None else row.kickoff
        row.edge = (row.odds / row.fair - 1) if row.fair else None
        row.seen_at = row.updated_at = at
        if status in LIVE:
            twin = _live_twin(db, row)
            if twin is not None and row.status == "offered":
                # An offered row cannot become rejected: refuse the re-post
                # and leave the row as it was.
                db.refresh(row)
                out["updated"] -= 1
                out["refused"].append((tip_id, f"duplicate of {twin.tip_id}"))
                continue
            if twin is not None:
                status = "rejected"
                row.reason = f"duplicate of {twin.tip_id}"
                out["duplicates"].append(tip_id)
        if status == "asked" and row.asked_at is None:
            row.asked_at = at
        if status in ("offered", "rejected") and row.status != status:
            row.answered_at = at
        row.status = status
        db.flush()
    expire(db, now)
    db.commit()
    return out


def set_status(db: Session, tip_id: str, status: str, at: datetime) -> FinderRow:
    """The placer's report: `placed` or `gone`, on an offered row only.
    KeyError: unknown tip. ValueError("status"): any other word.
    ValueError("final"): the row is final. ValueError("asked"): the row was
    never offered."""
    if status not in PLACER:
        raise ValueError("status")
    row = db.get(FinderRow, tip_id)
    if row is None:
        raise KeyError(tip_id)
    if row.status in FINAL:
        raise ValueError("final")
    if row.status != "offered":
        raise ValueError(row.status)
    row.status = status
    row.updated_at = _naive(at)
    db.commit()
    return row


def expire(db: Session, now: datetime) -> int:
    """Live rows whose `expires_at` has come → `expired`. Returns how many."""
    n = (db.query(FinderRow)
         .filter(FinderRow.status.in_(LIVE), FinderRow.expires_at <= _naive(now))
         .update({FinderRow.status: "expired", FinderRow.updated_at: _naive(now)},
                 synchronize_session="fetch"))
    db.commit()
    return n


def feed(db: Session, now: datetime) -> list[FinderRow]:
    """What the placer reads: offered rows not yet expired, soonest kick-off first."""
    return (db.query(FinderRow)
            .filter(FinderRow.status == "offered", FinderRow.expires_at > _naive(now))
            .order_by(FinderRow.kickoff, FinderRow.tip_id).all())


def board(db: Session, since: datetime) -> list[FinderRow]:
    """Every row created since `since`, all statuses, newest first (the page)."""
    return (db.query(FinderRow).filter(FinderRow.created_at >= _naive(since))
            .order_by(FinderRow.created_at.desc(), FinderRow.tip_id).all())


def sweep(db: Session, now: datetime, days: int = 30) -> int:
    """Delete rows created more than `days` ago. Returns how many."""
    n = (db.query(FinderRow)
         .filter(FinderRow.created_at < _naive(now) - timedelta(days=days))
         .delete(synchronize_session=False))
    db.commit()
    return n
