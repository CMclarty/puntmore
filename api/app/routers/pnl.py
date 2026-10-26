"""Private P&L bet tracker — per-user ledgers of settled bets.

Router-gated by require_tool("pnl") (main.py), and every row is scoped to the
session user, so a user only ever sees and edits their own bets. The API is
deliberately thin: it stores/returns raw bets and the frontend derives the
cumulative total, month-to-date and the chart from them.

There are two independent ledgers per user — "dogs" (greyhounds) and "virtuals"
(virtual racing) — kept in one table and split by the {category} path segment.
Every query filters on (user, category), so the two never bleed into each other.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Bet
from ..pnl.service import (  # noqa: F401  (re-exported: they lived here)
    CATEGORIES, CSV_COLUMNS, LIMIT, BetIn, bets_csv, ledger, new_bet,
)
from ..schemas import BetOut
from ..security import require_user

router = APIRouter(prefix="/pnl", tags=["pnl"])


def valid_category(category: str) -> str:
    """Resolve and validate the {category} path segment to a known ledger."""
    if category not in CATEGORIES:
        raise HTTPException(status_code=404, detail="Unknown ledger")
    return category


@router.get("/{category}/bets", response_model=list[BetOut])
def list_bets(
    category: str = Depends(valid_category),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """This ledger's bets, oldest first — the order the chart accumulates in."""
    return ledger(db, user, category)


@router.post("/{category}/bets", response_model=BetOut)
def add_bet(
    body: BetIn,
    category: str = Depends(valid_category),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    # A zero net is meaningless as a flat entry — there's nothing to record. In
    # stake/return mode it's a push (returned == stake), a real settlement the
    # user typed both sides of, so it's kept: the stake still counts as turnover.
    if body.amount is not None and round(body.amount, 2) == 0:
        raise HTTPException(status_code=400, detail="A bet result cannot be zero")
    bet = new_bet(body, user, category)
    db.add(bet)
    db.commit()
    db.refresh(bet)
    return bet


@router.get("/{category}/bets.csv")
def export_bets_csv(
    category: str = Depends(valid_category),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """This ledger's full history as CSV, oldest first.

    Scoped to the session user and category like every other route here — there
    is no way to export someone else's ledger, or to mix the two, by design.

    Empty cells mean "not recorded", never zero: a flat win/loss entry has no
    stake, and vs_sp_pct exists only where both prices were captured. Writing 0
    for those would misread as a real measurement once the file is in a
    spreadsheet, away from the UI that explains the difference.
    """
    bets = ledger(db, user, category)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"puntmore-pnl-{category}-{user}-{stamp}.csv"
    return Response(
        content=bets_csv(bets),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{category}/bets/{bet_id}", status_code=204)
def delete_bet(
    bet_id: int,
    category: str = Depends(valid_category),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """Remove a bet — used to undo a mistaken entry. Scoped to owner + ledger."""
    bet = (
        db.query(Bet)
        .filter(Bet.id == bet_id, Bet.user == user, Bet.category == category)
        .first()
    )
    if bet is None:
        raise HTTPException(status_code=404, detail="Bet not found")
    db.delete(bet)
    db.commit()
