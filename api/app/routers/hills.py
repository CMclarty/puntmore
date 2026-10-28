"""Hills' own endpoints, beside the shared Paddy ones it mounts at /hills:

  GET    /hills/fixtures   what the target can be (target.eligible)
  GET    /hills/target     the team's target match, or null
  PUT    /hills/target     set it: {fixture_id, boost_pct}
  DELETE /hills/target     clear it
  GET    /hills/builder    BB Boost: the target's builders at or above min_fair

Router-gated by require_tool("hills") in main.py. The target is one row for
the whole team (models.HillsTarget), not per user.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..hills import builder, target
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


@router.get("/builder", response_model=OpenModel)
def get_builder(request: Request, db: Session = Depends(get_db),
                min_fair: float = Query(default=2.0, ge=1.0, le=1000.0)):
    row = target.current(db)
    if row is None:
        return {"target": None, "legs": 0, "combos": []}
    src = _source(request)
    legs = builder.legs_for(src.get_events() if src else [], row.fixture_id)
    return {"target": target.as_dict(row), "legs": len(legs),
            "combos": builder.combos(legs, min_fair)}
