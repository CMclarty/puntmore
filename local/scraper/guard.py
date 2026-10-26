"""The traffic guard: every page load goes through here first.

State is in SQLite, so the budget and a Paddy Power cool-off hold across
restarts — a crash loop cannot spend tomorrow's budget, and restarting the
process does not end a cool-off.

A Paddy Power cool-off ends only when BOTH the hours have passed AND you have
run `python -m scraper resume paddy`. Either alone is not enough: the time
stops a resume-by-reflex, the resume makes a human look at why it happened.
"""
import sqlite3
import time
from datetime import datetime

HOUR = 3600


class Guard:
    def __init__(self, path, limits: dict, quiet=(1, 7), cooloff_hours: float = 24.0):
        """limits: {site: (per_hour, per_day)}; quiet: (start_hour, end_hour) local."""
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS loads (site TEXT, ts REAL, url TEXT);
            CREATE INDEX IF NOT EXISTS loads_site_ts ON loads(site, ts);
            CREATE TABLE IF NOT EXISTS cooloff (
                site TEXT PRIMARY KEY, since REAL, reason TEXT, resumed INTEGER);
        """)
        self.limits = limits
        self.quiet = quiet
        self.cooloff_hours = cooloff_hours

    # --- budget -----------------------------------------------------------
    def _count(self, site, since):
        return self.db.execute(
            "SELECT COUNT(*) FROM loads WHERE site=? AND ts>=?", (site, since)).fetchone()[0]

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
        per_hour, per_day = self.limits[site]
        if self._count(site, now - HOUR) >= per_hour:
            return False, f"hourly budget ({per_hour}) spent"
        if self._count(site, now - 24 * HOUR) >= per_day:
            return False, f"daily budget ({per_day}) spent"
        return True, ""

    def record_load(self, site, url, now=None):
        now = now or time.time()
        self.db.execute("INSERT INTO loads VALUES (?,?,?)", (site, now, url))
        self.db.execute("DELETE FROM loads WHERE ts < ?", (now - 2 * 24 * HOUR,))
        self.db.commit()

    def spent(self, site, now=None):
        now = now or time.time()
        return self._count(site, now - HOUR), self._count(site, now - 24 * HOUR)

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
