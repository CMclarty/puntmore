"""Paddy's board, kept (Callum, 10 Nov 2026: "we need a rolling list of
selections").

The live board shows only what Paddy prices now: a game drops off at kick-off
and a line when Paddy takes it down. AW 26 values a slip at the fair of the
moment it was struck, and its photo may be read hours later, so every line
the sweep prices is kept here:

- a row is written when a line's Paddy price or fair moves (the fair to the
  penny, so a refit's noise writes nothing); a row holds until the next one
  for the same line;
- before kick-off only: an in-play price is never kept;
- every line the board prices, down to FLOOR, so a leg taken below fair is
  found too;
- kept KEEP_DAYS after kick-off, then deleted (once a day, by `record`).

`board_at(at)` is the board as it stood at `at`, in the live feed's shape:
each line's price then, or its first price after when it had none yet (the
nearest there is), for every game not yet kicked off at `at`. Paddy's board
only: Hills' is not kept. Nothing here fetches anything.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import PaddyLine
from ..schemas import CardFeedBet, CardFeedItem
from .feed import describe, market_name
from .value import CardValueRow

KEEP_DAYS = 14
FLOOR = -0.50          # the lowest value the board is priced down to
WINDOW = timedelta(days=8)   # the board reaches 7 days out; a day's slack

# The last price written per line, so an unchanged line writes nothing. Loaded
# from the table on first use (a restart writes nothing again) and pruned of
# games that have kicked off, so it holds only the board's current lines.
_last: dict[tuple, tuple] = {}
_loaded = False
_pruned_on: date | None = None


def reset() -> None:
    """Forget what is in memory (a restart, in tests)."""
    global _loaded, _pruned_on
    _last.clear()
    _loaded = False
    _pruned_on = None


def _kickoff(commence_time: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def _key(event_id, market_key, line, side) -> tuple:
    return (event_id, market_key, float(line), side)


def _load(db: Session, now: datetime) -> None:
    global _loaded
    for r in (db.query(PaddyLine).filter(PaddyLine.kickoff > now)
              .order_by(PaddyLine.seen_at, PaddyLine.id)):
        _last[_key(r.event_id, r.market_key, r.line, r.side)] = (r.odds, r.fair, r.kickoff)
    _loaded = True


def record(db: Session, rows: list[CardValueRow], now: datetime) -> int:
    """Keep each priced row whose price moved; returns how many were written."""
    global _pruned_on
    if not _loaded:
        _load(db, now)
    written = 0
    for row in rows:
        kickoff = _kickoff(row.commence_time)
        if kickoff is None or kickoff <= now:
            continue
        key = _key(row.event_id, row.market_key, row.line, row.side)
        price = (row.paddy_odds, round(row.fair_odds, 2))
        if _last.get(key, (None, None))[:2] == price:
            continue
        db.add(PaddyLine(event_id=row.event_id, home=row.home_team, away=row.away_team,
                         commence_time=row.commence_time, kickoff=kickoff,
                         sport_key=row.sport_key or "", market_key=row.market_key,
                         line=float(row.line), side=row.side, odds=price[0], fair=price[1],
                         basis=row.basis, seen_at=now))
        _last[key] = (*price, kickoff)
        written += 1
    for key in [k for k, v in _last.items() if v[2] <= now]:
        del _last[key]
    if _pruned_on != now.date():
        db.query(PaddyLine).filter(PaddyLine.kickoff < now - timedelta(days=KEEP_DAYS)).delete(
            synchronize_session=False)
        _pruned_on = now.date()
    db.commit()
    return written


def board_at(db: Session, at: datetime) -> list[CardFeedItem]:
    """The board as it stood at `at` (naive UTC), soonest kick-off first."""
    chosen: dict[tuple, PaddyLine] = {}
    for r in (db.query(PaddyLine)
              .filter(PaddyLine.kickoff > at, PaddyLine.kickoff <= at + WINDOW)
              .order_by(PaddyLine.seen_at, PaddyLine.id)):
        key = _key(r.event_id, r.market_key, r.line, r.side)
        best = chosen.get(key)
        if r.seen_at <= at or best is None:
            chosen[key] = r            # the latest at or before `at`, else the first after
    games: dict[str, dict] = {}
    for r in chosen.values():
        g = games.setdefault(r.event_id, {"row": r, "bets": [], "seen": r.seen_at})
        g["seen"] = max(g["seen"], r.seen_at)
        bet = CardValueRow(event_id=r.event_id, home_team=r.home, away_team=r.away,
                           commence_time=r.commence_time, sport_key=r.sport_key,
                           market_key=r.market_key, market_label="", line=r.line, side=r.side,
                           paddy_odds=r.odds, pinnacle_odds=r.fair, fair_odds=r.fair,
                           pinnacle_margin=0.0, edge=r.odds / r.fair - 1, basis=r.basis)
        g["bets"].append(CardFeedBet(pick=describe(bet), odds=r.odds, fair=r.fair,
                                     value=round(bet.edge, 4), market=market_name(r.market_key),
                                     line=r.line, side=r.side, basis=r.basis))
    out = []
    for g in sorted(games.values(), key=lambda g: (g["row"].kickoff, g["row"].event_id)):
        r = g["row"]
        g["bets"].sort(key=lambda b: (b.market, b.line, b.side))
        out.append(CardFeedItem(event_id=r.event_id, match=f"{r.home} vs {r.away}", home=r.home,
                                away=r.away, commence_time=r.commence_time, sport_key=r.sport_key,
                                best_value=max(b.value for b in g["bets"]), bets=g["bets"],
                                priced_at=g["seen"]))
    return out
