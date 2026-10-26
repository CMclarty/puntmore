"""The 365 acca-boost value feed — importable without FastAPI.

Moved out of routers/accabonus.py (Oct 2026) so the router, /state, the
scheduler and the cross-tool value feed (app/value/) call one implementation.
Same behaviour as before the move.
"""
import logging

from ..config import settings
from ..schemas import AccaBonusFeedItem
from .arbs import FALLBACK_SPORT_KEYS, compute_arbs, select_accabonus_keys

logger = logging.getLogger("uvicorn.error")

# What the last pull saw, for /accabonus/diagnostics. A blank feed has several
# causes that look identical from outside, and one of them is the tool working.
LAST_PULL: dict = {}

def _sport_keys(client) -> list[str]:
    """Active Acca Boost-eligible league keys, discovered from the free sports
    list; or the ACCABONUS_SPORT_KEYS override (quota testing)."""
    if settings.accabonus_sport_keys:
        return [k.strip() for k in settings.accabonus_sport_keys.split(",") if k.strip()]
    try:
        return select_accabonus_keys(client.list_sports())
    except Exception:
        return FALLBACK_SPORT_KEYS


def _fetch_events(client) -> list[dict]:
    """Bet365 + Pinnacle h2h for every eligible sport key.

    PINNACLE IS THE MEASURING STICK, NOT A SECOND BOOK TO BET AT. The Acca Boost
    is a bet365 promotion, so bet365 is the only place any of this is placed;
    de-vigged Pinnacle is simply what "fair odds" means, exactly as it is for
    the coupon tracker and the cards tool.

    Reads on `accabonus_cache_ttl` rather than the shared 24-hour default, so
    the refresh rate here can be changed without moving every other consumer.
    """
    events: list[dict] = []
    fetched = skipped = failed = 0

    for sport_key in _sport_keys(client):
        # The same reserve the cards sweep respects. Without it a short TTL
        # here could quietly eat the allowance the coupon fallback and the
        # goalscorer scans depend on.
        remaining = getattr(client, "quota_remaining", None)
        if remaining is not None and remaining <= settings.cards_quota_reserve:
            skipped += 1
            continue
        try:
            # Explicit bookmakers: bet365 is UK-region (absent from the default
            # eu region) and naming them costs 1 credit vs region pricing.
            events.extend(client.fetch(
                sport_key, "h2h", bookmakers="bet365,pinnacle",
                ttl=settings.accabonus_cache_ttl,
            ))
            fetched += 1
        except Exception as exc:
            # Counted and logged: a silently swallowed failure here is what let
            # the cards client return nothing for months.
            failed += 1
            logger.error("accabonus: %s fetch failed: %s", sport_key, exc)

    if skipped:
        logger.error(
            "accabonus: skipped %d sport key(s) — TheOddsApi quota is at or "
            "below the %d reserve.", skipped, settings.cards_quota_reserve,
        )
    # WHICH bookmakers actually came back, counted. "Neither book present" and
    # "one book present" are completely different problems — the first is the
    # request or the plan, the second is coverage of that sport — and a
    # both-books boolean cannot tell them apart. Learned the hard way: a blank
    # feed was diagnosed as a region filter when the census would have named
    # the real culprit immediately.
    census: dict[str, int] = {}
    with_both = 0
    for e in events:
        keys = {bm.get("key") for bm in e.get("bookmakers", [])}
        for key in keys:
            if key:
                census[key] = census.get(key, 0) + 1
        if keys >= {"bet365", "pinnacle"}:
            with_both += 1

    LAST_PULL.update({
        "keys_read": fetched, "keys_failed": failed, "keys_skipped": skipped,
        "events": len(events), "events_with_both_books": with_both,
        "ttl_seconds": settings.accabonus_cache_ttl,
        "bet365_events": census.get("bet365", 0),
        "pinnacle_events": census.get("pinnacle", 0),
        # Every bookmaker the vendor returned, commonest first. If bet365 is
        # absent from this entirely, no request tweak will conjure it.
        "bookmakers_seen": dict(sorted(census.items(), key=lambda kv: -kv[1])),
    })
    log = logger.info if not failed else logger.warning
    log(
        "accabonus pull: %d key(s) read, %d failed, %d skipped, %d event(s), "
        "%d with both books, ttl=%ss",
        fetched, failed, skipped, len(events), with_both,
        settings.accabonus_cache_ttl,
    )
    return events


def eligible_keys(client) -> dict:
    """The sport keys this tool treats as Acca Boost-eligible, and why.

    Exists to be CHECKED AGAINST BET365'S PUBLISHED TERMS by eye. The
    eligibility list in accabonus/arbs.py is a reading of those terms from June
    2026 and nothing re-reads them — offers change, TheOddsApi renames groups,
    and both failures are silent: a renamed group quietly drops every key under
    it, and a widened group quietly adds fixtures the boost does not cover.

    Reports what was ACCEPTED and what was REJECTED, because over-inclusion
    (rows for fixtures that get no boost) and under-inclusion (a whole sport
    vanishing) look identical from a feed that simply has fewer rows on it.
    """
    from .arbs import _key_eligible, _OUTRIGHT_RE, _EXCLUDED_RE

    if settings.accabonus_sport_keys:
        pinned = [k.strip() for k in settings.accabonus_sport_keys.split(",") if k.strip()]
        return {"source": "ACCABONUS_SPORT_KEYS override", "accepted": pinned,
                "rejected": [], "count": len(pinned)}

    try:
        sports = client.list_sports()          # free endpoint, no quota cost
    except Exception as exc:
        return {"source": "discovery failed", "error": str(exc),
                "accepted": [], "rejected": [], "count": 0}

    accepted, rejected = [], []
    for sport in sports:
        key, group = sport.get("key", ""), sport.get("group", "")
        if not sport.get("active", True) or sport.get("has_outrights", False):
            continue
        entry = {"key": key, "group": group, "title": sport.get("title", "")}
        if _key_eligible(key, group):
            accepted.append(entry)
        elif group in ("Soccer", "Tennis", "Mixed Martial Arts", "Basketball",
                       "American Football", "Baseball", "Ice Hockey"):
            # Only near-misses are listed. Every darts and snooker key on the
            # platform would bury the thing this is for.
            rejected.append(entry)

    return {
        "source": "discovered from /v4/sports (free)",
        "count": len(accepted),
        "accepted": sorted(accepted, key=lambda e: e["key"]),
        "rejected_near_misses": sorted(rejected, key=lambda e: e["key"]),
        "note": (
            "Compare `accepted` against bet365's Acca Boost terms. Tennis and "
            "MMA are taken as WHOLE GROUPS, so anything TheOddsApi files under "
            "them is included whether or not the offer covers it. Soccer is "
            "excluded by an explicit decision (June 2026), not by the terms — "
            "the boost does cover it."
        ),
    }


def build_feed(client, min_edge: float) -> list[AccaBonusFeedItem]:
    """One card per event showing the best-value side. Shared with /state."""
    if client is None:
        return []
    arbs = compute_arbs(_fetch_events(client), min_edge)
    feed = []
    for arb in arbs:
        picks = [
            (arb.home_team, arb.bet365_home, arb.edge_home),
            (arb.away_team, arb.bet365_away, arb.edge_away),
        ]
        if arb.edge_draw is not None:
            picks.append(("Draw", arb.bet365_draw, arb.edge_draw))
        pick, odds, value = max(picks, key=lambda p: p[2])
        fair = {arb.home_team: arb.fair_home, arb.away_team: arb.fair_away,
                "Draw": arb.fair_draw}.get(pick)
        feed.append(AccaBonusFeedItem(
            match=f"{arb.home_team} vs {arb.away_team}",
            sport=arb.sport_title,
            pick=pick,
            odds=odds,
            value=value,
            event_id=arb.event_id,
            home=arb.home_team,
            away=arb.away_team,
            commence_time=arb.commence_time,
            sport_key=arb.sport_key,
            fair=fair,
        ))
    return feed
