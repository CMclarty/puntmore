"""APScheduler setup: 10-min odds refresh + daily 30-day cleanup."""
import os
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR

from .database import SessionLocal
from .models import Coupon, Leg
from .comparison import value_tier
from .pricing.legs import price_legs
from .time_utils import utcnow


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


def start_scheduler(odds_provider, notifier=None) -> BackgroundScheduler:
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

    if notifier:
        def _on_job_error(event):
            notifier.send(
                f":rotating_light: Scheduler job `{event.job_id}` failed: "
                f"{event.exception!r}"
            )
        scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    scheduler.start()
    return scheduler
