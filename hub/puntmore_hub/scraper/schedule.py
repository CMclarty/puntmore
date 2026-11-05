"""Decide whether a source is due. Plain rules an agent can read in the YAML."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .sources import Source

_UNIT = {"m": 60, "h": 3600, "d": 86400}


def parse_every(spec: str) -> timedelta:
    m = re.fullmatch(r"\s*(\d+)\s*([mhd])\s*", spec)
    if not m:
        raise ValueError(f"bad interval {spec!r}; use e.g. 30m, 2h, 1d")
    return timedelta(seconds=int(m.group(1)) * _UNIT[m.group(2)])


def is_due(source: Source, last_run: datetime | None, now: datetime, tz: str = "Europe/London") -> bool:
    sched = source.schedule
    if sched.every:
        return last_run is None or now - last_run >= parse_every(sched.every)
    if sched.at:
        local_now = now.astimezone(ZoneInfo(tz))
        for hhmm in sched.at:
            hh, mm = (int(x) for x in hhmm.split(":"))
            slot = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if local_now >= slot and (last_run is None or last_run.astimezone(ZoneInfo(tz)) < slot):
                return True
        return False
    return last_run is None   # no schedule: run once, then only on demand
