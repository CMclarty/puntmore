"""POST /api/paddy/saves-fair — fair goalkeeper-saves prices from the local
Bookie Bashing script (local/bookiebashing_saves.py).

Key-guarded rather than session-guarded, like /state: the caller is a script
on Callum's PC, not a browser. It writes to a bounded, expiring in-memory store
(cards/saves.py) and nothing else — no database, no vendor call.
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..cards.saves import FairSaves, complete, store, _parse_time
from ..config import settings

router = APIRouter()


def require_ingest_key(x_api_key: str | None = Header(default=None)):
    if not settings.saves_ingest_key:
        raise HTTPException(status_code=503, detail="SAVES_INGEST_KEY not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.saves_ingest_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


class FairSavesIn(BaseModel):
    home: str = Field(min_length=1, max_length=80)
    away: str = Field(min_length=1, max_length=80)
    kickoff: str                       # ISO 8601; UTC if no offset
    line: float = Field(gt=0, lt=40)
    over: float | None = Field(default=None, gt=1, lt=100)
    under: float | None = Field(default=None, gt=1, lt=100)


class SavesBatchIn(BaseModel):
    prices: list[FairSavesIn] = Field(max_length=2000)


@router.post("/saves-fair", dependencies=[Depends(require_ingest_key)])
def post_saves_fair(batch: SavesBatchIn):
    """Store a batch; report what was kept and why anything was not."""
    kept, rejected = [], []
    for p in batch.prices:
        when = _parse_time(p.kickoff)
        pair = complete(p.over, p.under)
        if when is None:
            rejected.append({"match": f"{p.home} v {p.away}", "reason": "kickoff unreadable"})
        elif pair is None:
            rejected.append({"match": f"{p.home} v {p.away}", "line": p.line,
                             "reason": "no fair price, or over/under not a fair pair"})
        else:
            kept.append(FairSaves(p.home, p.away, when, p.line, *pair))
    store(kept)
    return {"stored": len(kept), "rejected": rejected}
