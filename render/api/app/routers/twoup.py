"""/api/2up — the 2-Up engine, part of the 365 tool (`accabonus` grant).

Thin: the logic is app/twoup/. Two read-only routes, the feed and its
diagnostics. Nothing here places a bet, keeps an account or talks to a Hub.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request

from ..config import settings
from ..schemas import TwoUpCandidateOut, TwoUpDiagnosticsOut, TwoUpFeedOut
from ..twoup import model

router = APIRouter(prefix="/2up", tags=["2up"])

# The dials /diagnostics reports: the four filters plus the feed's own three.
_DIAGNOSTIC_DIALS = (
    "twoup_min_edge", "twoup_min_price", "twoup_max_price", "twoup_min_lead_minutes",
    "twoup_tournament_ids", "twoup_feed_ttl", "twoup_horizon_hours",
)


def _feed(request: Request):
    return getattr(request.app.state, "twoup_feed", None)


@router.get("/feed", response_model=TwoUpFeedOut)
def get_feed(request: Request, value_only: bool = Query(default=False)):
    """Every priced side of every qualifying fixture in the horizon (or only
    those passing the price band and edge floor), best edge first."""
    feed = _feed(request)
    now = datetime.now(timezone.utc)
    if feed is None:
        return TwoUpFeedOut(generated_at=now, available=False,
                            status={"degraded": True,
                                    "cannot_price": ["ALL — ODDSPAPI_API_KEY unset"]},
                            candidates=[])
    rows = sorted(feed.candidates(now), key=lambda c: c.edge, reverse=True)
    out = []
    for c in rows:
        ok = model.passes_filters(c, feed.params) is None
        if value_only and not ok:
            continue
        out.append(TwoUpCandidateOut(
            event_id=c.event_id, event=c.event, home=c.home, away=c.away, kickoff=c.kickoff,
            competition=c.competition, selection=c.selection, side=c.side, market=c.market,
            price=c.price, p_win=round(c.p_win, 6), rescue=c.rescue,
            fair_price=c.fair_price, edge=round(c.edge, 6), ev=round(c.ev, 6), value=ok,
            priced_at=c.priced_at,
        ))
    return TwoUpFeedOut(generated_at=now, available=True, status=feed.status(), candidates=out)


@router.get("/diagnostics", response_model=TwoUpDiagnosticsOut)
def get_diagnostics(request: Request):
    """What the feed last saw, the rescue table in use, and the dials. No secrets."""
    feed = _feed(request)
    table = feed.table if feed is not None else model.load_rescue_table(
        settings.twoup_rescue_table_path or None)
    return TwoUpDiagnosticsOut(
        feed=feed.status() if feed is not None else {
            "degraded": True, "cannot_price": ["ALL — ODDSPAPI_API_KEY unset"]},
        rescue_table={
            "path": table.path, "loaded": table.loaded, "default": table.default,
            "key": table.key,
            "buckets": [{"lo": b.lo, "hi": b.hi, "rescue": b.rescue, "n": b.n,
                         "source": b.source} for b in table.buckets],
        },
        global_kill_switch=settings.twoup_kill_switch,
        settings={k: getattr(settings, k) for k in _DIAGNOSTIC_DIALS},
    )
