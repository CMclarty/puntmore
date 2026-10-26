"""How each site is treated. Code, not config: these are the guarantees.

paddypower     Logged out. Budget 15 loads/h, 120/day across ALL its targets.
               No traffic 01:00-07:00 (pages are closed, not just left idle).
               A block (403/429/Cloudflare) closes every Paddy page and starts
               a 24 h cool-off that also needs a manual `resume`.
bookiebashing  Your subscription, logged in through VNC. Budget 8/h, 120/day.
               No quiet hours. A block or logout alerts once and backs off
               (15 min doubling to 2 h) until you have sorted it over VNC.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    per_hour: int
    per_day: int
    quiet_hours: bool
    needs_login: bool
    block_means_cooloff: bool   # True: stop the site and wait for `resume`
                                # False: back off and retry on its own


SITES = {
    "paddypower": Site(per_hour=15, per_day=120, quiet_hours=True,
                       needs_login=False, block_means_cooloff=True),
    "bookiebashing": Site(per_hour=8, per_day=120, quiet_hours=False,
                          needs_login=True, block_means_cooloff=False),
}

BACKOFF_MIN = (15, 120)
