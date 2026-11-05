"""The one tip id, minted where a tip is first seen: here, when a parser's
rows are handed to the Hub (sinks.deliver_to_hub).

sha1 of source | event | market | selection | day (event, market and
selection stripped and lower-cased; day = the first 10 characters of the ISO
tip_time), first 16 hex characters. The Hub has the same function in
`hub/puntmore_hub/ids.py` (the two never import each other); a test in each
suite pins the same vector, so a change to one without the other fails.
"""
import hashlib
from datetime import datetime, timezone


def tip_id_for(source_id: str, event: str, market: str, selection: str, tip_time: str) -> str:
    key = f"{source_id}|{event.strip().lower()}|{market.strip().lower()}|{selection.strip().lower()}|{tip_time[:10]}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def iso_utc(ts: float) -> str:
    """A drop's `at` as the Hub reads it when a row has no tip_time."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def with_tip_ids(source: str, rows: list, at: float) -> list:
    """The rows, each with its `tip_id`. A row without event, market and
    selection gets none (the Hub rejects it anyway); a row's own tip_time
    sets its day, else the drop's time (UTC), as the Hub does."""
    out = []
    for row in rows:
        row = dict(row)
        if source and all(row.get(k) for k in ("event", "market", "selection")):
            row["tip_id"] = tip_id_for(source, str(row["event"]), str(row["market"]),
                                       str(row["selection"]), str(row.get("tip_time") or iso_utc(at)))
        out.append(row)
    return out
