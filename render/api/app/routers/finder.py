"""/api/finder — Finder, Callum's private Bet Finder (docs/finder-spec.md).

Thin: the logic is app/finder/rows.py. Two POSTs on the ingest key (rows from
Hermes on the Hub; placed|gone from the placer) and two GETs on the `finder`
grant (the placer's feed, the me board). No router-level grant: each route
carries its own guard, because the writers and the readers hold different
keys. A session never writes here, and nothing here prices a tip, loads a
page or places a bet.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..finder import rows
from ..schemas import (
    FinderBoardOut, FinderFeedOut, FinderPostOut, FinderRowOut, FinderRowsIn, FinderStatusIn,
)
from ..security import require_tool
from .saves_ingest import require_ingest_key

router = APIRouter(prefix="/finder", tags=["finder"])


@router.post("/rows", response_model=FinderPostOut, dependencies=[Depends(require_ingest_key)])
def post_rows(body: FinderRowsIn, db: Session = Depends(get_db)):
    """Hermes's verdicts: upsert on `tip_id`. A post against a final row, or a
    move the status machine does not have, is listed under `refused` and
    changes nothing; a second live row for the same bet is stored `rejected`
    and listed under `duplicates`."""
    now = datetime.now(timezone.utc)
    return rows.upsert(db, [r.model_dump() for r in body.rows], now)


@router.post("/rows/{tip_id}/status", response_model=FinderRowOut,
             dependencies=[Depends(require_ingest_key)])
def post_status(tip_id: str, body: FinderStatusIn, db: Session = Depends(get_db)):
    """The placer's report on an offered row: `placed` or `gone`. 404 unknown
    tip, 409 a final row (or one never offered), 422 any other word."""
    try:
        return rows.set_status(db, tip_id, body.status, body.at)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown tip_id")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/feed", response_model=FinderFeedOut, dependencies=[Depends(require_tool("finder"))])
def get_feed(db: Session = Depends(get_db)):
    """What the placer reads: `offered` rows not yet expired, soonest kick-off first."""
    now = datetime.now(timezone.utc)
    return {"generated_at": now, "rows": rows.feed(db, now)}


@router.get("/board", response_model=FinderBoardOut, dependencies=[Depends(require_tool("finder"))])
def get_board(days: int = Query(default=3, ge=1, le=30), db: Session = Depends(get_db)):
    """The me page: every row of the last `days` days, all statuses, newest first."""
    now = datetime.now(timezone.utc)
    return {"generated_at": now, "rows": rows.board(db, now - timedelta(days=days))}
