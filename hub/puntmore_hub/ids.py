"""The one tip id: what makes two sightings of a tip the same tip.

sha1 of source | event | market | selection | day (event, market and
selection stripped and lower-cased; day = the first 10 characters of the ISO
tip_time), first 16 hex characters. The Scraper mints it when a parser's
rows are handed to the Hub (`scraper/ids.py`, the same function: the Hub and
the Scraper never import each other, so each has its copy, and a test in
each suite pins the same vector). The Hub mints it only for an old drop file
that carries none. Render keys on it and never mints one.
"""
from __future__ import annotations

import hashlib


def tip_id_for(source_id: str, event: str, market: str, selection: str, tip_time: str) -> str:
    """Stable id so the same tip seen twice (or from two sweeps) is stored once."""
    key = f"{source_id}|{event.strip().lower()}|{market.strip().lower()}|{selection.strip().lower()}|{tip_time[:10]}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]
