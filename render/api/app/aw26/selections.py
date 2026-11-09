"""AW 26: the same selection across slips.

Two legs are the same selection when they are on the same game and their
market and selection read the same. The game is the event id; failing one,
the words for the game on one London day (the kick-off's, or the day the slip
was placed when the leg has no kick-off), so two matches between the same two
teams are two games. `group` gathers the legs into one row per selection;
`mark` sets one result on every leg of a selection and re-settles each slip
that carries it. A mark is a result, not a correction: the EV, the fairs and
`edited` stand.
"""
import re
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session, contains_eager

from ..models import AwLeg, AwSlip
from . import service


def words(text: str | None) -> str:
    """Lower case, " vs " and " v. " read as " v ", runs of white space
    collapsed, trimmed."""
    s = re.sub(r"\s+", " ", (text or "").lower()).strip()
    return re.sub(r" (?:vs|v\.) ", " v ", s)


def game_key(leg) -> str:
    """"e:<event id>", or "w:<words>@<London day>": the kick-off's day, else
    the day the slip was placed."""
    if leg.fixture_id:
        return f"e:{leg.fixture_id}"
    day = service.london_day(leg.kickoff or leg.slip.placed_at)
    return f"w:{words(leg.event)}@{day.isoformat()}"


def selection_key(leg) -> str:
    return game_key(leg) + "|" + words(f"{leg.market or ''} {leg.selection or ''}")


def group(legs) -> list[dict]:
    """One dict per selection, ordered by kick-off (none last) then event."""
    by_key: dict[str, list] = {}
    for leg in legs:
        by_key.setdefault(selection_key(leg), []).append(leg)
    out = []
    for key, members in by_key.items():
        first = min(members, key=lambda lg: lg.id)
        kickoffs = [lg.kickoff for lg in members if lg.kickoff is not None]
        kickoff = min(kickoffs, default=None)
        odds = [lg.odds for lg in members if lg.odds is not None]
        results = {lg.result for lg in members}
        out.append({
            "leg_id": first.id,
            "key": key,
            "game": game_key(first),
            "event": first.event,
            "market": first.market,
            "selection": first.selection,
            "kickoff": kickoff,
            "day": service.london_day(kickoff or first.slip.placed_at).isoformat(),
            "slips": len({lg.slip_id for lg in members}),
            "odds": round(sum(odds) / len(odds), 2) if odds else None,
            "result": results.pop() if len(results) == 1 else "pending",
            "fair_assumed": any(lg.fair_assumed for lg in members),
        })
    out.sort(key=lambda g: (g["kickoff"] is None, g["kickoff"] or datetime.min, g["event"]))
    return out


def mark(db: Session, leg_id: int, result: str) -> tuple[int, int]:
    """Set `result` on every leg of `leg_id`'s selection on slips placed on or
    after START, re-settle each of those slips and commit. Returns (legs
    changed, slips touched). LookupError when the leg does not exist or its
    slip is from before START."""
    start = service.day_bounds(service.START)[0]
    target = db.get(AwLeg, leg_id)
    if target is None or target.slip.placed_at < start:
        raise LookupError(f"No leg {leg_id} on a slip from AW 26")
    same_game = (AwLeg.fixture_id == target.fixture_id if target.fixture_id
                 else or_(AwLeg.fixture_id.is_(None), AwLeg.fixture_id == ""))
    candidates = (db.query(AwLeg).join(AwLeg.slip).options(contains_eager(AwLeg.slip))
                  .filter(same_game, AwSlip.placed_at >= start).all())
    key = selection_key(target)
    legs = [lg for lg in candidates if selection_key(lg) == key]
    slips = {}
    for lg in legs:
        lg.result = result
        slips[lg.slip_id] = lg.slip
    for slip in slips.values():
        service.settle_slip(slip)
    db.commit()
    return len(legs), len(slips)
