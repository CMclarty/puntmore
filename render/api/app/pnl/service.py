"""P&L ledger logic that needs no FastAPI — routers/pnl.py calls it.

The entry rules (BetIn), the two ledgers and the CSV export, moved out of the
router (Oct 2026). Behaviour unchanged.
"""
import csv
import io

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..models import Bet


LIMIT = 1_000_000

# The two ledgers. A request for anything else is a 404, not a silently-empty
# third ledger — a typo'd path should fail loudly rather than swallow bets.
CATEGORIES = ("dogs", "virtuals")


def ledger(db: Session, user: str, category: str) -> list[Bet]:
    """One user's ledger, oldest first — the order the chart accumulates in."""
    return (
        db.query(Bet)
        .filter(Bet.user == user, Bet.category == category)
        .order_by(Bet.created_at.asc(), Bet.id.asc())
        .all()
    )


def new_bet(body: "BetIn", user: str, category: str) -> Bet:
    """The row for one entry (not yet added to a session)."""
    return Bet(
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
        ev=None if body.ev is None else round(body.ev, 2),
    )


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
    # Virtuals: the boost model's expected profit in pounds on this outlay
    # (see the model). Needs a stake — an EV with nothing at risk is a number
    # about nothing — and is bounded by it: no bet expects to lose more than
    # its outlay.
    ev: float | None = Field(default=None, gt=-LIMIT, lt=LIMIT)

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
                                           self.price_taken, self.bfsp, self.ev)):
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
        if self.ev is not None:
            if self.stake is None:
                raise ValueError("An EV needs the stake it was measured on")
            if self.ev < -self.stake:
                raise ValueError("An EV cannot lose more than the stake")
        return self

    def net(self) -> float:
        """The net result in pennies-rounded form, whichever mode was used."""
        if self.amount is not None:
            return round(self.amount, 2)
        return round(round(self.returned, 2) - round(self.stake, 2), 2)


CSV_COLUMNS = [
    "id", "created_at", "amount", "stake", "returned",
    "price_taken", "bfsp", "vs_sp_pct", "each_way", "place_fraction",
    "is_tip", "ev",
]


def bets_csv(bets: list[Bet]) -> str:
    """The ledger as CSV. Empty cells mean "not recorded", never zero."""
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
            "" if b.ev is None else f"{b.ev:.2f}",
        ])
    return buf.getvalue()
