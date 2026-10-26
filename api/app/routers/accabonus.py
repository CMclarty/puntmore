import logging

from fastapi import APIRouter, Query, Request

from ..accabonus.arbs import compute_arbs
# The feed lives in accabonus/feed.py; re-exported so existing imports work.
from ..accabonus.feed import (  # noqa: F401
    LAST_PULL, _fetch_events, _sport_keys, build_feed, eligible_keys,
)
from ..config import settings
from ..schemas import AccaBonusFeedItem, ArbOut, DiagnosticsOut, OpenModel

logger = logging.getLogger("uvicorn.error")

router = APIRouter()


@router.get("/accabonus/eligible-keys", response_model=OpenModel)
def get_eligible_keys(request: Request):
    """What this tool counts as boost-eligible, for checking against bet365."""
    client = request.app.state.arbs_client
    if client is None:
        return {"source": "no client — ODDS_API_KEY unset",
                "accepted": [], "count": 0}
    return eligible_keys(client)


# Legacy /tennis/* aliases kept so PWA clients with a cached pre-rename shell
# don't 404 until their service worker updates. Hidden from the schema.
@router.get("/accabonus/arbs", response_model=list[ArbOut])
@router.get("/tennis/arbs", response_model=list[ArbOut], include_in_schema=False)
def get_accabonus_arbs(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    if min_edge is None:
        min_edge = settings.arb_min_edge
    client = request.app.state.arbs_client
    if client is None:
        return []
    return compute_arbs(_fetch_events(client), min_edge)


@router.get("/accabonus/feed", response_model=list[AccaBonusFeedItem])
@router.get("/tennis/feed", response_model=list[AccaBonusFeedItem], include_in_schema=False)
def get_accabonus_feed(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    """Simplified AccaBonus feed: one card per event showing the best-value side."""
    if min_edge is None:
        min_edge = settings.arb_min_edge
    return build_feed(request.app.state.arbs_client, min_edge)


@router.get("/accabonus/diagnostics", response_model=DiagnosticsOut)
def get_accabonus_diagnostics(request: Request):
    """Why the AccaBonus screen looks the way it does.

    Blank has several causes and they are indistinguishable from a feed with no
    rows on it: no key, no eligible sports in season, the vendor returning no
    events, bet365 absent from the response, or nothing clearing the edge
    threshold. That LAST one is the tool working correctly and must not read as
    a fault.
    """
    client = request.app.state.arbs_client
    pull = dict(LAST_PULL)

    if client is None:
        verdict = "No client — ODDS_API_KEY is not set."
    elif not pull:
        verdict = "No pull has run yet. Open the feed to trigger one."
    elif pull.get("keys_read", 0) == 0:
        verdict = (
            f"No eligible sport key was read ({pull.get('keys_skipped', 0)} "
            f"skipped on quota, {pull.get('keys_failed', 0)} failed). Check "
            f"/accabonus/eligible-keys — nothing eligible may be in season."
        )
    elif pull.get("events", 0) == 0:
        verdict = (
            f"{pull.get('keys_read')} key(s) read and the vendor returned NO "
            f"events. Out of season, or no fixtures posted yet."
        )
    elif pull.get("events_with_both_books", 0) == 0:
        b365, pin = pull.get("bet365_events", 0), pull.get("pinnacle_events", 0)
        seen = list(pull.get("bookmakers_seen", {}))
        if b365 == 0 and pin == 0:
            verdict = (
                f"{pull.get('events')} event(s) returned and NEITHER bet365 nor "
                f"Pinnacle is among them. Books present: {seen or 'none'}. "
                f"If the list is non-empty, the request is reaching the vendor "
                f"and these two are simply not on this plan or not named the "
                f"way this code expects."
            )
        elif b365 == 0:
            verdict = (
                f"Pinnacle is on {pin} event(s) but bet365 on NONE. Books "
                f"present: {seen}. bet365 is a `uk` bookmaker — either it is "
                f"not carried on this plan, or it does not price these sports."
            )
        elif pin == 0:
            verdict = (
                f"bet365 is on {b365} event(s) but Pinnacle on NONE. Books "
                f"present: {seen}. Pinnacle is the fair-odds reference, so "
                f"without it there is nothing to measure value against."
            )
        else:
            verdict = (
                f"bet365 is on {b365} event(s) and Pinnacle on {pin}, but never "
                f"the SAME event. The two books cover different fixtures here."
            )
    else:
        verdict = (
            f"WORKING: {pull.get('events_with_both_books')} event(s) carry both "
            f"books and were priced. Any blank screen is the edge threshold "
            f"doing its job — lower it on the page to see near-value rows."
        )

    return {"verdict": verdict, "pull": pull,
            "min_edge": settings.arb_min_edge}


@router.get("/accabonus/probe-books", response_model=OpenModel)
def probe_books(request: Request, sport: str = "soccer_epl", region: str = "uk"):
    """Which bookmakers this plan actually carries for one sport and region.

    COSTS CREDITS — one call, on demand, never on a schedule. It exists because
    the question "is bet365 on this plan at all" cannot be answered any other
    way from here: the vendor's own docs are egress-blocked, and every automatic
    pull names the two books it wants, so a book that is absent is
    indistinguishable from a book that is filtered.

    This asks by REGION with no bookmaker filter, so the response is everything
    the plan carries. If bet365 is missing from that list, no request tweak will
    produce it and the tool needs a different reference or a different plan.
    """
    client = request.app.state.arbs_client
    if client is None:
        return {"error": "ODDS_API_KEY is not set"}

    try:
        # ttl=0 forces a fresh call: a cached answer would defeat the point.
        events = client.fetch(sport, "h2h", ttl=0, regions=region)
    except Exception as exc:
        return {"error": str(exc), "sport": sport, "region": region}

    census: dict[str, int] = {}
    for e in events:
        for bm in e.get("bookmakers", []):
            key = bm.get("key")
            if key:
                census[key] = census.get(key, 0) + 1

    found = sorted(census.items(), key=lambda kv: -kv[1])
    return {
        "sport": sport,
        "region_requested": region,
        "events": len(events),
        "bookmakers": dict(found),
        "bet365_present": "bet365" in census,
        "pinnacle_present": "pinnacle" in census,
        "quota_remaining": getattr(client, "quota_remaining", None),
        "last_call_cost": getattr(client, "last_call_cost", None),
        "note": (
            "No bookmaker filter was sent, so this is everything the plan "
            "carries for this sport. bet365 absent here means absent full stop."
        ),
    }
