"""How each site is treated. Code, not config: these are the guarantees.

Every site: page loads are spaced (load_gap_s x 1.0-1.8, and 20-45 s between
any two loads of any site), so nothing opens in a burst.

paddypower     Logged out. Budget 15 loads/h, 120/day across ALL its targets.
               No traffic 01:00-07:00 (pages are closed, not just left idle).
               A block (403/429/Cloudflare) closes every Paddy page and starts
               a 24 h cool-off that also needs a manual `resume`.
bookiebashing  Your subscription, logged in through VNC. Budget 8/h, 120/day.
               No quiet hours. A block or logout alerts once and backs off
               (15 min doubling to 2 h) until you have sorted it over VNC.
"""
from dataclasses import dataclass

# Paddy's page fetches a tab's markets (names, runners, ids) from its event-page
# and pub endpoints; getMarketPrices then polls their prices every few seconds.
# A response matching this after a market-tab click is what "loaded" means.
_PP_MARKET_LIST = r"/smspp/(event-page|pub)/v\d+"


@dataclass(frozen=True)
class Site:
    per_hour: int
    per_day: int
    quiet_hours: bool
    needs_login: bool
    block_means_cooloff: bool   # True: stop the site and wait for `resume`
                                # False: back off and retry on its own
    load_gap_s: float = 90.0    # at least this (x1.0-1.8) between two of its page loads
    market_list: str | None = None   # URL regex: a market tab's markets arrived


SITES = {
    "paddypower": Site(per_hour=15, per_day=120, quiet_hours=True,
                       needs_login=False, block_means_cooloff=True,
                       load_gap_s=90.0, market_list=_PP_MARKET_LIST),
    "bookiebashing": Site(per_hour=8, per_day=120, quiet_hours=False,
                          needs_login=True, block_means_cooloff=False,
                          load_gap_s=60.0),
}

BACKOFF_MIN = (15, 120)
