"""Settling BB Boost bets from OddsPapi's /settlements.

/settlements?fixtureId= answers per market id and outcome id, the ids each
logged leg carries (hills/legs.py): markets.<mid>.outcomes.<oid>.players."0"
.result, one of WIN, LOSE, HALFWIN, HALFLOSS, PUSH, CANCELLED, UNDECIDED (the
documented shape, oddspapi.io/en/docs/get-settlements; read off a live
response before trusting it).

A bet is LOST as soon as one leg loses. It is WON only when every leg has
won and nothing was left to hand (no free-text note). Anything else — a leg
undecided, pushed or cancelled, or a note — stays open for someone to set.

Cost: one billable request per fixture per check. Checks run only when the
page is opened, only once the match should be over (kick-off + 2 h), at most
every 30 minutes per fixture, and stop once its bets are all settled.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import HillsBet

logger = logging.getLogger("uvicorn.error")

FINISHED_AFTER = timedelta(hours=2)
RECHECK_SECONDS = 1800
GIVE_UP_AFTER = timedelta(days=2)
_RESULTS = {"WIN": "won", "LOSE": "lost", "PUSH": "void", "CANCELLED": "void"}
_LAST_CHECK: dict[str, float] = {}


def leg_results(settlements: dict) -> dict[tuple[str, str], str]:
    """{(market_id, outcome_id): won | lost | void} from a /settlements body.
    UNDECIDED and the half results (never on a half line) are left out."""
    out = {}
    for mid, market in ((settlements or {}).get("markets") or {}).items():
        for oid, outcome in ((market or {}).get("outcomes") or {}).items():
            players = (outcome or {}).get("players") or {}
            result = (players.get("0") or next(iter(players.values()), {}) or {}).get("result")
            if result in _RESULTS:
                out[(str(mid), str(oid))] = _RESULTS[result]
    return out


def bet_result(legs: list[dict], note: str | None) -> str:
    results = [leg.get("result") for leg in legs]
    if "lost" in results:
        return "lost"
    if legs and all(r == "won" for r in results) and not note:
        return "won"
    return "open"


def apply(db: Session, fixture_id: str, results: dict[tuple[str, str], str]) -> int:
    """Write leg results onto the fixture's open bets; returns bets settled."""
    settled = 0
    for bet in db.query(HillsBet).filter(HillsBet.fixture_id == fixture_id,
                                         HillsBet.result == "open"):
        legs = json.loads(bet.legs_json or "[]")
        for leg in legs:
            got = results.get((leg["market_id"], leg["outcome_id"]))
            if got:
                leg["result"] = got
        bet.legs_json = json.dumps(legs)
        outcome = bet_result(legs, bet.note)
        if outcome != "open":
            bet.result, bet.settled_by = outcome, "oddspapi"
            settled += 1
    db.commit()
    return settled


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def due_fixtures(db: Session, now: datetime | None = None) -> list[str]:
    """Fixtures with open, OddsPapi-settleable bets whose match should be over."""
    now = now or datetime.now(timezone.utc)
    rows = db.query(HillsBet.fixture_id, HillsBet.kickoff).filter(
        HillsBet.result == "open", HillsBet.legs_json != "[]").distinct()
    return [fid for fid, ko in rows
            if _aware(ko) + FINISHED_AFTER <= now < _aware(ko) + GIVE_UP_AFTER]


def check(db: Session, client, now: datetime | None = None) -> int:
    """Settle what is due, within the recheck spacing. Never raises."""
    if client is None or not hasattr(client, "get"):
        return 0
    settled = 0
    for fid in due_fixtures(db, now):
        if time.monotonic() - _LAST_CHECK.get(fid, -1e9) < RECHECK_SECONDS:
            continue
        _LAST_CHECK[fid] = time.monotonic()
        try:
            settled += apply(db, fid, leg_results(client.get("/settlements", fixtureId=fid)))
        except Exception as exc:          # a failed check waits for the next window
            logger.warning("hills settle: /settlements for %s failed: %s", fid, exc)
    return settled
