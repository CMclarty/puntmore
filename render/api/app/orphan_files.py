"""The 04:00 sweep of the upload folder (Callum, 10 Nov 2026).

Coupon photos and AW 26 photos share `settings.upload_dir`. A file can be left
with no row pointing at it: an upload cancelled while it was being stored, or
a delete whose file removal failed. Once a day, at 04:00 London, when Callum
is never uploading, every file in the folder that no row points at is
removed. A row points at a file through `coupons.image_path`,
`aw_slips.image_path` or `aw_photos.image_path`; files are matched on their
name (every stored name is a fresh uuid), so a relative and an absolute
spelling of the folder never cost a file. A file written in the last hour is
left alone whatever the time, and so is anything that is not a plain file.
Rows are never touched: this only removes files nothing refers to.
"""
import logging
import os
import time

from sqlalchemy.orm import Session

from .config import settings
from .models import AwPhoto, AwSlip, Coupon

logger = logging.getLogger("uvicorn.error")

MIN_AGE_SECONDS = 3600


def _names_in_use(db: Session) -> set[str]:
    names = set()
    for column in (Coupon.image_path, AwSlip.image_path, AwPhoto.image_path):
        for (path,) in db.query(column).filter(column.isnot(None)).distinct():
            names.add(os.path.basename(path))
    return names


def sweep(db: Session) -> int:
    """Remove every plain file in the upload folder that no row points at and
    that is more than an hour old; returns how many went."""
    folder = settings.upload_dir
    if not os.path.isdir(folder):
        return 0
    in_use = _names_in_use(db)
    cutoff = time.time() - MIN_AGE_SECONDS
    removed = 0
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.name in in_use or not entry.is_file(follow_symlinks=False):
                continue
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
                os.remove(entry.path)
                removed += 1
            except OSError:
                logger.warning("orphan files: could not remove %s", entry.path)
    if removed:
        logger.info("orphan files: removed %d file(s) no row points at", removed)
    return removed
