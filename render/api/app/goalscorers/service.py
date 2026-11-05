"""Goalscorer pipeline: build the day's watchlist, scan a fixture, store+notify.

Deterministic — no LLM in the loop. The daily job builds a watchlist of major
fixtures; each fixture is scanned at kickoff − 45 min (and optionally on an
in-play interval), because Pinnacle only posts first-goalscorer close to KO for
major events. A scan compares bet365 to de-vigged Pinnacle and stores the value
rows, alerting Discord on players that are newly in value.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import GoalscorerAlert
from ..time_utils import utcnow
from .value import (
    DEFAULT_NAME_MATCH_FLOOR,
    FIRST_GOALSCORER_MARKET,
    compute_goalscorer_value,
)

logger = logging.getLogger("uvicorn.error")

# Both books, so the de-vig reference and the value side arrive in one call.
_BOOKMAKERS = "bet365,pinnacle"


def _parse_commence(iso: str) -> datetime | None:
    """Parse TheOddsApi's ISO8601 commence_time (…Z) to an aware UTC datetime."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def build_watchlist(client, sport_keys: list[str], within_hours: int = 30) -> list[dict]:
    """Fixtures for the given sports kicking off within `within_hours` from now.

    Uses the free /events listing (no odds, no quota). Returns lightweight refs
    the scan and the scheduler both consume.
    """
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=within_hours)
    watch: list[dict] = []
    for sport_key in sport_keys:
        try:
            events = client.list_events(sport_key)
        except Exception as exc:  # network / vendor error — skip this sport
            logger.warning("goalscorer watchlist: %s failed: %r", sport_key, exc)
            continue
        for ev in events:
            kickoff = _parse_commence(ev.get("commence_time", ""))
            if kickoff is None or not (now <= kickoff <= horizon):
                continue
            watch.append({
                "sport_key": sport_key,
                "event_id": ev.get("id", ""),
                "home": ev.get("home_team", ""),
                "away": ev.get("away_team", ""),
                "commence_time": ev.get("commence_time", ""),
                "kickoff": kickoff,
            })
    return watch


def scan_and_store(
    client,
    ref: dict,
    db: Session,
    notifier=None,
    *,
    min_edge: float,
    name_match_floor: float = DEFAULT_NAME_MATCH_FLOOR,
    inplay: bool = False,
) -> list[GoalscorerAlert]:
    """Scan one fixture, replace its stored rows, alert on newly-valued players.

    Returns the freshly stored GoalscorerAlert rows (may be empty).
    """
    event = client.fetch_event(
        ref["sport_key"], ref["event_id"], FIRST_GOALSCORER_MARKET, _BOOKMAKERS
    )
    if not event:
        return []

    rows = compute_goalscorer_value(event, min_edge, name_match_floor)

    # Players already in value for this event, so a re-scan (esp. the in-play
    # loop) doesn't re-alert the same names every cycle.
    prior = {
        a.player
        for a in db.query(GoalscorerAlert)
        .filter(GoalscorerAlert.event_id == ref["event_id"])
        .all()
    }

    db.query(GoalscorerAlert).filter(
        GoalscorerAlert.event_id == ref["event_id"]
    ).delete()

    kickoff = _parse_commence(ref.get("commence_time", ""))
    stored: list[GoalscorerAlert] = []
    for r in rows:
        alert = GoalscorerAlert(
            event_id=r.event_id,
            match_name=r.match_name,
            commence_time=kickoff.replace(tzinfo=None) if kickoff else None,
            player=r.player,
            matched_player=r.matched_player,
            match_score=r.match_score,
            bet365_price=r.bet365_price,
            pinnacle_price=r.pinnacle_price,
            fair_price=r.fair_price,
            edge=r.edge,
            inplay=inplay,
        )
        db.add(alert)
        stored.append(alert)
    db.commit()

    new_value = [r for r in rows if r.player not in prior]
    if new_value:
        from ..notifications.routes import notify_rows
        tag = "IN-PLAY " if inplay else ""
        match = new_value[0].match_name.replace("{", "{{").replace("}", "}}")
        notify_rows(notifier, "365", f":soccer: 365 — {tag}first-goalscorer value ({match}):", [
            (r.edge, f"  {r.player} — bet365: {r.bet365_price}, fair: {r.fair_price}, "
                     f"value: +{r.edge:.1%}")
            for r in new_value
        ])

    return stored


def cleanup_stale_alerts(db: Session, *, older_than_hours: int = 6) -> int:
    """Drop alert rows for fixtures that kicked off well in the past.

    Keeps the feed showing only current/near fixtures. Returns rows removed.
    """
    cutoff = utcnow() - timedelta(hours=older_than_hours)
    stale = db.query(GoalscorerAlert).filter(
        GoalscorerAlert.commence_time.isnot(None),
        GoalscorerAlert.commence_time < cutoff,
    )
    count = stale.count()
    stale.delete(synchronize_session=False)
    db.commit()
    return count
