"""First-goalscorer value feed — reads rows the scheduler stored near kickoff.

Unlike the AccaBonus/Cards feeds, this does NOT compute on request: Pinnacle
only prices first goalscorer close to KO, so scanning at request time would
almost always return nothing (and burn per-event credits). The scheduler scans
at kickoff − 45 min and stores the value rows; this endpoint just serves them.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..goalscorers.value import NAME_MATCH_CONFIDENT
from ..models import GoalscorerAlert
from ..schemas import GoalscorerFeedItem

router = APIRouter()


@router.get("/goalscorers/feed", response_model=list[GoalscorerFeedItem])
def get_goalscorer_feed(db: Session = Depends(get_db)):
    """Current first-goalscorer value rows, best edge first."""
    rows = (
        db.query(GoalscorerAlert)
        .order_by(GoalscorerAlert.edge.desc())
        .all()
    )
    return [
        GoalscorerFeedItem(
            match=r.match_name,
            commence_time=r.commence_time.isoformat() if r.commence_time else None,
            player=r.player,
            matched_player=r.matched_player,
            match_score=r.match_score,
            low_confidence=(
                r.match_score is not None and r.match_score < NAME_MATCH_CONFIDENT
            ),
            bet365_price=r.bet365_price,
            pinnacle_price=r.pinnacle_price,
            fair_price=r.fair_price,
            edge=r.edge,
            inplay=r.inplay,
        )
        for r in rows
    ]
