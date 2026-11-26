"""The traffic guard: every page load goes through here first.

State is in SQLite, so the budget and a Paddy Power cool-off hold across
restarts — a crash loop cannot spend tomorrow's budget, and restarting the
process does not end a cool-off.

A Paddy Power cool-off ends only when BOTH the hours have passed AND you have
run `python -m scraper resume paddy`. Either alone is not enough: the time
stops a resume-by-reflex, the resume makes a human look at why it happened.

Its limits (each site's loads an hour and a day, the quiet hours, the
cool-off's hours) are settings in behaviour.py. The guard takes them when it
is built (`from_behaviour`) and keeps its own copy; `use` replaces that copy,
which the runner does after each load of data/behaviour.toml.

A load made for a request (ask.py, `python -m scraper ask`) is recorded with
`requested=True`: it is in the record (`last_load`, `requested`) but not in
what the budget counts (`spent`, `can_load`), so a request never spends the
sweep's loads (Callum, 26 Nov 2026: a request disregards Paddy's budget). A
guard.sqlite3 from before has the column added in place, its rows kept as
the sweep's.
"""
import sqlite3
import time
from datetime import datetime

from . import behaviour
from .sites import SITES

HOUR = 3600


def from_behaviour() -> dict:
    """The guard's limits as behaviour.py has them now, as `use`'s arguments."""
    return {"limits": {site: (behaviour.get(f"{site}.per_hour"), behaviour.get(f"{site}.per_day"))
                       for site in SITES},
            "quiet": behaviour.get("quiet.hours"),
            "cooloff_hours": behaviour.get("cooloff.hours")}


class Guard:
    def __init__(self, path, limits: dict | None = None, quiet=None, cooloff_hours=None):
        """limits: {site: (per_hour, per_day)}; quiet: (start_hour, end_hour)
        local; cooloff_hours. Any not given is behaviour.py's (from_behaviour)."""
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS loads (site TEXT, ts REAL, url TEXT);
            CREATE INDEX IF NOT EXISTS loads_site_ts ON loads(site, ts);
            CREATE TABLE IF NOT EXISTS cooloff (
                site TEXT PRIMARY KEY, since REAL, reason TEXT, resumed INTEGER);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(loads)")}
        if "requested" not in columns:          # a file from before requests: rows kept
            self.db.execute("ALTER TABLE loads ADD COLUMN requested INTEGER NOT NULL DEFAULT 0")
            self.db.commit()
        now = from_behaviour()
        self.use(limits if limits is not None else now["limits"],
                 quiet if quiet is not None else now["quiet"],
                 cooloff_hours if cooloff_hours is not None else now["cooloff_hours"])

    def use(self, limits: dict, quiet, cooloff_hours: float):
        """The limits this guard holds every load to, from now on."""
        self.limits = dict(limits)
        self.quiet = tuple(quiet)
        self.cooloff_hours = float(cooloff_hours)

    def limit(self, site) -> tuple[int, int]:
        """(per_hour, per_day) for the site, as this guard holds it."""
        return self.limits[site]

    # --- budget -----------------------------------------------------------
    def _count(self, site, since, requested=False):
        """The site's loads since `since`: the sweep's, or the requested ones."""
        return self.db.execute(
            "SELECT COUNT(*) FROM loads WHERE site=? AND ts>=? AND requested=?",
            (site, since, int(requested))).fetchone()[0]

    def in_quiet(self, now=None) -> bool:
        h = datetime.fromtimestamp(now or time.time()).hour
        start, end = self.quiet
        return start <= h < end if start <= end else (h >= start or h < end)

    def can_load(self, site, now=None, *, quiet_applies=True) -> tuple[bool, str]:
        now = now or time.time()
        if self.cooloff_active(site, now):
            return False, "cooling off"
        if quiet_applies and self.in_quiet(now):
            return False, "quiet hours"
        per_hour, per_day = self.limit(site)
        if self._count(site, now - HOUR) >= per_hour:
            return False, f"hourly budget ({per_hour}) spent"
        if self._count(site, now - 24 * HOUR) >= per_day:
            return False, f"daily budget ({per_day}) spent"
        return True, ""

    def record_load(self, site, url, now=None, requested=False):
        """One load went out. `requested`: for a request (ask.py), which the
        budget does not count."""
        now = now or time.time()
        self.db.execute("INSERT INTO loads (site, ts, url, requested) VALUES (?,?,?,?)",
                        (site, now, url, int(requested)))
        self.db.execute("DELETE FROM loads WHERE ts < ?", (now - 2 * 24 * HOUR,))
        self.db.commit()

    def last_load(self, site, url) -> float | None:
        """When `url` was last loaded for `site` (kept two days), or None."""
        row = self.db.execute("SELECT MAX(ts) FROM loads WHERE site = ? AND url = ?",
                              (site, url)).fetchone()
        return row[0] if row else None

    def spent(self, site, now=None):
        """(this hour, today) of the site's budgeted loads: requested ones are not counted."""
        now = now or time.time()
        return self._count(site, now - HOUR), self._count(site, now - 24 * HOUR)

    def requested(self, site, now=None) -> tuple[int, int]:
        """(this hour, today) of the site's loads made for requests (ask.py)."""
        now = now or time.time()
        return (self._count(site, now - HOUR, requested=True),
                self._count(site, now - 24 * HOUR, requested=True))

    # --- cool-off ---------------------------------------------------------
    def start_cooloff(self, site, reason, now=None):
        self.db.execute("INSERT OR REPLACE INTO cooloff VALUES (?,?,?,0)",
                        (site, now or time.time(), reason))
        self.db.commit()

    def cooloff(self, site):
        row = self.db.execute(
            "SELECT since, reason, resumed FROM cooloff WHERE site=?", (site,)).fetchone()
        return None if row is None else {"since": row[0], "reason": row[1], "resumed": bool(row[2])}

    def cooloff_active(self, site, now=None) -> bool:
        c = self.cooloff(site)
        if c is None:
            return False
        served = (now or time.time()) - c["since"] >= self.cooloff_hours * HOUR
        if served and c["resumed"]:
            self.db.execute("DELETE FROM cooloff WHERE site=?", (site,))
            self.db.commit()
            return False
        return True

    def resume(self, site):
        self.db.execute("UPDATE cooloff SET resumed=1 WHERE site=?", (site,))
        self.db.commit()
