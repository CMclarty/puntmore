from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    We compute it tz-aware (the non-deprecated way) then drop the tzinfo so the
    value stays naive — matching the existing SQLite DateTime columns and the
    naive cutoff comparison in the cleanup job.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
