"""Private P&L bet tracker — per-user ledgers of settled bets.

Router-gated by require_tool("pnl") (main.py), and every row is scoped to the
session user, so a user only ever sees and edits their own bets. The API is
deliberately thin: it stores/returns raw bets and the frontend derives the
cumulative total, month-to-date and the chart from them.

There are two independent ledgers per user — "dogs" (greyhounds) and "virtuals"
(virtual racing) — kept in one table and split by the {category} path segment.
Every query filters on (user, category), so the two never bleed into each other.
"""
import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Bet
from ..schemas import BetOut
from ..security import require_user

router = APIRouter(prefix="/pnl", tags=["pnl"])


LIMIT = 1_000_000

# The two ledgers. A request for anything else is a 404, not a silently-empty
# third ledger — a typo'd path should fail loudly rather than swallow bets.
CATEGORIES = ("dogs", "virtuals")


def valid_category(category: str) -> str:
    """Resolve and validate the {category} path segment to a known ledger."""
    if category not in CATEGORIES:
        raise HTTPException(status_code=404, detail="Unknown ledger")
    return category


class BetIn(BaseModel):
    """One bet, in either entry mode.

    Flat: `amount` alone — the net result, positive = win, negative = loss.
    Detailed: `stake` + `returned` — the net is derived (returned − stake) so
    the rest of the app keeps reading one field. Exactly one mode per request;
    sending `amount` alongside stake/return is rejected rather than silently
    preferring one, since a mismatch between them has no right answer.
    Bounded so a fat-fingered keypad entry can't store a nonsense figure.
    """
    amount: float | None = Field(default=None, gt=-LIMIT, lt=LIMIT)
    stake: float | None = Field(default=None, ge=0, lt=LIMIT)
    returned: float | None = Field(default=None, ge=0, lt=LIMIT)
    # Decimal odds, so 1.01 is the floor — 1.0 would be a bet that returns the
    # stake. Bounded at the top of the Betfair ladder.
    price_taken: float | None = Field(default=None, ge=1.01, le=1000)
    bfsp: float | None = Field(default=None, ge=1.01, le=1000)
    # Each-way. `stake` is then the TOTAL outlay (both parts), and
    # place_fraction the terms the place part paid at — 0.25 = 1/4 odds.
    each_way: bool = False
    place_fraction: float | None = Field(default=None, gt=0, le=1)
    # A staff tip: a cash loss, not a bet (see model). Amount-mode only, and it
    # carries none of the bet detail — the flag is what keeps it out of the
    # win rate and turnover.
    is_tip: bool = False

    @model_validator(mode="after")
    def _one_mode(self):
        # A tip is a plain cash movement: an amount, nothing else. Reject any bet
        # detail on it so a tip can never be miscounted as a wager, and require
        # the amount to be a loss — a tip is money OUT of the till.
        if self.is_tip:
            if self.amount is None:
                raise ValueError("A tip needs an amount")
            if round(self.amount, 2) >= 0:
                raise ValueError("A tip is a cash loss, so its amount must be negative")
            if any(v is not None for v in (self.stake, self.returned,
                                           self.price_taken, self.bfsp)):
                raise ValueError("A tip carries no stake or price")
            if self.each_way or self.place_fraction is not None:
                raise ValueError("A tip is not each-way")
            return self
        detailed = self.stake is not None or self.returned is not None
        if detailed:
            if self.amount is not None:
                raise ValueError("Send either amount, or stake and returned — not both")
            if self.stake is None or self.returned is None:
                raise ValueError("Stake/return entry needs both stake and returned")
            if round(self.stake, 2) <= 0:
                raise ValueError("Stake must be greater than zero")
        elif self.amount is None:
            raise ValueError("Send either amount, or stake and returned")
        # An each-way bet without its place terms can't be reconstructed later,
        # and terms without each_way describe a bet that has no place part —
        # both are stored-but-meaningless, so neither is accepted.
        if self.each_way and self.place_fraction is None:
            raise ValueError("An each-way bet needs place_fraction")
        if self.place_fraction is not None and not self.each_way:
            raise ValueError("place_fraction only applies to an each-way bet")
        return self

    def net(self) -> float:
        """The net result in pennies-rounded form, whichever mode was used."""
        if self.amount is not None:
            return round(self.amount, 2)
        return round(round(self.returned, 2) - round(self.stake, 2), 2)


@router.get("/{category}/bets", response_model=list[BetOut])
def list_bets(
    category: str = Depends(valid_category),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """This ledger's bets, oldest first — the order the chart accumulates in."""
    return (
        db.query(Bet)
        .filter(Bet.user == user, Bet.category == category)
        .order_by(Bet.created_at.asc(), Bet.id.asc())
        .all()
    )


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
    bet = Bet(
        user=user,
        category=category,
        amount=body.net(),
        stake=None if body.stake is None else round(body.stake, 2),
        returned=None if body.returned is None else round(body.returned, 2),
        price_taken=None if body.price_taken is None else round(body.price_taken, 2),
        bfsp=None if body.bfsp is None else round(body.bfsp, 2),
        each_way=body.each_way,
        place_fraction=body.place_fraction,
        is_tip=body.is_tip,
    )
    db.add(bet)
    db.commit()
    db.refresh(bet)
    return bet


CSV_COLUMNS = [
    "id", "created_at", "amount", "stake", "returned",
    "price_taken", "bfsp", "vs_sp_pct", "each_way", "place_fraction",
    "is_tip",
]


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
    bets = (
        db.query(Bet)
        .filter(Bet.user == user, Bet.category == category)
        .order_by(Bet.created_at.asc(), Bet.id.asc())
        .all()
    )

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for b in bets:
        vs_sp = ""
        if b.price_taken is not None and b.bfsp is not None:
            vs_sp = f"{(b.price_taken / b.bfsp - 1) * 100:.2f}"
        writer.writerow([
            b.id,
            b.created_at.isoformat() if b.created_at else "",
            f"{b.amount:.2f}",
            "" if b.stake is None else f"{b.stake:.2f}",
            "" if b.returned is None else f"{b.returned:.2f}",
            "" if b.price_taken is None else f"{b.price_taken:.2f}",
            "" if b.bfsp is None else f"{b.bfsp:.2f}",
            vs_sp,
            "1" if b.each_way else "0",
            "" if b.place_fraction is None else f"{b.place_fraction:.4f}",
            "1" if b.is_tip else "0",
        ])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"puntmore-pnl-{category}-{user}-{stamp}.csv"
    return Response(
        content=buf.getvalue(),
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
