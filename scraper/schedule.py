"""When each target is due: reload intervals, staleness, jitter, load spacing.

Nothing here loads a page or touches the guard's budget: the runner asks
these questions and the guard still says yes or no to every load.

Jitter is drawn ONCE per load and kept on the tab (`due_at`), never afresh
each tick: drawing it every 15 s tick made a reload fire at the low end
nearly every time.
"""
import random

# Jitter on timed reloads, drawn once per load.
RELOAD_JITTER = (0.75, 1.35)     # every_min (and a fetch target's next run)
RELIST_JITTER = (0.9, 1.3)       # a listing's relist_min
ANY_LOAD_GAP_S = (20, 45)        # between any two page loads, whatever the site
SITE_GAP_JITTER = (1.0, 1.8)     # x the site's load_gap_s between two of its loads
RECYCLE_H = 0.5                  # a tab's page is swapped for a fresh one this often
RECYCLE_JITTER = (0.85, 1.2)
# A match page polls only the prices of the market tab on screen, so its
# market tabs are clicked again this often (in-page clicks, not loads).
CLICK_EVERY_S = 10 * 60
CLICK_JITTER = (0.8, 1.4)
OPEN_GAP_JITTER = (1.0, 1.6)     # x a matches target's open_gap_min
FETCH_DEFAULT_MIN = 60           # a fetch target with no every_min runs hourly
FETCH_RETRY_S = 300              # a fetch whose load failed tries again after this


class Pacer:
    """Spacing between page loads: one person opens one page, looks at it, and
    only then opens the next. Nothing is opened in a burst - not at startup,
    not when quiet hours end, not when several tabs go stale together."""

    def __init__(self, sites):
        self.sites = sites
        self.next_load_at: dict[str, float] = {}      # site -> earliest next page load
        self.next_any_load = 0.0                      # ... of any site

    def may_load(self, site, now) -> bool:
        return now >= self.next_load_at.get(site, 0.0) and now >= self.next_any_load

    def spaced(self, site, now):
        self.next_load_at[site] = now + self.sites[site].load_gap_s * random.uniform(*SITE_GAP_JITTER)
        self.next_any_load = now + random.uniform(*ANY_LOAD_GAP_S)


def recycle_at(now) -> float:
    return now + RECYCLE_H * 3600 * random.uniform(*RECYCLE_JITTER)


def reload_at(last_load, every_min) -> float:
    return last_load + every_min * 60 * random.uniform(*RELOAD_JITTER)


def relist_at(last_load, relist_min) -> float:
    return last_load + relist_min * 60 * random.uniform(*RELIST_JITTER)


def next_clicks_at(now) -> float:
    return now + CLICK_EVERY_S * random.uniform(*CLICK_JITTER)


def next_open_at(now, open_gap_min) -> float:
    return now + open_gap_min * 60 * random.uniform(*OPEN_GAP_JITTER)


def next_fetch_at(now, every_min) -> float:
    return now + (every_min or FETCH_DEFAULT_MIN) * 60 * random.uniform(*RELOAD_JITTER)


def page_due(t, tab, now) -> bool:
    """A kept-open page wants a load: its timed reload is due, it has gone
    quiet for stale_min, or its page is worn (recycle time, or crashed). The
    timed reload's jitter is drawn once per load and kept on the tab."""
    if t.every_min and tab.due_at is None:
        tab.due_at = reload_at(tab.last_load, t.every_min)
    due = t.every_min and now >= tab.due_at
    return bool(due or stale(t, tab, now) or tab.worn(now))


def stale(t, tab, now) -> bool:
    return (tab.capture.quiet_for(now) >= t.stale_min * 60
            and now - tab.last_load >= t.stale_min * 60)


def listing_due(t, listing, now) -> bool:
    if listing.due_at is None:
        listing.due_at = relist_at(listing.last_load, t.matches.relist_min)
    return now >= listing.due_at or listing.worn(now)
