"""BB Boost's bet log: the builders the team has placed on target matches.

Free-text legs and a result set by hand (open → won / lost). No prices, stakes
or fairs are stored: builders cannot be priced from their legs, and the page's
fair wheel is an on-the-spot check only.
"""
from sqlalchemy.orm import Session

from ..models import HillsBet, HillsTarget

RESULTS = ("open", "won", "lost")
MAX_LEGS_TEXT = 300


def _out(b: HillsBet) -> dict:
    return {"id": b.id, "fixture_id": b.fixture_id, "match": b.match,
            "kickoff": b.kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"), "legs": b.legs,
            "result": b.result, "placed_by": b.placed_by,
            "placed_at": b.placed_at.strftime("%Y-%m-%dT%H:%M:%SZ")}


def for_fixture(db: Session, fixture_id: str) -> list[dict]:
    rows = (db.query(HillsBet).filter(HillsBet.fixture_id == fixture_id)
            .order_by(HillsBet.placed_at.desc(), HillsBet.id.desc()).all())
    return [_out(b) for b in rows]


def recent(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(HillsBet).order_by(HillsBet.placed_at.desc(), HillsBet.id.desc()).limit(limit)
    return [_out(b) for b in rows]


def add(db: Session, target: HillsTarget, legs: str, user: str) -> dict:
    bet = HillsBet(fixture_id=target.fixture_id, match=f"{target.home} vs {target.away}",
                   kickoff=target.kickoff, legs=legs.strip()[:MAX_LEGS_TEXT], placed_by=user)
    db.add(bet)
    db.commit()
    return _out(bet)


def settle(db: Session, bet_id: int, result: str) -> dict | None:
    bet = db.get(HillsBet, bet_id)
    if bet is None:
        return None
    bet.result = result
    db.commit()
    return _out(bet)


def remove(db: Session, bet_id: int, user: str) -> bool | None:
    """None: no such bet. False: someone else's (only its placer removes it)."""
    bet = db.get(HillsBet, bet_id)
    if bet is None:
        return None
    if bet.placed_by != user:
        return False
    db.delete(bet)
    db.commit()
    return True
