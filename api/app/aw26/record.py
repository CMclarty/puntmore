"""Record an AW 26 slip that was read OFF the server (no photo, no server OCR).

One implementation for both ways in: the key-guarded ingest the desktop
session uses (`POST /aw26/slips/ingest`, `ingest` grant) and the grant-gated
`POST /aw26/slips/record` an API client such as a future bet-placer uses to
RECORD a bet it has placed elsewhere. Recording only — nothing here places a
bet. Priced and settled on the spot, exactly like a photo upload.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AwSlip
from ..providers.slip_ocr import ExtractedSlip, ExtractedSlipLeg
from .marry import marry_slip
from .offers import OFFERS
from .service import build_slip, price_slip, recent_fairs, settle_slip


class Refused(ValueError):
    """The slip cannot be recorded. `status` is the HTTP answer to give."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def record_slip(db: Session, body, user: str, events: list[dict]) -> AwSlip:
    """Create, price, settle and commit one slip from `body` (an
    schemas.AwSlipRecordIn). A receipt already in is refused (409), so the same
    slip sent twice is never counted twice."""
    if body.offer not in OFFERS:
        raise Refused(400, f"offer must be one of {', '.join(OFFERS)}")
    if body.receipt and db.query(AwSlip).filter(AwSlip.receipt == body.receipt).first():
        raise Refused(409, f"Slip {body.receipt} is already in")

    read = ExtractedSlip(
        receipt=body.receipt,
        placed_at_text=body.placed_at,
        bet_type=None,
        stake=body.stake,
        potential_returns=None,
        legs=[ExtractedSlipLeg(event=lg.event, market=lg.market, selection=lg.selection,
                               odds=lg.odds, kickoff_text=lg.kickoff) for lg in body.legs],
    )
    slip = build_slip(read, offer=body.offer, user=user, image_path=None, events=events)
    # A fair looked up at read time stands; the rest are matched below.
    for leg, given in zip(slip.legs, body.legs):
        if given.fair_odds is not None:
            leg.fair_odds = given.fair_odds
    marry_slip(db, slip)
    price_slip(slip, recent_fairs(db))
    settle_slip(slip)
    db.add(slip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Refused(409, f"Slip {body.receipt} is already in")
    db.refresh(slip)
    return slip
