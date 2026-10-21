"""POST /api/aw26/fairs — the day's fair prices from Bookie Bashing's Group
Tracker, sent by the local script (local/bookiebashing_tracker.py).

Key-guarded like /paddy/saves-fair, and with the SAME key: both come from the
one Bookie Bashing script machine, behind Callum's login, which never reaches
the server. Every slip still waiting on a fair price is priced on arrival.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..aw26.legs import classify
from ..aw26.service import FAIRS_KEEP_DAYS, parse_when, price_slip, recent_fairs
from ..database import get_db
from ..models import AwFair, AwSlip
from ..time_utils import utcnow
from .saves_ingest import require_ingest_key

router = APIRouter(prefix="/aw26")


class FairIn(BaseModel):
    home: str = Field(min_length=1, max_length=80)
    away: str = Field(min_length=1, max_length=80)
    kickoff: str | None = None           # ISO 8601 UTC, or as printed (London)
    market: str | None = Field(default=None, max_length=120)
    selection: str | None = Field(default=None, max_length=120)
    fair_odds: float = Field(gt=1, lt=1000)


class FairsIn(BaseModel):
    fairs: list[FairIn] = Field(max_length=5000)


@router.post("/fairs", dependencies=[Depends(require_ingest_key)])
def post_fairs(batch: FairsIn, db: Session = Depends(get_db)):
    now = utcnow()
    for f in batch.fairs:
        kind, line, side = classify(f.market, f.selection)
        db.add(AwFair(received_at=now, home=f.home, away=f.away,
                      kickoff=parse_when(f.kickoff, now) if f.kickoff else None,
                      market=f.market, selection=f.selection,
                      kind=kind, line=line, side=side, fair_odds=f.fair_odds))
    # Bounded: two weeks of scrapes, never every one ever sent.
    db.query(AwFair).filter(AwFair.received_at < now - timedelta(days=FAIRS_KEEP_DAYS)).delete()
    db.flush()
    fairs = recent_fairs(db, now)
    waiting = db.query(AwSlip).filter(AwSlip.ev.is_(None)).all()
    for slip in waiting:
        price_slip(slip, fairs)
    db.commit()
    return {"stored": len(batch.fairs),
            "priced": sum(1 for s in waiting if s.ev is not None),
            "still_unpriced": sum(1 for s in waiting if s.ev is None)}
