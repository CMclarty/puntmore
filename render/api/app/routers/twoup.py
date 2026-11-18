"""/api/2up — the 2-Up engine, part of the 365 tool.

Thin: the logic is app/twoup/. Two read-only routes behind the `accabonus`
grant (`router`), the feed and its diagnostics; and the tracker's ingest
(`ingest_router`), `POST /api/2up/bb`, on the `ingest` grant like
`/api/paddy/saves-fair`: the Hub posts Bookie Bashing's Early Payout Tracker
as the Scraper read it. Nothing here places a bet or keeps an account.
"""
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from ..schemas import (TwoUpCandidateOut, TwoUpDiagnosticsOut, TwoUpFeedOut,
                       TwoUpTrackerIngestOut, UTCDatetime)
from ..twoup import model
from ..twoup.tracker import BET365, SIDES, TrackerRow, TrackerStore
from .saves_ingest import require_ingest_key

router = APIRouter(prefix="/2up", tags=["2up"])
ingest_router = APIRouter(prefix="/2up", tags=["2up"])

# The dials /diagnostics reports: the filters, the feed's own and the tracker's.
_DIAGNOSTIC_DIALS = (
    "twoup_min_edge", "twoup_min_price", "twoup_max_price", "twoup_min_lead_minutes",
    "twoup_tournament_ids", "twoup_feed_ttl", "twoup_horizon_hours",
    "twoup_bet365_source", "twoup_bb_min_rating", "twoup_bb_max_age_s",
    "twoup_bb_row_max_age_s",
)


def _feed(request: Request):
    return getattr(request.app.state, "twoup_feed", None)


def _tracker(request: Request) -> TrackerStore:
    """The held tracker snapshot: the feed's, or (no feed on this deploy)
    one kept on app.state so a post is still taken and diagnosable."""
    feed = _feed(request)
    if feed is not None:
        return feed.tracker
    store = getattr(request.app.state, "twoup_tracker", None)
    if store is None:
        store = request.app.state.twoup_tracker = TrackerStore()
    return store


@router.get("/feed", response_model=TwoUpFeedOut)
def get_feed(request: Request, value_only: bool = Query(default=False)):
    """The served board (refreshed by the `twoup_refresh` job and by each
    tracker post, never fetched here): every priced side of every qualifying
    fixture in the horizon (or only those in the price band and flagged by
    at least one mode), best edge first. `generated_at` is when the served
    board was priced; before the first pass (or, in the `bb` source, the
    first tracker post) the list is empty and `status.not_yet_refreshed`
    says so."""
    feed = _feed(request)
    now = datetime.now(timezone.utc)
    if feed is None:
        return TwoUpFeedOut(generated_at=now, available=False,
                            status={"degraded": True, "killed": settings.twoup_kill_switch,
                                    "cannot_price": ["ALL — ODDSPAPI_API_KEY unset"]},
                            candidates=[])
    status = {**feed.status(now), "killed": settings.twoup_kill_switch}
    if status["killed"]:
        # The global kill switch empties the feed: no rows, never the board.
        return TwoUpFeedOut(generated_at=now, available=True, status=status, candidates=[])
    at, served = feed.served(now)
    out = []
    for c in sorted(served, key=lambda c: c.edge, reverse=True):
        ok = model.passes_filters(c, feed.params) is None
        if value_only and not ok:
            continue
        out.append(TwoUpCandidateOut(
            event_id=c.event_id, event=c.event, home=c.home, away=c.away, kickoff=c.kickoff,
            competition=c.competition, selection=c.selection, side=c.side, market=c.market,
            price=c.price, p_win=None if c.p_win is None else round(c.p_win, 6),
            rescue=c.rescue, fair_price=c.fair_price, edge=round(c.edge, 6),
            ev=round(c.ev, 6), value=ok, priced_at=c.priced_at,
            sources=model.flagged_by(c, feed.params),
            bb_rating=c.bb_rating, bb_fair=c.bb_fair,
        ))
    return TwoUpFeedOut(generated_at=at or now, available=True, status=status, candidates=out)


@router.get("/diagnostics", response_model=TwoUpDiagnosticsOut)
def get_diagnostics(request: Request):
    """What the feed last saw, the tracker snapshot held, the rescue table in
    use, and the dials. No secrets."""
    feed = _feed(request)
    now = datetime.now(timezone.utc)
    table = feed.table if feed is not None else model.load_rescue_table(
        settings.twoup_rescue_table_path or None)
    if feed is not None:
        tracker = feed.tracker_status(now)
    else:
        snap = _tracker(request).snapshot()
        tracker = {"received_at": snap.received_at.isoformat() if snap else None,
                   "age_s": round(snap.age_s(now), 1) if snap else None,
                   "rows_received": snap.received if snap else 0,
                   "bet365_rows": len(snap.rows) if snap else 0,
                   "not_kept": dict(snap.dropped) if snap else {}}
    return TwoUpDiagnosticsOut(
        feed=feed.status(now) if feed is not None else {
            "degraded": True, "cannot_price": ["ALL — ODDSPAPI_API_KEY unset"]},
        rescue_table={
            "path": table.path, "loaded": table.loaded, "default": table.default,
            "key": table.key,
            "buckets": [{"lo": b.lo, "hi": b.hi, "rescue": b.rescue, "n": b.n,
                         "source": b.source} for b in table.buckets],
        },
        global_kill_switch=settings.twoup_kill_switch,
        settings={k: getattr(settings, k) for k in _DIAGNOSTIC_DIALS},
        tracker=tracker,
    )


# --- the tracker's ingest -------------------------------------------------------

class TrackerRowIn(BaseModel):
    """One row of Bookie Bashing's Early Payout Tracker, as the Scraper reads
    it. Strict: an unknown key, a missing one or a value out of range 422s
    the whole post."""
    model_config = ConfigDict(extra="forbid")

    bookmaker: str = Field(min_length=1, max_length=60)
    event: str = Field(min_length=1, max_length=200)
    home: str = Field(min_length=1, max_length=100)
    away: str = Field(min_length=1, max_length=100)
    selection: str = Field(min_length=1, max_length=100)
    side: Literal["home", "away", "draw"]
    book_odds: float = Field(gt=1, lt=1000)
    lay: float = Field(gt=1, lt=1000)
    rating: float = Field(gt=0, lt=1000)       # BB's EV rating, 100 = break-even
    n_odds: float = Field(gt=1, lt=1000)       # BB's fair odds for the 2-up bet
    kickoff: UTCDatetime                       # UTC if no offset
    updated_s: Optional[int] = Field(ge=0)     # null: the tracker did not say
    seen_at: UTCDatetime                       # when the Scraper read the row


class TrackerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prices: list[TrackerRowIn] = Field(max_length=2000)


@ingest_router.post("/bb", response_model=TwoUpTrackerIngestOut,
                    dependencies=[Depends(require_ingest_key)])
def post_tracker(request: Request, batch: TrackerIn):
    """Replace the held tracker snapshot with this post: the whole table as
    the Scraper read it. Only bet365's home and away rows are kept; the rest
    are counted. Held in memory: a restart loses it and the next post refills
    it. A post older than the one held (its newest `seen_at` earlier) is
    refused with 409, so a delayed post never puts older prices back."""
    now = datetime.now(timezone.utc)
    kept, not_kept = [], {"not bet365": 0, "draw": 0}
    for p in batch.prices:
        if p.bookmaker.strip().lower() != BET365:
            not_kept["not bet365"] += 1
        elif p.side not in SIDES:
            not_kept["draw"] += 1
        else:
            kept.append(TrackerRow(
                bookmaker=BET365, event=p.event, home=p.home, away=p.away,
                selection=p.selection, side=p.side, book_odds=p.book_odds, lay=p.lay,
                rating=p.rating, n_odds=p.n_odds, kickoff=p.kickoff, updated_s=p.updated_s,
                seen_at=p.seen_at))
    feed = _feed(request)
    if feed is not None:
        snap, why = feed.take_tracker(kept, len(batch.prices), not_kept, now)
    else:
        snap, why = _tracker(request).put(kept, len(batch.prices), not_kept, now)
    if snap is None:
        raise HTTPException(status_code=409, detail=why)
    return TwoUpTrackerIngestOut(received=len(batch.prices), kept=len(kept),
                                 not_kept=not_kept, received_at=now)
