"""What the Scraper says about itself, for the Hub and `python -m scraper status`.

  heartbeat     data/heartbeat, the time of the last tick (and an optional
                healthchecks ping every HEALTH_PING_S)
  status.json   per target: state, detail, last load/parse, rows posted; per
                site: budget spent and cool-off. Written every STATUS_EVERY_S.

Nothing here is sent off the box except the healthcheck ping.
"""
import collections
import json

from .sites import SITES

HEALTH_PING_S = 300
STATUS_EVERY_S = 60


class Reporter:
    def __init__(self, settings):
        self.s = settings
        self.next_health = 0.0
        self.next_status = 0.0

    def heartbeat(self, now):
        (self.s.data_dir / "heartbeat").write_text(str(int(now)))
        if self.s.healthcheck_url and now >= self.next_health:
            self.next_health = now + HEALTH_PING_S
            import httpx
            try:
                httpx.get(self.s.healthcheck_url, timeout=10)
            except Exception:
                pass

    def report(self, now, build):
        """Write status.json if due; `build(now)` makes the body."""
        if now < self.next_status:
            return
        self.next_status = now + STATUS_EVERY_S
        body = build(now)
        try:
            (self.s.data_dir / "status.json").write_text(json.dumps(body, indent=1))
        except OSError:
            pass


def target_entry(t, state, detail, last_load, last_parse, rows_posted,
                 next_kickoff=None, near=False) -> dict:
    return {"name": t.name, "site": t.site, "url": t.url, "state": state, "mode": t.mode,
            "detail": detail, "last_load": last_load, "last_parse": last_parse,
            "rows_posted": rows_posted, "next_kickoff": next_kickoff, "near": bool(near)}


def site_status(guard, now) -> list[dict]:
    """Budget spent and cool-off per site, off the guard's SQLite."""
    out = []
    for site, rules in SITES.items():
        h, d = guard.spent(site, now)
        c = guard.cooloff(site)
        cooling = bool(c) and guard.cooloff_active(site, now)
        left = (max(0.0, guard.cooloff_hours * 3600 - (now - c["since"])) / 3600) if cooling else None
        out.append({"site": site, "loads_hour": h, "per_hour": rules.per_hour,
                    "loads_day": d, "per_day": rules.per_day,
                    "cooloff": c["reason"] if cooling else None,
                    "cooloff_left_h": None if left is None else round(left, 2),
                    "needs_resume": bool(cooling and not c["resumed"])})
    return out


def market_tabs_said(tabs) -> str:
    """'market tabs: Corners loaded 2/3, Cards not found 1/3' across a
    target's open match tabs, or '' when none has clicked any."""
    clicked = [tab for tab in tabs if tab.market_tabs]
    if not clicked:
        return ""
    counts = collections.Counter((name, outcome) for tab in clicked
                                 for name, outcome in tab.market_tabs.items())
    return "market tabs: " + ", ".join(f"{name} {outcome} {c}/{len(clicked)}"
                                       for (name, outcome), c in sorted(counts.items()))
