"""POST /api/paddy/saves-fair — fair goalkeeper-saves prices from the local
Bookie Bashing scraper (scraper/, target parser `bb_saves`).

Key-guarded rather than session-guarded, like /state: the caller is a script
on Callum's PC, not a browser. It writes to a bounded, expiring in-memory store
(cards/saves.py) and nothing else — no database, no vendor call.
"""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..cards.most import PaddyMost, store as store_most
from ..cards.saves import FairSaves, complete, store, _parse_time
from ..schemas import IngestOut
from ..security import require_key_grant

router = APIRouter()


# The `ingest` grant: SAVES_INGEST_KEY, or an API client granted `ingest`.
require_ingest_key = require_key_grant("ingest", "saves_ingest_key")


class FairSavesIn(BaseModel):
    home: str = Field(min_length=1, max_length=80)
    away: str = Field(min_length=1, max_length=80)
    kickoff: str                       # ISO 8601; UTC if no offset
    line: float = Field(gt=0, lt=40)
    over: float | None = Field(default=None, gt=1, lt=100)
    under: float | None = Field(default=None, gt=1, lt=100)


class SavesBatchIn(BaseModel):
    prices: list[FairSavesIn] = Field(max_length=2000)


@router.post("/saves-fair", response_model=IngestOut, dependencies=[Depends(require_ingest_key)])
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


class PaddyMostIn(BaseModel):
    home: str = Field(min_length=1, max_length=80)
    away: str = Field(min_length=1, max_length=80)
    kickoff: str                       # ISO 8601; UTC if no offset
    market: Literal["most_corners", "most_cards"]
    home_price: float = Field(gt=1, lt=100)
    tie_price: float = Field(gt=1, lt=100)
    away_price: float = Field(gt=1, lt=100)


class MostBatchIn(BaseModel):
    prices: list[PaddyMostIn] = Field(max_length=2000)


@router.post("/most-prices", response_model=IngestOut, dependencies=[Depends(require_ingest_key)])
def post_most_prices(batch: MostBatchIn):
    """Paddy Power's own "team to have most corners / most cards" prices, read
    off the Paddy page by the local scraper — OddsPapi does not carry them, so
    like the saves fairs they come from Callum's PC. Same key as /saves-fair.

    Payload: {"prices": [{"home", "away", "kickoff" (ISO 8601, UTC if no
    offset), "market": "most_corners" | "most_cards", "home_price",
    "tie_price", "away_price"}]}. Kept in memory for 36h and matched to the
    sweep's fixtures by team names and kickoff day (cards/most.py); a fixture
    with a price becomes value rows, one without stays a fair-only row.
    """
    kept, rejected = [], []
    for p in batch.prices:
        when = _parse_time(p.kickoff)
        if when is None:
            rejected.append({"match": f"{p.home} v {p.away}", "reason": "kickoff unreadable"})
            continue
        kept.append(PaddyMost(p.home, p.away, when, p.market,
                              p.home_price, p.tie_price, p.away_price))
    store_most(kept)
    return {"stored": len(kept), "rejected": rejected}
