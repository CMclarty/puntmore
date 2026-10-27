"""What value is on offer right now, across every tool — ONE row shape.

A future bet-placer asks one question here and gets `ValueSelection` rows
(schemas.py), sorted by edge. Every row comes from the tool's OWN feed builder
(cards/feed.py, accabonus/feed.py, the goalscorer rows the scheduler stores,
the coupon tracker's priced legs), so this file prices nothing itself and
cannot drift from what the pages show. It reads through the same caches as
the pages: polling it costs no vendor quota beyond what the pages cost.

Read-only. Nothing here places, or prepares to place, a bet.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ..models import GoalscorerAlert
from ..normalise import split_sides
from ..pricing.counts.lines import is_half_line
from ..schemas import ValueSelection
from ..security import enabled_tools, has_permission

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class Source:
    name: str        # what a row's `source` says, and the /api/value/{source} path
    tool: str        # the grant (and enable flag) that owns it
    bookmaker: str


# Feed order. A source is listed only while its tool is enabled on the server.
SOURCES: tuple[Source, ...] = (
    Source("paddy", "cards", "paddypower"),
    Source("365", "accabonus", "bet365"),
    Source("scorers", "goalscorers", "bet365"),
    Source("coupon", "coupons", "williamhill"),
)
BY_NAME = {s.name: s for s in SOURCES}


def visible_sources(perms: str) -> list[Source]:
    """Sources this caller may read: all of them with the `value` grant (or
    "*"), otherwise those whose tool it holds — and only enabled tools."""
    on = set(enabled_tools())
    everything = has_permission(perms, "value")
    return [s for s in SOURCES if s.tool in on and (everything or has_permission(perms, s.tool))]


def _utc(value) -> datetime | None:
    """An ISO string or naive-UTC datetime → an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        when = value
    else:
        try:
            when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _sides(event: str, home: str | None = None, away: str | None = None):
    if home and away:
        return home, away
    pair = split_sides(event)
    return pair if pair else (None, None)


# --- per source ---------------------------------------------------------------

def paddy_rows(cards_client, floor: float, now: datetime) -> list[ValueSelection]:
    from ..cards.feed import build_feed
    out = []
    for item in build_feed(cards_client, floor):
        home, away = _sides(item.match, item.home, item.away)
        for bet in item.bets:
            three_way = bet.market.startswith("most_")
            line = None if three_way else bet.line
            if line is not None and not is_half_line(line):
                continue              # pushes are never listed, anywhere
            out.append(ValueSelection(
                source="paddy", bookmaker="paddypower",
                event_id=str(item.event_id), event=item.match, home=home, away=away,
                kickoff=_utc(item.commence_time), sport=item.sport_key or None,
                market=bet.market, selection=bet.pick, line=line,
                side=(bet.side or "").lower() or None,
                odds=bet.odds, fair=bet.fair, edge=bet.value, basis=bet.basis,
                seen_at=now,
            ))
    return out


def acca_row(item, now: datetime) -> ValueSelection | None:
    """One 365 acca feed item (AccaBonusFeedItem) as a ValueSelection, or None
    when it has no fair price. Shared with the bet feed (betfeed/service.py)."""
    if item.fair is None:
        return None
    home, away = _sides(item.match, item.home, item.away)
    side = ("home" if item.pick == home else "away" if item.pick == away
            else "draw" if item.pick == "Draw" else None)
    return ValueSelection(
        source="365", bookmaker="bet365",
        event_id=item.event_id, event=item.match, home=home, away=away,
        kickoff=_utc(item.commence_time), sport=item.sport_key or item.sport,
        market="h2h", selection=item.pick, side=side,
        odds=item.odds, fair=item.fair, edge=item.value, basis="devig",
        seen_at=now,
    )


def acca_rows(arbs_client, floor: float, now: datetime) -> list[ValueSelection]:
    from ..accabonus.feed import build_feed
    rows = (acca_row(item, now) for item in build_feed(arbs_client, floor))
    return [r for r in rows if r is not None]


def scorer_rows(db: Session, floor: float, now: datetime) -> list[ValueSelection]:
    out = []
    for r in db.query(GoalscorerAlert).filter(GoalscorerAlert.edge >= floor).all():
        home, away = _sides(r.match_name)
        out.append(ValueSelection(
            source="scorers", bookmaker="bet365",
            event_id=r.event_id, event=r.match_name, home=home, away=away,
            kickoff=_utc(r.commence_time), sport="soccer",
            market="first_goalscorer", selection=r.player,
            odds=r.bet365_price, fair=r.fair_price, edge=r.edge, basis="devig",
            seen_at=_utc(r.created_at) or now,
        ))
    return out


def coupon_rows(db: Session, floor: float, now: datetime) -> list[ValueSelection]:
    from ..coupons.service import live_value_legs
    out = []
    for leg in live_value_legs(db, now.replace(tzinfo=None)):
        if leg.value < floor:
            continue
        home, away = _sides(leg.match_name)
        out.append(ValueSelection(
            source="coupon", bookmaker="williamhill",
            event_id=str(leg.external_event_id) if leg.external_event_id is not None else None,
            event=leg.match_name, home=home, away=away,
            kickoff=_utc(leg.kickoff), sport="soccer",
            market=leg.coupon.type_code, selection=leg.pick, side=leg.side,
            odds=leg.odds, fair=leg.fair_odds, edge=leg.value, basis="grid",
            seen_at=_utc(leg.fair_odds_updated_at) or now,
        ))
    return out


# --- the feed -----------------------------------------------------------------

def collect(
    sources: list[Source],
    *,
    db: Session,
    cards_client=None,
    arbs_client=None,
    min_edge: float | None = None,
    kickoff_within_hours: float | None = None,
    bookmaker: str | None = None,
    now: datetime | None = None,
) -> tuple[list[ValueSelection], dict[str, str]]:
    """Every value row from `sources`, filtered, best edge first.

    `min_edge` None means edge > 0 (value at all); a number means edge >= it,
    and may be negative to see near-value rows. A source that raises is left
    out and named in the returned errors — the rest still answer.
    """
    now = now or datetime.now(timezone.utc)
    floor = 0.0 if min_edge is None else min_edge
    builders: dict[str, Callable[[], list[ValueSelection]]] = {
        "paddy": lambda: paddy_rows(cards_client, floor, now),
        "365": lambda: acca_rows(arbs_client, floor, now),
        "scorers": lambda: scorer_rows(db, floor, now),
        "coupon": lambda: coupon_rows(db, floor, now),
    }
    rows: list[ValueSelection] = []
    errors: dict[str, str] = {}
    want_book = (bookmaker or "").strip().lower() or None
    for src in sources:
        if want_book and src.bookmaker != want_book:
            continue
        try:
            rows.extend(builders[src.name]())
        except Exception as exc:          # one broken vendor must not blank the rest
            logger.error("value feed: %s failed: %s", src.name, exc)
            errors[src.name] = f"{type(exc).__name__}: {exc}"[:300]

    if min_edge is None:
        rows = [r for r in rows if r.edge > 0]
    else:
        rows = [r for r in rows if r.edge >= min_edge]
    if kickoff_within_hours is not None:
        until = now + timedelta(hours=kickoff_within_hours)
        rows = [r for r in rows if r.kickoff is not None and now <= r.kickoff <= until]
    rows.sort(key=lambda r: r.edge, reverse=True)
    return rows, errors
