"""APScheduler setup: 10-min odds refresh + daily 30-day cleanup + the daily
first-goalscorer watchlist that schedules each fixture's pre-kickoff scan."""
import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR

from .config import settings
from .database import SessionLocal
from .models import Coupon, Leg
from .comparison import value_tier
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
            if c.image_path and os.path.exists(c.image_path):
                os.remove(c.image_path)
                files_removed += 1
            db.delete(c)
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

            if notifier and value_legs:
                lines = "\n".join(
                    f"  {l.match_name} ({l.pick}) — your: {l.odds}, "
                    f"fair: {l.fair_odds:.2f}, value: +{l.value:.1%}"
                    for l in value_legs
                )
                notifier.send(
                    f":moneybag: {len(value_legs)} new/changed value leg(s):\n{lines}"
                )
    finally:
        if close:
            db.close()
    return total_updated


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


def start_scheduler(odds_provider, notifier=None, odds_client=None) -> BackgroundScheduler:
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

    scheduler.add_job(
        refresh_all_live_coupons,
        trigger="interval",
        minutes=10,
        id="odds_refresh",
        replace_existing=True,
        kwargs={"odds_provider": odds_provider, "notifier": notifier},
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
