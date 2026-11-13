"""How each site is treated: what a block means, and whether it needs a login.

These are code, not settings. Its limits (loads an hour and a day, the least
gap between two of its loads, whether it keeps quiet hours) and every other
number about how the Scraper behaves are in behaviour.py, read when they are
used; the defaults are given below as they stand there.

Every site: page loads are spaced (load_gap_s x load.site_gap_x, and
load.any_gap_s between any two loads of any site), so nothing opens in a burst.

paddypower     Logged out. 15 loads/h, 120/day across ALL its targets by
               default. No traffic in quiet hours (01:00-07:00 by default;
               pages are closed, not just left idle).
               A block (403/429/Cloudflare) closes every Paddy page and starts
               a cool-off (cooloff.hours, 24 h by default) that also needs a
               manual `resume`.
bookiebashing  Your subscription. The Scraper signs in itself with the login in
               its .env (BB_USERNAME, BB_PASSWORD; login.py), once per logout,
               on the page already open. 8/h, 120/day by default. No quiet
               hours. A block, a failed sign-in, or a logout with no login in
               .env alerts once and backs off (backoff.min: 15 min doubling to
               2 h by default) until a person has sorted it (over VNC only in
               the Docker fallback).
               Its jobs are Callum's to add (docs/decisions.md; the rule that
               bb-saves was the only one was removed on 12 Nov 2026).
teamcasino     Callum's subscription (casino reload offers; 13 Nov 2026). The
               Scraper signs in itself with the login in its .env (TC_USERNAME,
               TC_PASSWORD; login.py), once per logout, on the page already
               open. Its members page keeps its url when logged out and shows
               the login form instead (`logged_out` below). 6/h, 30/day by
               default. No quiet hours. As Bookie Bashing: a block, a failed
               sign-in, or a logout with no login in .env alerts once and backs
               off, with the tab left open, until a person has sorted it (a
               failed sign-in: logs in on the Scraper's screen, or runs
               `resume teamcasino`, which gives one more try at once).
generic        Any other site: Finder's sources (tipster pages, listings) and any
               other page a tool needs.
               Never Paddy, Bookie Bashing or Team Casino. 60/h, 400/day across ALL generic
               targets by default, 20 s+ between loads. A target is loaded,
               parsed and closed - nothing kept open. A block (403/429/
               Cloudflare) starts the cool-off for every generic target and
               needs `resume generic`.
"""
from dataclasses import dataclass

# Paddy's page fetches a tab's markets (names, runners, ids) from its event-page
# and pub endpoints; getMarketPrices then polls their prices every few seconds.
# A response matching this after a market-tab click is what "loaded" means.
_PP_MARKET_LIST = r"/smspp/(event-page|pub)/v\d+"
# Paddy prices its betslip at every page load; the response lists what is in it
# ("betCombinations"). Seen on 12 Nov 2026 with a selection nobody chose in it.
_PP_BETSLIP = r"/transactional/v\d+/implyBets"
# A match page prices its own "Most Popular Bet Builders" cards with the same
# call, marked pricePolicy=SUGGESTED (seen 12 Nov 2026): not the betslip.
_PP_BETSLIP_NOT = r"[?&]pricePolicy=SUGGESTED"


@dataclass(frozen=True)
class Site:
    needs_login: bool
    block_means_cooloff: bool   # True: stop the site and wait for `resume`
                                # False: back off and retry on its own
    market_list: str | None = None   # URL regex: a market tab's markets arrived
    betslip: str | None = None       # URL regex: the page pricing its betslip (never meant to hold anything)
    betslip_not: str | None = None   # URL regex: the same call when it is NOT the betslip
    # CSS: shown on the site's page only to a logged-out visitor, for a site
    # whose url says nothing when logged out; None: browser.py's general rule
    # (a visible password field, or a login path).
    logged_out: str | None = None


# The keys are the sites; behaviour.py has the same ones under "Limits, per site".
SITES = {
    "paddypower": Site(needs_login=False, block_means_cooloff=True,
                       market_list=_PP_MARKET_LIST, betslip=_PP_BETSLIP,
                       betslip_not=_PP_BETSLIP_NOT),
    "bookiebashing": Site(needs_login=True, block_means_cooloff=False),
    # Its members page (reload-offers-dashboard) answers a logged-out visit at the
    # same url, with "Please Log In To View This Page" and the login form shown
    # (the page saved on 13 Nov 2026). Its forms are the mark, not a password
    # field: a logged-in page may well have one of its own. Either form shown
    # is logged out: the page's "Forgot Password?" (resetBtn()) hides the login
    # form and shows the reset form in its place, which is still logged out -
    # read as logged in, a person clicking it over VNC would clear a failed
    # sign-in's note. A page with neither form shown reads as logged in.
    "teamcasino": Site(needs_login=True, block_means_cooloff=False,
                       logged_out="form#loginrequest:visible, form#resetrequest:visible"),
    "generic": Site(needs_login=False, block_means_cooloff=True),
}
