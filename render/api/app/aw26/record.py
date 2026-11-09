"""Record an AW 26 slip: `POST /aw26/slips/record`, behind the `aw26` grant.

HubAI calls it with an API client key granted `aw26` (the slip is filed under
"api:<name>"); a signed-in session with the grant may too. Recording only —
nothing here places a bet. Priced and settled on the spot from what the body
carries.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AwSlip
from .offers import OFFERS
from .service import build_slip, price_slip, settle_slip


class Refused(ValueError):
    """The slip cannot be recorded. `status` is the HTTP answer to give."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def record_slip(db: Session, body, user: str) -> AwSlip:
    """Create, price, settle and commit one slip from `body` (a
    schemas.AwSlipRecordIn). A receipt already in is refused (409), so the same
    slip sent twice is never counted twice."""
    if body.offer not in OFFERS:
        raise Refused(400, f"offer must be one of {', '.join(OFFERS)}")
    if body.receipt and db.query(AwSlip).filter(AwSlip.receipt == body.receipt).first():
        raise Refused(409, f"Slip {body.receipt} is already in")

    slip = build_slip(body, user=user)
    price_slip(slip)
    settle_slip(slip)
    db.add(slip)
    try:
        db.commit()
    except IntegrityError:
        # Two records of one receipt racing: the unique index refuses the second.
        db.rollback()
        raise Refused(409, f"Slip {body.receipt} is already in")
    db.refresh(slip)
    return slip
