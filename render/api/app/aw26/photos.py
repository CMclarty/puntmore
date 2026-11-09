"""AW 26's photo queue: slip photos waiting for Qwen on the Hub.

Render stores a photo (from the page or Telegram) and tracks its state; it
never reads one. Qwen takes a waiting photo, reads it on the Hub and records
the slip with `photo_id` (aw26/record.py), or fails it with a reason. A photo
taken and never finished is failed after STALE_AFTER, when the queue is next
listed (back to waiting while it has tries left): nothing runs in the
background.

    waiting -> reading -> (recorded) done | (receipt already in) duplicate
                       -> (fail) waiting while tries < MAX_TRIES, else failed
    failed -> (resend) waiting, tries 0

Each state change is one guarded UPDATE (`WHERE id = :id AND state IN ...`):
when another request moved the photo first, no row changes and the change is
refused (409; 404 when the photo is gone), so two requests never both win.
The caller commits.
"""
import logging
import os
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import case
from sqlalchemy.orm import Session

from ..config import settings
from ..images import prepare_for_ocr
from ..models import AwPhoto
from ..routers.upload import ALLOWED_MEDIA_TYPES
from . import record, service

logger = logging.getLogger("uvicorn.error")

MAX_TRIES = 3
STALE_AFTER = timedelta(minutes=15)
MAX_BATCH = 20
SOURCES = ("page", "telegram")


def store(db: Session, data: bytes, media_type: str, *, user: str, source: str) -> AwPhoto:
    """Write the photo, turned upright, under the upload directory and add its
    row, waiting. Nothing reads it. The row is flushed, not committed; if the
    write or the flush fails the file is removed, so neither is left without
    the other."""
    data, media_type = prepare_for_ocr(data, media_type)
    folder = Path(settings.upload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = str(folder / f"{uuid.uuid4().hex}{ALLOWED_MEDIA_TYPES[media_type]}")
    try:
        with open(path, "wb") as f:
            f.write(data)
        photo = AwPhoto(created_at=service.utcnow(), user=user, image_path=path, source=source,
                        state="waiting", tries=0)
        db.add(photo)
        db.flush()
    except Exception:
        remove_file(path)
        raise
    return photo


def _refuse(db: Session, photo_id: int, refusal) -> record.Refused:
    """The refusal for a change that moved no row: 404 when the photo is gone,
    else 409 with `refusal(<its state now>)`."""
    state = db.query(AwPhoto.state).filter(AwPhoto.id == photo_id).scalar()
    if state is None:
        return record.Refused(404, "No such photo")
    return record.Refused(409, refusal(state))


def _move(db: Session, photo_id: int, states: tuple[str, ...], refusal, **values) -> None:
    """Set `values` on the photo only while its state is one of `states`;
    Refused (`_refuse`) when no row changed."""
    changed = (db.query(AwPhoto).filter(AwPhoto.id == photo_id, AwPhoto.state.in_(states))
               .update(values, synchronize_session=False))
    if not changed:
        raise _refuse(db, photo_id, refusal)


# Back to waiting while tries are left, else failed.
_AFTER_A_FAIL = case((AwPhoto.tries < MAX_TRIES, "waiting"), else_="failed")


def take(db: Session, photo: AwPhoto) -> None:
    """Hand a waiting photo to Qwen: it is reading, from now, one more try."""
    _move(db, photo.id, ("waiting",), lambda state: f"Photo {photo.id} is {state}, not waiting",
          state="reading", taken_at=service.utcnow(), tries=AwPhoto.tries + 1)


def done(db: Session, photo: AwPhoto, slip_id: int) -> None:
    """The photo is slip `slip_id`'s picture: done, unless it already is one."""
    _move(db, photo.id, ("waiting", "reading", "failed", "duplicate"),
          lambda state: f"Photo {photo.id} is already a slip",
          state="done", slip_id=slip_id, reason=None)


def fail(db: Session, photo: AwPhoto, reason: str) -> None:
    """Qwen could not make a slip of it: keep the reason; back to waiting while
    tries are left, else failed until someone resends it."""
    _move(db, photo.id, ("waiting", "reading"), lambda state: f"Photo {photo.id} is {state}",
          state=_AFTER_A_FAIL, reason=reason)


def release_stale(db: Session) -> None:
    """Fail every photo taken more than STALE_AFTER ago and still reading."""
    cutoff = service.utcnow() - STALE_AFTER
    (db.query(AwPhoto).filter(AwPhoto.state == "reading", AwPhoto.taken_at < cutoff)
     .update({"state": _AFTER_A_FAIL, "reason": "Qwen did not finish"}, synchronize_session=False))


def resend(db: Session, photo: AwPhoto) -> None:
    """A failed photo goes back on the queue with its tries back to 0."""
    _move(db, photo.id, ("failed",), lambda state: f"Photo {photo.id} is {state}, not failed",
          state="waiting", tries=0, reason=None)


def delete(db: Session, photo: AwPhoto) -> None:
    """Delete the row unless it is done: a done photo's file is its slip's and
    goes when the slip is deleted (409). The caller removes the file."""
    gone = (db.query(AwPhoto).filter(AwPhoto.id == photo.id, AwPhoto.state != "done")
            .delete(synchronize_session=False))
    if not gone:
        raise _refuse(db, photo.id, lambda state: "The photo is a slip's: delete the slip")


def remove_file(path: str | None) -> None:
    """Delete a stored photo; a file already gone is not an error."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            logger.warning("aw26: could not delete photo %s", path)
