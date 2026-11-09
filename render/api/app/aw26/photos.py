"""AW 26's photo queue: slip photos waiting for Qwen on the Hub.

Render stores a photo (from the page or Telegram) and tracks its state; it
never reads one. Qwen takes a waiting photo, reads it on the Hub and records
the slip with `photo_id` (aw26/record.py), or fails it with a reason. A photo
taken and never finished is failed after STALE_AFTER, when the queue is next
listed: nothing runs in the background.

    waiting -> reading -> (recorded) done | (receipt already in) duplicate
                       -> (fail) waiting while tries < MAX_TRIES, else failed
    failed -> (resend) waiting, tries 0

These change the row; the caller commits.
"""
import logging
import os
import uuid
from datetime import timedelta
from pathlib import Path

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
    flush fails the file is removed, so neither is left without the other."""
    data, media_type = prepare_for_ocr(data, media_type)
    folder = Path(settings.upload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = str(folder / f"{uuid.uuid4().hex}{ALLOWED_MEDIA_TYPES[media_type]}")
    with open(path, "wb") as f:
        f.write(data)
    photo = AwPhoto(created_at=service.utcnow(), user=user, image_path=path, source=source,
                    state="waiting", tries=0)
    db.add(photo)
    try:
        db.flush()
    except Exception:
        remove_file(path)
        raise
    return photo


def take(photo: AwPhoto) -> None:
    """Hand a waiting photo to Qwen: it is reading, from now, one more try."""
    if photo.state != "waiting":
        raise record.Refused(409, f"Photo {photo.id} is {photo.state}, not waiting")
    photo.state = "reading"
    photo.taken_at = service.utcnow()
    photo.tries += 1


def fail(photo: AwPhoto, reason: str) -> None:
    """Qwen could not make a slip of it: keep the reason; back to waiting while
    tries are left, else failed until someone resends it."""
    if photo.state not in ("waiting", "reading"):
        raise record.Refused(409, f"Photo {photo.id} is {photo.state}")
    photo.reason = reason
    photo.state = "waiting" if photo.tries < MAX_TRIES else "failed"


def release_stale(db: Session) -> None:
    """Fail every photo taken more than STALE_AFTER ago and still reading."""
    cutoff = service.utcnow() - STALE_AFTER
    for photo in (db.query(AwPhoto)
                  .filter(AwPhoto.state == "reading", AwPhoto.taken_at < cutoff).all()):
        fail(photo, "Qwen did not finish")


def resend(photo: AwPhoto) -> None:
    """A failed photo goes back on the queue with its tries back to 0."""
    if photo.state != "failed":
        raise record.Refused(409, f"Photo {photo.id} is {photo.state}, not failed")
    photo.state = "waiting"
    photo.tries = 0
    photo.reason = None


def remove_file(path: str | None) -> None:
    """Delete a stored photo; a file already gone is not an error."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            logger.warning("aw26: could not delete photo %s", path)
