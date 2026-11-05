"""Hills' own endpoints, beside the shared Paddy ones it mounts at /hills:

  GET    /hills/fixtures   what the target can be (target.eligible)
  GET    /hills/target     the team's target match, or null
  PUT    /hills/target     set it: {fixture_id, boost_pct}
  DELETE /hills/target     clear it
  GET    /hills/legs       William Hill's lines on the target, to log a bet from
  GET    /hills/bets       BB Boost's log: the target's bets (or ?fixture_id=);
                           settles what is due from OddsPapi first (hills/settle.py)
  POST   /hills/bets       log one on the target: {legs: [{market_id, outcome_id}], note}
  PATCH  /hills/bets/{id}  set a result by hand: {result: open | won | lost}
  DELETE /hills/bets/{id}  remove one (anyone; the page asks first)

Router-gated by require_tool("hills") in main.py. The target is one row for
the whole team (models.HillsTarget), not per user.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..hills import bets, legs, settle, target
from ..schemas import OpenModel
from ..security import require_user

router = APIRouter(prefix="/hills", tags=["hills"])


def _source(request: Request):
    return getattr(request.app.state, "hills_client", None)


def _fixtures(request: Request) -> list[dict]:
    """The sweep's fixtures; sweeps first if none has run (cached boards: cheap)."""
    src = _source(request)
    if src is None:
        return []
    if not src.fixtures:
        src.get_events()
    return target.eligible(src.fixtures)


@router.get("/fixtures", response_model=OpenModel)
def get_fixtures(request: Request):
    return {"fixtures": _fixtures(request), "boosts": list(target.BOOSTS)}


@router.get("/target", response_model=OpenModel)
def get_target(db: Session = Depends(get_db)):
    return {"target": target.as_dict(target.current(db))}


class TargetIn(BaseModel):
    fixture_id: str
    boost_pct: float = target.BOOSTS[0]


@router.put("/target", response_model=OpenModel)
def put_target(body: TargetIn, request: Request, db: Session = Depends(get_db),
               user: str = Depends(require_user)):
    if body.boost_pct not in target.BOOSTS:
        raise HTTPException(422, f"boost_pct must be one of {list(target.BOOSTS)}")
    fixture = next((f for f in _fixtures(request) if f["fixture_id"] == body.fixture_id), None)
    if fixture is None:
        raise HTTPException(404, "not a pickable fixture (Premier League weekend or a final, "
                                 "next 7 days)")
    return {"target": target.as_dict(target.set_target(db, fixture, body.boost_pct, user))}


@router.delete("/target", response_model=OpenModel)
def delete_target(db: Session = Depends(get_db)):
    target.clear(db)
    return {"target": None}


def _target_legs(request: Request, row) -> list[dict]:
    src = _source(request)
    if src is None:
        return []
    if not getattr(src, "boards", None):
        src.get_events()
    return legs.legs_for(src, row.fixture_id, row.home, row.away)


@router.get("/legs", response_model=OpenModel)
def get_legs(request: Request, db: Session = Depends(get_db)):
    row = target.current(db)
    return {"legs": _target_legs(request, row) if row else []}


@router.get("/bets", response_model=OpenModel)
def get_bets(request: Request, db: Session = Depends(get_db), fixture_id: str | None = None):
    """The bets on `fixture_id`, else on the live target, else the latest.
    Bets on matches that should be over are settled from OddsPapi first."""
    settle.check(db, getattr(_source(request), "_client", None))
    row = target.current(db)
    fid = fixture_id or (row.fixture_id if row else None)
    return {"bets": bets.for_fixture(db, fid) if fid else bets.recent(db)}


class LegIn(BaseModel):
    market_id: str
    outcome_id: str


class BetIn(BaseModel):
    legs: list[LegIn] = []
    note: str | None = None


@router.post("/bets", response_model=OpenModel)
def post_bet(body: BetIn, request: Request, db: Session = Depends(get_db),
             user: str = Depends(require_user)):
    row = target.current(db)
    if row is None:
        raise HTTPException(409, "no target match: pick one first")
    offered = legs.by_id(_target_legs(request, row))
    picked = [offered.get((leg.market_id, leg.outcome_id)) for leg in body.legs]
    if any(p is None for p in picked):
        raise HTTPException(422, "a leg is not one of William Hill's lines on this match")
    if not picked and not (body.note or "").strip():
        raise HTTPException(422, "pick the builder's legs (or note them)")
    return {"bet": bets.add(db, row, picked, body.note, user)}


class ResultIn(BaseModel):
    result: str


@router.patch("/bets/{bet_id}", response_model=OpenModel)
def patch_bet(bet_id: int, body: ResultIn, db: Session = Depends(get_db),
              user: str = Depends(require_user)):
    if body.result not in bets.RESULTS:
        raise HTTPException(422, f"result must be one of {list(bets.RESULTS)}")
    out = bets.settle(db, bet_id, body.result, user)
    if out is None:
        raise HTTPException(404, "no such bet")
    return {"bet": out}


@router.delete("/bets/{bet_id}", response_model=OpenModel)
def delete_bet(bet_id: int, db: Session = Depends(get_db)):
    if not bets.remove(db, bet_id):
        raise HTTPException(404, "no such bet")
    return {"ok": True}
