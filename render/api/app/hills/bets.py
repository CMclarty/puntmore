"""BB Boost's bet log: the builders the team has placed on target matches.

A bet is the legs picked off William Hill's lines on the match (hills/legs.py,
by OddsPapi id, so it settles itself from /settlements — hills/settle.py) plus
an optional note for a leg not on the list. A result can also be set by hand.
No prices, stakes or fairs are stored: builders cannot be priced from their
legs, and the page's fair wheel is an on-the-spot check only.
"""
import json

from sqlalchemy.orm import Session

from ..models import HillsBet, HillsTarget

RESULTS = ("open", "won", "lost")
MAX_NOTE = 300


def _out(b: HillsBet) -> dict:
    return {"id": b.id, "fixture_id": b.fixture_id, "match": b.match,
            "kickoff": b.kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "legs": [{"label": leg["label"], "result": leg.get("result")}
                     for leg in json.loads(b.legs_json or "[]")],
            "note": b.note, "text": b.legs, "result": b.result, "settled_by": b.settled_by,
            "placed_by": b.placed_by, "placed_at": b.placed_at.strftime("%Y-%m-%dT%H:%M:%SZ")}


def for_fixture(db: Session, fixture_id: str) -> list[dict]:
    rows = (db.query(HillsBet).filter(HillsBet.fixture_id == fixture_id)
            .order_by(HillsBet.placed_at.desc(), HillsBet.id.desc()).all())
    return [_out(b) for b in rows]


def recent(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(HillsBet).order_by(HillsBet.placed_at.desc(), HillsBet.id.desc()).limit(limit)
    return [_out(b) for b in rows]


def add(db: Session, target: HillsTarget, legs: list[dict], note: str | None, user: str) -> dict:
    """`legs` are entries of hills.legs.legs_for (trusted: looked up server-side)."""
    note = (note or "").strip()[:MAX_NOTE] or None
    picked = [{"market_id": leg["market_id"], "outcome_id": leg["outcome_id"],
               "label": leg["label"], "result": None} for leg in legs]
    text = " + ".join([leg["label"] for leg in picked] + ([note] if note else []))
    bet = HillsBet(fixture_id=target.fixture_id, match=f"{target.home} vs {target.away}",
                   kickoff=target.kickoff, legs=text, legs_json=json.dumps(picked),
                   note=note, placed_by=user)
    db.add(bet)
    db.commit()
    return _out(bet)


def settle(db: Session, bet_id: int, result: str, user: str) -> dict | None:
    bet = db.get(HillsBet, bet_id)
    if bet is None:
        return None
    bet.result, bet.settled_by = result, (None if result == "open" else user)
    db.commit()
    return _out(bet)


def remove(db: Session, bet_id: int) -> bool:
    """Anyone on the team may remove a bet (the page asks first). False when
    there is no such bet."""
    bet = db.get(HillsBet, bet_id)
    if bet is None:
        return False
    db.delete(bet)
    db.commit()
    return True
