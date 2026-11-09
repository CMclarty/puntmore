"""Record an AW 26 slip: `POST /aw26/slips/record`, behind the `aw26` grant.

HubAI calls it with an API client key granted `aw26` (the slip is filed under
"api:<name>"); a signed-in session with the grant may too. Recording only —
nothing here places a bet. Priced and settled on the spot from what the body
carries.

A record with `photo_id` is Qwen's reading of a queued photo (aw26/photos.py):
the photo becomes the slip's picture and is done in the same commit, or, if
the receipt is already in, a duplicate. A photo read with no receipt is failed
and nothing recorded, since without one the same slip could be counted twice.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AwPhoto, AwSlip
from . import photos
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
    photo = None
    if body.photo_id is not None:
        photo = db.get(AwPhoto, body.photo_id)
        if photo is None:
            raise Refused(404, "No such photo")
        if photo.state == "done":
            raise Refused(409, f"Photo {photo.id} is already a slip")
    if body.offer not in OFFERS:
        raise Refused(400, f"offer must be one of {', '.join(OFFERS)}")
    if photo is not None and not body.receipt:
        if photo.state in ("waiting", "reading"):
            photos.fail(photo, "no receipt number")
            db.commit()
        raise Refused(400, "no receipt number")
    if body.receipt and db.query(AwSlip).filter(AwSlip.receipt == body.receipt).first():
        _duplicate(db, body.photo_id, body.receipt)

    slip = build_slip(body, user=user)
    price_slip(slip)
    settle_slip(slip)
    if photo is not None:
        slip.image_path = photo.image_path
    db.add(slip)
    try:
        db.flush()                                 # the slip's id, for the photo
        if photo is not None:
            photo.state, photo.slip_id, photo.reason = "done", slip.id, None
        db.commit()
    except IntegrityError:
        # Two records of one receipt racing: the unique index refuses the second.
        db.rollback()
        _duplicate(db, body.photo_id, body.receipt)
    db.refresh(slip)
    return slip


def _duplicate(db: Session, photo_id: int | None, receipt: str):
    """Refuse a receipt already in (409); a photo it was read from is marked a
    duplicate, with the refusal's words as its reason."""
    refusal = Refused(409, f"Slip {receipt} is already in")
    photo = db.get(AwPhoto, photo_id) if photo_id is not None else None
    if photo is not None:
        photo.state, photo.reason = "duplicate", str(refusal)
        db.commit()
    raise refusal
