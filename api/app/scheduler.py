"""APScheduler setup: 10-min odds refresh + daily 30-day cleanup + the daily
first-goalscorer watchlist that schedules each fixture's pre-kickoff scan."""
import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR

from .config import settings
from .notifications.routes import notify_rows
from .coupon_images import delete_coupon_image
from .database import SessionLocal
from .models import Coupon, Leg
from .comparison import value_tier
from .cards.client import merge_pinnacle
from .goalscorers.service import (
    build_watchlist,
    cleanup_stale_alerts,
    scan_and_store,
)
from .pricing.legs import price_legs
from .time_utils import utcnow

logger = logging.getLogger("uvicorn.error")


def cleanup_old_coupons(db=None, *, cutoff_days: int = 30, notifier=None) -> int:
    """Delete coupons older than cutoff_days, including their image files.

    Returns the number of coupons deleted.
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True

    cutoff = utcnow() - timedelta(days=cutoff_days)
    try:
        old = db.query(Coupon).filter(Coupon.created_at < cutoff).all()
        count = len(old)
        files_removed = 0
        for c in old:
            # One photo of a sheet backs a coupon per market block on it, so the
            # file goes with the LAST row that points at it, not the first.
            if delete_coupon_image(db, c):
                files_removed += 1
            db.delete(c)
            # Flushed per coupon so the next one's reference count sees this
            # deletion — otherwise the last sibling still counts the others and
            # the image is never removed at all.
            db.flush()
        db.commit()
    finally:
        if close:
            db.close()

    if notifier and count:
        notifier.send(
            f":wastebasket: Cleanup: removed {count} coupon(s) older than "
            f"{cutoff_days} days ({files_removed} image file(s) deleted)."
        )
    return count


def cleanup_kicked_off_legs(db=None, *, grace_minutes: int | None = None) -> int:
    """Remove selections whose fixture has kicked off.

    A coupon covers a WINDOW, so a Monday fixture on a Mon–Wed coupon stays
    inside a live window for two more days: the coupon reads "active" and the
    row sits in the list long after the match finished, showing "live" because
    its kickoff is in the past. The window says the coupon is current; only the
    kickoff says whether a fixture is still bettable.

    The grace period exists because a kickoff read off a coupon is a printed
    time, not a feed: it can be a few minutes out, and a price is occasionally
    still worth taking right on the whistle. Past that, the row is history.

    A leg with NO kickoff is never touched — unknown is not the same as past,
    and an unparsed kickoff is usually a row worth correcting by hand.

    Returns the number of legs removed.
    """
    grace = settings.leg_kickoff_grace_minutes if grace_minutes is None else grace_minutes
    close = False
    if db is None:
        db = SessionLocal()
        close = True

    cutoff = utcnow() - timedelta(minutes=grace)
    try:
        stale = (
            db.query(Leg)
            .filter(Leg.kickoff.isnot(None), Leg.kickoff < cutoff)
            .all()
        )
        for leg in stale:
            db.delete(leg)
        db.commit()
    finally:
        if close:
            db.close()

    if stale:
        # Logged, never notified: this runs every 10 minutes and would fire a
        # Discord message after every match day. The interesting event is a
        # value bet appearing, not a finished fixture leaving.
        logger.info(
            "Cleared %d leg(s) more than %d min past kickoff", len(stale), grace,
        )
    return len(stale)


def refresh_all_live_coupons(odds_provider, notifier=None, db=None) -> int:
    """Refresh fair odds for all legs in live coupons.

    Notifies only legs whose value tier is new or changed since the last
    notification (tracked in Leg.notified_tier) — the 10-min loop must not
    re-alert the same value bets every cycle.

    Returns the total number of legs updated.
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    total_updated = 0
    try:
        coupons = db.query(Coupon).filter(Coupon.status == "confirmed").all()
        for coupon in coupons:
            legs = db.query(Leg).filter(Leg.coupon_id == coupon.id).all()
            if not legs:
                continue

            try:
                priced = price_legs(legs, odds_provider)
            except Exception:
                continue

            total_updated += len(priced)
            value_legs = []
            for leg in priced:
                tier = value_tier(leg.value)
                if tier and tier != leg.notified_tier:
                    value_legs.append(leg)
                leg.notified_tier = tier

            db.commit()

            notify_rows(notifier, "coupons", ":moneybag: {n} new/changed value leg(s):", [
                (l.value, f"  {l.match_name} ({l.pick}) — your: {l.odds}, "
                          f"fair: {l.fair_odds:.2f}, value: +{l.value:.1%}")
                for l in value_legs
            ])
    finally:
        if close:
            db.close()
    return total_updated


# Which cards/corners bets have already been alerted, and at what tier.
#
# IN MEMORY, unlike the coupon path's Leg.notified_tier. Cards rows are computed
# per sweep and never stored — there is no row to hang a flag off — and they are
# short-lived by nature: a bet exists only inside its fixture's pre-kickoff
# window and is gone once the match starts. A restart re-alerts each live bet
# once, which is the honest trade for not adding a table whose rows would all be
# stale within four hours.
_cards_notified: dict[tuple, str] = {}
# The last cards sweep's failure, if the latest one failed (for diagnostics).
LAST_CARDS_ERROR: dict = {}


def _cards_key(row) -> tuple:
    return (row.event_id, row.market_key, row.line, row.side)


def sweep_cards(cards_client, pinnacle=None, notifier=None) -> int:
    """One cards/corners sweep: fetch, value, alert on new value. Returns rows.

    Runs on a 5-MINUTE tick, but that is not the same as calling every fixture
    every five minutes. The client tiers each fixture by time to kickoff (via
    the odds cache's per-read TTL), so a tick only spends credits on fixtures
    inside the fast window; the rest are served from cache. The cost dial is the
    WINDOW, not this interval — see cards/client.py.

    Alerts are deduped by value tier, the same rule the coupon refresh follows:
    a bet is announced when its tier appears or changes, never once per tick.
    Without that a live value bet would fire every five minutes for four hours.
    """
    if cards_client is None:
        return 0
    try:
        from .routers.cards import price_events      # one pricing entry point
        events = merge_pinnacle(cards_client.get_events(), pinnacle)
        rows = price_events(events, settings.cards_min_edge)
    except Exception as exc:
        # Logged with the cause: a silent failure here is what made the old
        # cards client look like a market with no value in it. Kept for
        # /paddy/diagnostics too, which otherwise shows only an empty pricing.
        logger.exception("Cards sweep failed")
        LAST_CARDS_ERROR.update(at=utcnow().isoformat(), error=repr(exc)[:500])
        return 0
    LAST_CARDS_ERROR.clear()

    fresh = []
    seen = set()
    for row in rows:
        key = _cards_key(row)
        seen.add(key)
        tier = value_tier(row.edge)
        if tier and _cards_notified.get(key) != tier:
            fresh.append(row)
        if tier:
            _cards_notified[key] = tier
        else:
            _cards_notified.pop(key, None)

    # Forget bets that have dropped off the sweep entirely (their fixture kicked
    # off, or the market came down). Without this the dict is a slow leak keyed
    # by every bet the process has ever seen — the same shape of bug the caches
    # had before ttl_cache.py.
    for key in [k for k in _cards_notified if k not in seen]:
        del _cards_notified[key]

    # Alerts stay off until the feed has been proven against real prices —
    # a value tool that alerts before anyone has checked it trains trust it has
    # not earned. The sweep still runs, and the rows are still on the screen.
    if fresh and settings.cards_discord_alerts:
        from .routers.cards import market_name
        notify_rows(notifier, "paddy", ":triangular_flag_on_post: Paddy — {n} new/changed value bet(s):", [
            (r.edge, f"  {r.home_team} v {r.away_team} — {r.side[0]}{r.line:g} "
                     f"{market_name(r.market_key)} @ {r.paddy_odds}, "
                     f"fair {r.fair_odds:.2f}, +{r.edge:.1%}")
            for r in fresh
        ])
    return len(rows)


def _goalscorer_sport_keys() -> list[str]:
    return [k.strip() for k in settings.goalscorer_sport_keys.split(",") if k.strip()]


def run_goalscorer_scan(client, ref: dict, *, inplay: bool, notifier=None) -> int:
    """Scan one fixture for first-goalscorer value and store/alert. Own DB session."""
    db = SessionLocal()
    try:
        stored = scan_and_store(
            client, ref, db, notifier,
            min_edge=settings.goalscorer_min_edge,
            name_match_floor=settings.goalscorer_name_match_floor,
            inplay=inplay,
        )
        return len(stored)
    finally:
        db.close()


def build_goalscorer_watchlist(scheduler, client, notifier=None) -> int:
    """Build the day's major-fixture watchlist and schedule each fixture's scan.

    Runs daily and at startup. For each fixture: a one-off scan at
    kickoff − lead_minutes (clamped to "soon" if that moment has passed), and —
    when the in-play interval is enabled — a recurring scan from kickoff for the
    in-play window. Also clears alert rows for fixtures long past.
    """
    db = SessionLocal()
    try:
        cleanup_stale_alerts(db)
    finally:
        db.close()

    if client is None:
        return 0

    watch = build_watchlist(client, _goalscorer_sport_keys())
    now = datetime.now(timezone.utc)
    lead = timedelta(minutes=settings.goalscorer_lead_minutes)
    interval = settings.goalscorer_inplay_interval
    window = timedelta(minutes=settings.goalscorer_inplay_window)

    for ref in watch:
        kickoff = ref["kickoff"]
        scan_at = max(now + timedelta(seconds=30), kickoff - lead)
        scheduler.add_job(
            run_goalscorer_scan,
            trigger="date",
            run_date=scan_at,
            id=f"gs_pre_{ref['event_id']}",
            replace_existing=True,
            kwargs={"client": client, "ref": ref, "inplay": False, "notifier": notifier},
        )
        if interval > 0:
            scheduler.add_job(
                run_goalscorer_scan,
                trigger="interval",
                minutes=interval,
                start_date=kickoff,
                end_date=kickoff + window,
                id=f"gs_live_{ref['event_id']}",
                replace_existing=True,
                kwargs={"client": client, "ref": ref, "inplay": True, "notifier": notifier},
            )

    logger.info("Goalscorer watchlist: scheduled %d fixture(s)", len(watch))
    return len(watch)


# 365 acca boost picks already alerted, and at what tier. In memory and swept
# every pass, for the same reasons as _cards_notified.
_acca_notified: dict[tuple, str] = {}


def sweep_accabonus(arbs_client, notifier=None) -> int:
    """Post 365 acca boost picks that are new or changed tier. Returns rows."""
    if arbs_client is None:
        return 0
    from .routers.accabonus import build_feed
    rows = build_feed(arbs_client, settings.accabonus_alert_min_edge)
    fresh, seen = [], set()
    for r in rows:
        key = (r.match, r.pick)
        seen.add(key)
        tier = value_tier(r.value)
        if tier and _acca_notified.get(key) != tier:
            fresh.append(r)
        if tier:
            _acca_notified[key] = tier
    for key in [k for k in _acca_notified if k not in seen]:
        del _acca_notified[key]
    if settings.accabonus_discord_alerts:
        notify_rows(notifier, "365", ":rocket: 365 acca boost — {n} new/changed value pick(s):", [
            (r.value, f"  {r.match} ({r.sport}) — {r.pick} @ {r.odds}, +{r.value:.1%}")
            for r in fresh
        ])
    return len(rows)


def start_scheduler(
    odds_provider, notifier=None, odds_client=None, cards_client=None, arbs_client=None,
) -> BackgroundScheduler:
    """Create, configure, and start the background scheduler."""
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        cleanup_old_coupons,
        trigger="interval",
        hours=24,
        id="daily_cleanup",
        replace_existing=True,
        kwargs={"notifier": notifier},
    )

    # Same 10-minute cadence as the refresh: a row is stale the moment its
    # fixture kicks off, and the refresh is what would otherwise keep pricing
    # (and alerting on) a match that has already been played.
    scheduler.add_job(
        cleanup_kicked_off_legs,
        trigger="interval",
        minutes=10,
        id="kicked_off_cleanup",
        replace_existing=True,
    )

    scheduler.add_job(
        refresh_all_live_coupons,
        trigger="interval",
        minutes=10,
        id="odds_refresh",
        replace_existing=True,
        kwargs={"odds_provider": odds_provider, "notifier": notifier},
    )

    # Cards/corners: a 5-minute tick, but only fixtures inside the fast window
    # actually cost a call — the client tiers them by time to kickoff. Gated on
    # the client existing, so no ODDS_API_KEY means no job rather than a job
    # that wakes every five minutes to do nothing.
    if settings.enable_cards and cards_client is not None:
        scheduler.add_job(
            sweep_cards,
            trigger="interval",
            # OddsPapi's tightest gear is 2 minutes, so tick that often; its
            # cache decides what is actually fetched. TheOddsApi's path keeps
            # its own interval (its tiers are tuned to it).
            minutes=2 if type(cards_client).__name__ == "OddsPapiPaddySource"
            else settings.cards_fast_interval_minutes,
            id="cards_sweep",
            replace_existing=True,
            # Skip a tick rather than queue it if a sweep overruns — a backlog
            # of sweeps would spend the daily budget catching up on prices that
            # are already stale.
            coalesce=True,
            max_instances=1,
            kwargs={
                "cards_client": cards_client,
                "pinnacle": odds_provider,
                "notifier": notifier,
            },
        )

    if settings.enable_accabonus and arbs_client is not None:
        scheduler.add_job(
            sweep_accabonus,
            trigger="interval",
            minutes=settings.accabonus_alert_minutes,
            id="accabonus_alerts",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            kwargs={"arbs_client": arbs_client, "notifier": notifier},
        )

    # First-goalscorer pipeline: rebuild the watchlist daily, and once now so a
    # restart re-populates today's per-fixture scans instead of waiting a day.
    if settings.enable_goalscorers and odds_client is not None:
        scheduler.add_job(
            build_goalscorer_watchlist,
            trigger="interval",
            hours=24,
            id="goalscorer_watchlist",
            replace_existing=True,
            next_run_time=utcnow(),
            kwargs={"scheduler": scheduler, "client": odds_client, "notifier": notifier},
        )

    if notifier:
        def _on_job_error(event):
            notifier.send(
                f":rotating_light: Scheduler job `{event.job_id}` failed: "
                f"{event.exception!r}"
            )
        scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    scheduler.start()
    return scheduler
