"""Deleting a coupon's stored image.

Its own module because TWO callers must agree on the rule — the upload path,
when a new issue supersedes an old one, and the scheduled cleanup — and because
importing a router into the scheduler to share it would be backwards.

The rule: a coupon's row and its image file go together, never one without the
other. What makes that non-trivial is that ONE photo of a sheet backs SEVERAL
coupons — a sheet prints six markets over one fixture list, and an upload builds
a coupon for each block it carries. They share the stored image so any of them
can be re-scanned, so the file belongs to the last row pointing at it, not the
first to be deleted.
"""
import logging
import os

from .models import Coupon

logger = logging.getLogger("uvicorn.error")


def delete_coupon_image(db, coupon) -> bool:
    """Remove a coupon's image file unless another coupon still points at it.

    Returns True when the file was removed. Callers delete the row either way —
    a file that is missing, shared, or unremovable must never strand the row,
    since a row and a file going out of step is what leaves orphans behind.
    """
    path = coupon.image_path
    if not path:
        return False
    shared_with = (
        db.query(Coupon)
        .filter(Coupon.image_path == path, Coupon.id != coupon.id)
        .count()
    )
    if shared_with:
        return False
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        logger.warning("Could not delete image for coupon %s", coupon.id)
        return False
