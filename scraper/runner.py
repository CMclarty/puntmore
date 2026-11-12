"""The loop: keep each target's pages open, and touch the site as little as possible.

A kept-open page is refreshed by the site's own scripts, so after the first
load most targets cost NOTHING per tick — the capture just listens. A page is
reloaded only when its target asks for it (`every_min`) or it has gone quiet
for `stale_min`. Every load goes through the guard first.

Every load of an open tab is a fresh page (the old one closed), and a tab is
given one every `recycle.hours` anyway. Playwright holds every request and response
a page has seen until that page closes - a reload frees none of it - so a tab
kept open for hours filled the container's memory cap and hung the scraper
(27-28 Oct 2026).

Target kinds:
  page     one URL, one tab (e.g. Bookie Bashing's saves page)
  matches  a listing page (kept open) whose match links are opened, a few at
           a time, in their own kept-open tabs (e.g. a Paddy competition);
           `listings` adds other competitions' listing pages to the same job,
           each in its own kept-open tab, their links interleaved; only
           matches kicking off within the job's window are opened
           (schedule.within_h; kick-offs read off the listing, pp_listing),
           and with none that near the job rests (_window_links)

Targets are targets.toml on the Hub, changed only through scraper/targets.py
(an atomic rename, so a half-written file is never read). The file is checked every
TARGETS_CHECK_S: after an edit a removed or changed target's tabs close, a new
one opens on the next tick - no restart. A change to its timing, priority,
window, days, until, min_rows, trust_weight or within_h only is applied to
the open tabs in place (RESCHEDULE_FIELDS). A file that will not parse is reported and
the running targets are kept. The runner writes its state to
data/status.json every STATUS_EVERY_S, which the Hub reads.

How it behaves is data/behaviour.toml over behaviour.py's defaults, checked
on the same once-a-minute stat (its mtime, or the file appearing or going:
gone is every setting back to its default). After a load the guard takes the
new limits; each setting that moved is one line, "key: old -> new", in the
journal and in data/logs/behaviour.log; a file that will not load, or a line
that is refused, keeps the value in use and is said once per edit. A value
already drawn (a tab's next reload or recycle, the next market-tab round, a
site's back-off, the pacer's next load, the service's restart time) keeps
until its next draw.

This module wires the pieces: schedule.py says when a target is due,
sinks.py delivers its rows (drop files for the Hub), status.py writes the
heartbeat and status.json, guard.py says yes or no to every load, rest.py
keeps a job's rest (a parser that raised rest.Rest: its tabs closed, nothing
loaded until then, like a job outside its window). Every
line about one job goes to stdout and to that job's own log (joblog.py,
Runner._say); site-wide lines are stdout only. How it behaves (each site's
limits and quiet hours, the back-off, every gap, jitter and click) is
behaviour.py's, read when it is used; the guard holds its own copy of the
limits (guard.py). A job failing the same way, or running with pages open
and handing over no rows, is alerted once and once when it clears
(standing.JobAlerts, which this module feeds: _parse, _start_rest,
_watch_rows); the standing alert is the job's "alert" in status.json.
"""
import collections
import dataclasses
import json
import random
import re
import time
from datetime import datetime
from urllib.parse import urlparse

from . import behaviour
from . import browser
from . import config
from . import guard as guard_mod
from . import joblog
from . import login
from . import rest
from . import schedule
from . import standing
from .capture import Capture, DiskBudget
from .config import host_allowed
from .parsers import PARSERS, pp_listing
from .sinks import Sinks, _safe, deliver_to_hub, emit_event  # noqa: F401  (re-exported)
from .sites import SITES
from .status import (HEALTH_PING_S, STATUS_EVERY_S, Reporter, market_tabs_said,  # noqa: F401
                     site_status, target_entry)

PARSE_EVERY_S = 60
# A change to only these fields is applied to the open tabs in place; a change
# to any other (url, site, mode, parser, post, wait_for, matches) closes the
# target's tabs and reopens them.
RESCHEDULE_FIELDS = ("every_min", "stale_min", "priority", "window", "min_rows", "trust_weight",
                     "near_min", "near_hours", "days", "until", "within_h")
TARGETS_CHECK_S = 60             # a stat of targets.toml; read only when it changed
# A matches job with a window whose listings show match links but no kick-off
# it can read (Paddy's layout moved?): said once in its log, its status detail.
UNREAD_KICKOFFS = "kick-off times could not be read from the listing - nothing opened"
BEHAVIOUR_LOG = "behaviour"      # data/logs/behaviour.log (joblog's file for that name)
# A job that delivers every parse logs the same delivery line again only
# after this long (a change in what was handed or left out is logged at once).
DELIVERY_SAID_S = 600


class Tab:
    def __init__(self, target, url, page, capture):
        self.target, self.url, self.page, self.capture = target, url, page, capture
        self.opened = time.time()
        self.last_load = self.opened
        self.fresh_page(page, self.opened)
        self.reloaded_stale = False        # reloaded once for going quiet
        self.due_at: float | None = None   # next timed reload, drawn once per load
        self.next_parse = 0.0
        self.market_tabs: dict[str, str] = {}   # market tab name -> what the click did
        self.next_clicks: float | None = None   # next market-tab round; None = never clicked
        self.tabs_said = ""
        self.kickoff: float | None = None       # a match tab's kick-off, as its listing last gave it

    def fresh_page(self, page, now):
        self.page = page
        self.crashed = False
        self.recycle_at = schedule.recycle_at(now)
        page.on("crash", lambda _p: setattr(self, "crashed", True))

    def worn(self, now) -> bool:
        """Due a fresh page: its time is up, or its renderer died (a crashed
        page fails every parse until it is replaced)."""
        return self.crashed or now >= self.recycle_at


def _quiet_applies(site) -> bool:
    """Whether the site keeps quiet hours (`<site>.quiet_hours`)."""
    return behaviour.get(f"{site}.quiet_hours")


class SiteState:
    def __init__(self):
        self.blocked_for = None
        self.backoff = behaviour.get("backoff.min")[0]
        self.retry_at = 0.0


class Runner:
    def __init__(self, settings, ctx, guard, alerts, run_dir):
        self.s, self.ctx, self.guard, self.alerts = settings, ctx, guard, alerts
        self.run_dir = run_dir
        self.budget = DiskBudget(settings.capture_mb, settings.capture_tab_mb)
        self.tabs: dict[str, Tab] = {}             # key -> Tab
        self.sites = {name: SiteState() for name in SITES}
        self.paused_said: dict[str, str] = {}
        self.served_said: dict[str, float] = {}       # site -> cool-off `since` already alerted
        self.next_open: dict[str, float] = {}         # target -> earliest next match-tab open
        self.pacer = schedule.Pacer()                 # spacing between page loads
        self.retired: dict[str, collections.deque] = {}
        self.parser_said: dict[str, str] = {}
        self.failure_said: dict[str, str] = {}        # job -> first line of its last parser failure
        # job -> ((handed, left out), when) of its last delivery line in its log
        self.delivery_said: dict[str, tuple[tuple[int, int], float]] = {}
        self.targets = list(settings.targets)
        self.targets_file = settings.targets_file
        self.targets_mtime = self._mtime()            # load() has just read it
        self.next_targets_check = 0.0
        # data/behaviour.toml (behaviour.py): checked like targets.toml. __main__
        # loaded it at start; this load takes up an edit made since, quietly
        # unless it moved a value, and keeps the problems for status.json.
        self.behaviour_seen = self._behaviour_sig()
        self.next_behaviour_check = 0.0
        self.behaviour_problems: list[str] = []
        self._apply_behaviour(say_problems=False)
        self.reporter = Reporter(settings)
        self.last_load_at: dict[str, float] = {}       # target name -> last good load
        self.last_parse_at: dict[str, float] = {}      # ... last parse that returned rows
        self.rows_posted: dict[str, int] = {}
        self.retired_targets: dict[str, config.Target] = {}   # removed by a config change
        self.snapshots: set = set()                    # (target, why) saved this run
        self.betslip_seen: dict[str, float] = {}       # tab key -> newest payload looked at (_watch_betslip)
        self.betslip_said: set = set()                 # sites whose non-empty betslip was alerted this run
        (settings.data_dir / "latest").mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "requests").mkdir(parents=True, exist_ok=True)
        # Fetch targets (Finder's sources): next run per target, and the health
        # state the Hub is told about (min_rows breaches, blocks) so it only
        # wakes HubAI on a change.
        self.next_fetch: dict[str, float] = {}
        self.health_said: dict[str, str] = {}
        self.sinks = Sinks(settings)
        self.outside: dict[str, str] = {}              # inactive targets -> why (schedule.inactive)
        self.wanting_since: dict[str, float] = {}      # target -> when it began wanting a load
        # The kick-off sweep (schedule.py): the nearest kick-off ahead that a
        # target's own rows reported, and which targets are inside their near
        # window now (so a flip is acted on once, not every tick).
        self.next_kickoff: dict[str, float] = {}
        self.near: set[str] = set()
        self.login_pending: dict[str, float] = {}      # site -> when a sign-in was sent (login.py)
        # site -> {"why", "written"}: a failed sign-in, kept here too so a note
        # that could not be written still stops the next attempt
        self.login_failed: dict[str, dict] = {}
        # A job's rest (rest.py): name -> {"until", "why"}, kept in
        # data/resting.json; only the current targets' rests are kept, each
        # no further off than MAX_REST_S (a hand-edited or skewed file must
        # not park a job for years).
        names = {t.name for t in self.targets}
        on_disk = rest.load(settings.data_dir)
        latest = time.time() + rest.MAX_REST_S
        self.resting: dict[str, dict] = {n: {**v, "until": min(v["until"], latest)}
                                         for n, v in on_disk.items() if n in names}
        if self.resting != on_disk:
            rest.save(settings.data_dir, self.resting)
        self.rest_said: set[str] = set()               # rests said (or printed) by this process
        # A matches job's listing read (_window_links): what it last said about
        # its kick-offs, shown as its status detail (UNREAD_KICKOFFS).
        self.listing_said: dict[str, str] = {}
        # Each job's failing and no-rows alerts (standing.py), kept in data/alerted.json.
        self.job_alerts = standing.JobAlerts(settings.data_dir, alerts)
        self.job_alerts.keep_only(names)

    # --- site-wide state ----------------------------------------------------
    def _paused(self, site, now) -> str | None:
        if self.guard.cooloff_active(site, now):
            return "cooling off - run `resume`"
        if _quiet_applies(site) and self.guard.in_quiet(now):
            return "quiet hours"
        if self.sites[site].retry_at > now:
            return f"backing off ({self.sites[site].blocked_for})"
        return None

    def _close_site(self, site):
        for key in [k for k, t in self.tabs.items() if t.target.site == site]:
            self._close(key)

    def _close(self, key):
        tab = self.tabs.pop(key, None)
        if tab:
            try:
                tab.page.close()
            except Exception:
                pass

    def _on_blocked(self, site, reason, now, tab=None):
        """A block or logout, from a load or seen on an open page. Paddy: every
        page closes and a cool-off starts. Bookie Bashing: one alert, then a
        back-off, with the tab left open to sort over VNC. The alert carries a
        screenshot of the page, also kept in data/blocks/. Each of the site's
        jobs gets a line in its own log (no url): a cool-off's every time, a
        back-off's once per block, as its alert."""
        st = self.sites[site]
        image = self._block_shot(site, tab) if tab else None
        where = f" on {login.safe_url(tab.page.url)}" if tab else ""
        emit_event(self.s.data_dir, "blocked_source", site, f"{reason}{where}", now)
        if SITES[site].block_means_cooloff:
            self.guard.start_cooloff(site, reason, now)
            self._close_site(site)
            self.alerts.send(f"{site}: {reason}{where} - every page closed, cooling off "
                             f"{self.guard.cooloff_hours:g} h. Needs `resume` after that.", image)
            self._say_site(site, f"blocked: {reason} - every page closed, cooling off "
                                 f"{self.guard.cooloff_hours:g} h, then needs resume")
            return
        if reason != st.blocked_for:
            where_to = "over VNC" if browser.in_container() else "on the Hub's screen"
            note = None                         # a note left after the login was blanked: today's text
            if reason == "logged out" and login.credentials(site):
                note = login.failed(self.s.data_dir, site) or self.login_failed.get(site)
            if note:
                self.alerts.send(f"{site}: {reason}{where} - signing in failed ({note.get('why')}). "
                                 f"Not trying again until you log in {where_to} or run "
                                 f"`resume {site}`. Backing off.", image)
            else:
                self.alerts.send(f"{site}: {reason}{where} - needs you {where_to}. Backing off.", image)
            self._say_site(site, f"blocked: {reason} - backing off")
        st.blocked_for = reason
        st.retry_at = now + st.backoff * 60 * random.uniform(*behaviour.get("backoff.jitter_x"))
        st.backoff = min(st.backoff * 2, behaviour.get("backoff.min")[1])

    def _block_shot(self, site, tab):
        out = self.s.data_dir / "blocks"
        try:
            out.mkdir(parents=True, exist_ok=True)
            for old in sorted(out.glob("*.png"))[:-49]:
                old.unlink(missing_ok=True)
            path = out / f"{datetime.now():%Y%m%d-%H%M%S}-{site}.png"
            browser.screenshot(tab.page, path)
            return path
        except Exception as exc:
            print(f"{site}: block screenshot failed: {exc}", flush=True)
            return None

    def _check_open(self, now):
        """A kept-open page makes no loads, so a block shows up on the page
        itself: its own requests refused (3+ in 10 min), a Cloudflare page, or
        a logout. Looks only at what is already in the browser - no request."""
        for site, rules in SITES.items():
            st = self.sites[site]
            tabs = [tab for tab in self.tabs.values() if tab.target.site == site]
            if not tabs:                        # closed or removed: no reload is coming
                self.login_pending.pop(site, None)
            bad = False
            for tab in tabs:
                reason = None
                if tab.capture.refused_within(600) >= 3:
                    reason = "repeated 403/429 on an open page"
                else:
                    reason = browser.blocked(tab.page)
                if reason is None and rules.needs_login and browser.logged_out(tab.page, tab.url):
                    reason = "logged out"
                if reason is None:
                    continue
                bad = True
                # Signing in: only the tool url's reload says if it took. The
                # page the form was sent from still reads as logged out (Bookie
                # Bashing's form lands back on its /how-it-works page).
                # Never during a back-off for something else (403/429, a check):
                # nothing at all this tick - no sign-in, no alert, the back-off
                # keeps its reason; the reload when it ends deals with the logout.
                if reason == "logged out" and (
                        site in self.login_pending
                        or st.blocked_for not in (None, "logged out")
                        or self._try_login(site, tab, now)):
                    break
                if not rules.block_means_cooloff and st.blocked_for == reason:
                    break                       # already said; backing off
                self._on_blocked(site, reason, now, tab)
                break
            # Sorted over VNC: the open page is fine again, so say so now
            # rather than at the next reload.
            if (tabs and not bad and not rules.block_means_cooloff
                    and st.blocked_for in ("logged out", "Cloudflare check")):
                self._on_clear(site)

    def _cooloff_alerts(self, now, site, was, why):
        """Once when a cool-off has served its hours and waits on `resume`,
        once when the site is running again."""
        if was and was.startswith("cooling off") and not (why or "").startswith("cooling off"):
            self.alerts.send(f"{site}: cool-off over - running again.")
        c = self.guard.cooloff(site)
        if (c and not c["resumed"] and now - c["since"] >= self.guard.cooloff_hours * 3600
                and self.served_said.get(site) != c["since"]):
            self.served_said[site] = c["since"]
            self.alerts.send(f"{site}: cool-off ({c['reason']}) has served its "
                             f"{self.guard.cooloff_hours:g} h - waiting on `resume`.")

    def _on_clear(self, site):
        st = self.sites[site]
        was = st.blocked_for
        if was:
            self.alerts.send(f"{site}: clear again ({was} sorted).")
        st.blocked_for, st.backoff, st.retry_at = None, behaviour.get("backoff.min")[0], 0.0
        told = set()
        if SITES[site].needs_login:             # logged in: a failed sign-in is forgotten
            pending = self.login_pending.pop(site, None)
            self.login_failed.pop(site, None)
            login.clear(self.s.data_dir, site)
            if pending is not None:
                told = {t.target.name for t in self.tabs.values() if t.target.site == site}
                for name in sorted(told):
                    self._say(name, "signed in")
        if was:                                 # "signed in" already says it for those
            self._say_site(site, "no longer blocked", skip=told)

    def _try_login(self, site, tab, now) -> bool:
        """A needs_login page found logged out: sign in on it (login.py), once
        per logout. True when the form was sent: the logout is not reported and
        the tab's own url is due again at once, through the guard and the
        spacing; that reload is the read that says whether it took. False when
        there is no login in .env, when the last attempt failed (until the site
        is seen logged in or `resume`), or when this attempt failed - and then
        the logout is reported as today, its alert saying the sign-in failed.
        Failing too: login.max_signins already sent in the window, or the
        sign-in's time could not be written down first."""
        creds = login.credentials(site)
        if creds is None:
            return False
        mark = self.login_failed.get(site)
        if mark:
            # Forgotten only once its note reached disk and is gone (`resume`,
            # another process); one that never reached disk stays until the
            # site is seen logged in or a restart.
            if not mark["written"] or login.failed(self.s.data_dir, site):
                return False
            del self.login_failed[site]
        elif login.failed(self.s.data_dir, site):
            return False
        if site in self.login_pending:          # sent last time, and still logged out
            del self.login_pending[site]
            why = "still logged out after signing in"
        elif len(login.sent(self.s.data_dir, site, now)) >= behaviour.get("login.max_signins"):
            why = (f"signed out again after {behaviour.get('login.max_signins')} sign-ins in "
                   f"{behaviour.get('login.window_h')} h")
        elif not login.record_sent(self.s.data_dir, site, now):
            why = "cannot write login.json"     # fail closed: no sign-in it cannot count
        else:
            why = login.attempt(tab.page, site, creds)
            if why is None:
                self.login_pending[site] = now
                tab.due_at = now
                # the site's own navigation after the form is a page load too
                self.guard.record_load(site, login.safe_url(tab.page.url), now)
                self._say(tab.target.name, "logged out - signing in")
                return True
        written = login.mark_failed(self.s.data_dir, site, why, now)
        self.login_failed[site] = {"why": why, "written": written}
        self._say(tab.target.name, f"signing in failed ({why})")
        # Alerted even when the site was already backing off for this logout
        # (a `resume` mid back-off gives it one more try).
        self.sites[site].blocked_for = None
        return False

    # --- loading ------------------------------------------------------------
    def _may_load(self, site, now) -> bool:
        return self.pacer.may_load(site, now)

    def _load(self, target, key, url, now) -> Tab | None:
        """Open (or reload) one tab, through the guard and the spacing. None
        if not done."""
        site = target.site
        if not self._may_load(site, now):
            return None
        ok, _why = self.guard.can_load(site, now, quiet_applies=_quiet_applies(site))
        if not ok or not host_allowed(url, site):
            return None
        self.pacer.spaced(site, now)
        tab = self.tabs.get(key)
        try:
            if tab is None:
                page = self.ctx.new_page()
                cap = Capture(page, self.run_dir / _safe(key), self.budget)
                tab = Tab(target, url, page, cap)
                self.tabs[key] = tab
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            else:
                old = tab.page
                page = self.ctx.new_page()
                tab.capture.attach(page)
                tab.fresh_page(page, now)
                try:
                    old.close()
                except Exception:
                    pass
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            self.guard.record_load(site, url, now)
            tab.last_load = now
            tab.due_at = None
        except Exception as exc:
            self.guard.record_load(site, url, now)     # a failed load still went out
            self._say(target.name, (f"{key}: " if key != target.name else "")
                      + f"load failed: {exc.__class__.__name__}: {exc}")
            return None

        reason = browser.blocked(tab.page, resp.status if resp else None)
        if reason is None and SITES[site].needs_login and browser.logged_out(tab.page, url):
            if self._try_login(site, tab, now):
                return None
            reason = "logged out"
        if reason:
            self._on_blocked(site, reason, now, tab)
            return None
        self._on_clear(site)
        self.last_load_at[target.name] = now
        browser.dismiss_consent(tab.page)
        browser.settle(tab.page)
        return tab

    # --- the tick -----------------------------------------------------------
    def tick(self, now=None):
        now = now or time.time()
        self._reload_behaviour(now)
        self._reload_targets(now)
        for site in SITES:
            why = self._paused(site, now)
            was = self.paused_said.get(site)
            if why != was:
                print(f"{site}: {'paused - ' + why if why else 'running'}", flush=True)
            self._cooloff_alerts(now, site, was, why)
            self.paused_said[site] = why
            # Cool-off and quiet hours mean NO traffic, so the pages close. A
            # back-off leaves them open: a logged-out tab is where you log in.
            if why and not why.startswith("backing off"):
                self._close_site(site)

        self._check_open(now)
        live = []
        for t in self.targets:
            if self._paused(t.site, now):
                continue
            why = schedule.inactive(t, now)
            if why:
                self._outside_window(t, why)
                continue
            if t.name in self.outside:
                was = self.outside.pop(t.name)
                self._say(t.name, "window open - running" if was.startswith("outside window")
                          else "running again")
            if self._resting(t, now):
                continue
            live.append(t)
        self._sweep(live, now)
        for t in schedule.order(live, self._wanting(live, now), lambda site: self._left(site, now),
                                self.next_kickoff):
            if t.mode == "fetch":
                self._tick_fetch(t, now)
            elif t.matches:
                self._tick_matches(t, now)
            else:
                self._tick_page(t, t.name, t.url, now)

        for key, tab in list(self.tabs.items()):
            # A paused site's tab stays open (to log in over VNC) but is not
            # driven: parsing a login page every minute is noise, not data.
            # Nor is a tab whose site is signing in, until the reload, nor
            # one closed by this loop (its job began a rest).
            if self.tabs.get(key) is not tab:
                continue
            if not self._paused(tab.target.site, now) and tab.target.site not in self.login_pending:
                self._parse(tab, now)
        try:
            self._watch_rows(live, now)
        except Exception as exc:                # an alert's bookkeeping never ends a tick
            print(f"job alerts: {exc.__class__.__name__}: {exc}", flush=True)
        try:
            self._watch_betslip(now)
        except Exception as exc:
            print(f"betslip watch: {exc.__class__.__name__}: {exc}", flush=True)
        self._requests()
        self._heartbeat(now)
        self._report(now)

    def _watch_betslip(self, now):
        """A site's betslip is never meant to hold anything (sites.py
        `betslip`): when a page of that site is seen pricing a slip with a
        selection in it, say so once per run - in the alerts and that job's
        log. The Scraper clicks only a cookie banner and a game's market tabs
        (browser.click, which checks what is under the pointer), so a
        selection means a click landed where it should not have."""
        for key, tab in list(self.tabs.items()):
            site = tab.target.site
            pattern = SITES[site].betslip
            if not pattern or site in self.betslip_said or tab.capture is None:
                continue
            last = self.betslip_seen.get(key, 0.0)
            for ts, _kind, url, text in tab.capture.since(last):
                self.betslip_seen[key] = max(self.betslip_seen.get(key, 0.0), ts + 1e-6)
                if not re.search(pattern, url or "") or re.search(SITES[site].betslip_not or r"$^", url or ""):
                    continue
                try:
                    body = json.loads(text)
                    held = len(body.get("betCombinations") or []) if isinstance(body, dict) else 0
                except (TypeError, ValueError):
                    held = 0
                if held:
                    self.betslip_said.add(site)
                    msg = ("the betslip is not empty - nobody chose what is in it; "
                           "empty it on the Scraper's screen")
                    self.alerts.send(f"{site}: {msg}")
                    joblog.write(self.s.data_dir, tab.target.name, msg)
                    break

    def _watch_rows(self, live, now):
        """The no-rows clock (standing.py) runs for a job with a `post` route
        that is running this tick (site not paused, inside its window and
        days, not resting, not finished, not signing in) with a page open that
        its parser reads: its own tab, or for a matches job a match tab (a
        listing alone does not count). A fetch job has min_rows instead."""
        running = {t.name for t in live}
        for t in self.targets:
            if (t.name in running and t.post and t.parser and t.mode != "fetch"
                    and t.name not in self.resting and t.site not in self.login_pending
                    and not self._paused(t.site, now) and self._read_tab_open(t)):
                self.job_alerts.running(t.name, now, lambda t=t: self._state(t, now)[1])
            else:
                self.job_alerts.idle(t.name)
        self.job_alerts.flush(now)

    def _read_tab_open(self, t) -> bool:
        if t.matches:
            return any(k.startswith(t.name + ":m:") for k in self.tabs)
        return t.name in self.tabs

    # --- days, until, window and priority ------------------------------------
    def _outside_window(self, t, why):
        """A target that is not active (`why`, from schedule.inactive: off its
        days, past its until, outside its window) is treated like quiet
        hours: its tabs close and nothing loads. A fetch target forgets its
        next run, so it runs when it is active again. Said once per change of
        reason. A reason new to this process that the job's log already ends
        with (the line before a restart) is printed but not logged again."""
        if self.outside.get(t.name) != why:
            self.outside[t.name] = why
            msg = f"outside window {t.window} - paused" if why == "outside window" else f"{why} - paused"
            last = joblog.read(self.s.data_dir, t.name, 1)
            if last and last[0]["msg"] == msg:
                print(f"{t.name}: {msg}", flush=True)
            else:
                self._say(t.name, msg)
        for key in [k for k, tab in self.tabs.items() if tab.target.name == t.name]:
            self._close(key)
        self.next_fetch.pop(t.name, None)
        self.wanting_since.pop(t.name, None)

    def _resting(self, t, now) -> bool:
        """A job resting (its parser raised rest.Rest) is skipped like one
        outside its window: its tabs close and nothing loads. Once `until` has
        passed the rest is forgotten and the job runs in this same tick. A
        rest this process has not said yet (one from before a restart) is
        printed, not logged again."""
        r = self.resting.get(t.name)
        if r is None:
            return False
        if now >= r["until"]:
            del self.resting[t.name]
            rest.save(self.s.data_dir, self.resting)
            self.rest_said.discard(t.name)
            self._say(t.name, "rest over - running")
            return False
        if t.name not in self.rest_said:
            self.rest_said.add(t.name)
            print(f"{t.name}: {_rest_detail(r)}", flush=True)
        for key in [k for k, tab in self.tabs.items() if tab.target.name == t.name]:
            self._close(key)
        self.next_fetch.pop(t.name, None)
        self.wanting_since.pop(t.name, None)
        return True

    def _start_rest(self, t, until, why, now):
        """A job rests until `until` (clamped to MIN_REST_S..MAX_REST_S from
        now): kept in data/resting.json, its tabs closed, said once."""
        until = min(max(until, now + rest.MIN_REST_S), now + rest.MAX_REST_S)
        self.resting[t.name] = {"until": until, "why": why}
        rest.save(self.s.data_dir, self.resting)
        for key in [k for k, x in self.tabs.items() if x.target.name == t.name]:
            self._close(key)
        self.parser_said.pop(t.name, None)
        self.listing_said.pop(t.name, None)
        self.job_alerts.reading(t.name)                # a rest clears a failing alert
        self.rest_said.add(t.name)
        self._say(t.name, _rest_detail(self.resting[t.name]))

    def _end_rest(self, name) -> bool:
        """Forget a job's rest (saved). True when it had one."""
        self.rest_said.discard(name)
        if self.resting.pop(name, None) is None:
            return False
        rest.save(self.s.data_dir, self.resting)
        return True

    def _sweep(self, targets, now):
        """Enter or leave each target's near window (schedule.near). On a
        flip the interval in force changes, so the timed reload is redrawn
        from the last load with the new one (a fetch target's next run
        likewise); a kick-off that has passed drops out of next_kickoff."""
        for t in targets:
            k = self.next_kickoff.get(t.name)
            if k is not None and k < now:
                self.next_kickoff.pop(t.name, None)
                k = None
            is_near = schedule.near(t, k, now)
            if is_near == (t.name in self.near):
                continue
            (self.near.add if is_near else self.near.discard)(t.name)
            every = schedule.effective_every(t, k, now)
            self._say(t.name, f"kick-off in {(k - now) / 60:.0f} min - every {every:g} min"
                      if is_near else "past the near window - normal interval")
            for tab in self.tabs.values():
                if tab.target.name == t.name:
                    tab.due_at = None
            last = self.last_load_at.get(t.name)
            if t.mode == "fetch" and t.name in self.next_fetch and last is not None:
                self.next_fetch[t.name] = schedule.next_fetch_at(last, every)

    def _every(self, t, now):
        """The reload interval in force for `t` now (the near window or not)."""
        return schedule.effective_every(t, self.next_kickoff.get(t.name), now)

    def _left(self, site, now) -> int:
        """Loads the site has left: this hour's, or today's if fewer."""
        h, d = self.guard.spent(site, now)
        per_hour, per_day = self.guard.limit(site)
        return max(0, min(per_hour - h, per_day - d))

    def _wanting(self, targets, now) -> dict:
        """name -> when the target began wanting a load, for schedule.order.
        Worked out only for a site that could be contended (fewer loads left
        than it has targets): elsewhere the order is file order anyway."""
        tight = {site for site in {t.site for t in targets}
                 if self._left(site, now) < sum(1 for t in targets if t.site == site)}
        for t in targets:
            if t.site not in tight or not self._wants(t, now):
                self.wanting_since.pop(t.name, None)
            else:
                self.wanting_since.setdefault(t.name, self._due_since(t, now))
        return {t.name: self.wanting_since[t.name] for t in targets if t.name in self.wanting_since}

    def _wants(self, t, now) -> bool:
        """Would this target load something if it were given the next slot?
        Asks only what is already known: no page is read."""
        if t.mode == "fetch":
            due = self.next_fetch.get(t.name)
            return due is None or now >= due
        if not t.matches:
            tab = self.tabs.get(t.name)
            return tab is None or schedule.page_due(t, tab, now, self._every(t, now))
        for key, _url in listing_keys(t):
            listing = self.tabs.get(key)
            if listing is None or schedule.listing_due(t, listing, now):
                return True
        mine = [tab for k, tab in self.tabs.items() if k.startswith(t.name + ":m:")]
        if any(schedule.stale(t, tab, now) or tab.worn(now) for tab in mine):
            return True
        # Room for another match tab: counted as a want, though the listing
        # may turn out to have no new link.
        return len(mine) < t.matches.max_tabs and now >= self.next_open.get(t.name, 0.0)

    def _due_since(self, t, now) -> float:
        """When a wanted load fell due: the drawn reload time when there is
        one, else now (the target is first seen wanting it now)."""
        if t.mode == "fetch":
            return min(self.next_fetch.get(t.name, now), now)
        keys = [k for k, _url in listing_keys(t)] if t.matches else [t.name]
        dues = [tab.due_at for tab in map(self.tabs.get, keys) if getattr(tab, "due_at", None) is not None]
        return min(min(dues), now) if dues else now

    # --- targets.toml, re-read when it changes ------------------------------------
    def _mtime(self):
        try:
            return self.targets_file.stat().st_mtime_ns if self.targets_file else None
        except OSError:
            return None

    def _reload_targets(self, now):
        """Apply an edit to targets.toml without a restart. A file that has
        gone, or will not load, keeps what is running and says so once per
        edit - a half-written save must not close every tab."""
        if self.targets_file is None or now < self.next_targets_check:
            return
        self.next_targets_check = now + TARGETS_CHECK_S
        mtime = self._mtime()
        if mtime == self.targets_mtime:
            return
        self.targets_mtime = mtime
        if mtime is None:
            print(f"config: {self.targets_file} is gone; keeping the current targets", flush=True)
            return
        try:
            targets = config.load_targets(self.targets_file)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"config: {self.targets_file} not applied ({exc.__class__.__name__}: {exc}); "
                  "keeping the current targets", flush=True)
            return
        self.apply_targets(targets)

    # --- behaviour.toml, re-read when it changes ------------------------------------
    def _behaviour_sig(self):
        """What says the file moved: its inode (a save is a rename), mtime
        and size; None when there is none."""
        try:
            st = behaviour.path(self.s.data_dir).stat()
        except OSError:
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size

    def _reload_behaviour(self, now):
        """Take up an edit to data/behaviour.toml without a restart, once a
        minute at most, and only when the file has moved."""
        if now < self.next_behaviour_check:
            return
        self.next_behaviour_check = now + TARGETS_CHECK_S
        sig = self._behaviour_sig()
        if sig == self.behaviour_seen:
            return
        self.behaviour_seen = sig
        self._apply_behaviour()

    def _apply_behaviour(self, say_problems=True):
        """behaviour.load, then the guard takes the limits in use; a value
        that moved is said, and the load's problems too (once: this runs only
        when the file has moved). Never raises."""
        before = {s.key: behaviour.get(s.key) for s in behaviour.SETTINGS}
        self.behaviour_problems = behaviour.load(self.s.data_dir)
        moved = [(s.key, before[s.key], behaviour.get(s.key)) for s in behaviour.SETTINGS
                 if behaviour.get(s.key) != before[s.key]]
        if moved:
            self.guard.use(**guard_mod.from_behaviour())
        for key, old, new in moved:
            self._say_behaviour(f"{key}: {behaviour.show(old)} -> {behaviour.show(new)}")
        if say_problems:
            for p in self.behaviour_problems:
                self._say_behaviour(p)

    def _say_behaviour(self, msg: str) -> None:
        print(f"behaviour: {msg}", flush=True)
        joblog.write(self.s.data_dir, BEHAVIOUR_LOG, msg)

    def apply_targets(self, targets):
        """Swap in a new target list. A target that is gone (removed or
        disabled), or whose page changed (any field but RESCHEDULE_FIELDS),
        has its tabs closed; new and changed targets open through the normal
        tick (and its guard) from the next tick on. A change to
        RESCHEDULE_FIELDS only is applied in place: the open tabs stay and
        are rescheduled, so it costs no load."""
        old = {t.name: t for t in self.targets}
        new = {t.name: t for t in targets}
        if old == new:
            return
        in_place = sorted(n for n in set(old) & set(new)
                          if old[n] != new[n] and reschedule_only(old[n], new[n]))
        for name in in_place:
            self._reschedule(new[name])
            # a new window: a job resting for want of a game that near looks again
            if old[name].within_h != new[name].within_h and self._end_rest(name):
                self._say(name, "within_h changed - rest ended, running")
        for name, t in old.items():
            if new.get(name) != t and name not in in_place:
                for key in [k for k, tab in self.tabs.items() if tab.target.name == name]:
                    self._close(key)
                self.retired.pop(name, None)
                self.next_open.pop(name, None)
                self.parser_said.pop(name, None)
                self.delivery_said.pop(name, None)
                self.listing_said.pop(name, None)
                self._end_rest(name)
            if name not in new:
                self.retired_targets[name] = t
        for name in new:
            self.retired_targets.pop(name, None)
        self.job_alerts.keep_only(new)
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        changed = sorted(n + (" (in place)" if n in in_place else "")
                         for n in set(old) & set(new) if old[n] != new[n])
        print("config: " + "; ".join(f"{label} {', '.join(names)}" for label, names in
                                     (("added", added), ("removed", removed), ("changed", changed))
                                     if names), flush=True)
        self.targets = list(targets)

    def _reschedule(self, t):
        """A change to RESCHEDULE_FIELDS only: the open tabs keep their pages
        and take the new definition. A timed reload is redrawn from the last
        load with the new every_min; a fetch target's next run likewise. The
        window, days, until and priority apply from the next tick."""
        for tab in self.tabs.values():
            if tab.target.name == t.name:
                if (tab.target.every_min, tab.target.near_min, tab.target.near_hours) != \
                        (t.every_min, t.near_min, t.near_hours):
                    tab.due_at = None
                tab.target = t
        self.near.discard(t.name)              # re-entered on the next tick with the new fields
        last = self.last_load_at.get(t.name)
        if t.name in self.next_fetch and last is not None:
            self.next_fetch[t.name] = schedule.next_fetch_at(last, t.every_min)

    def _tick_page(self, t, key, url, now):
        tab = self.tabs.get(key)
        if tab is None:
            self._load(t, key, url, now)
            return
        if schedule.page_due(t, tab, now, self._every(t, now)):
            self._load(t, key, url, now)

    def _tick_fetch(self, t, now):
        """A fetch target (a Finder source): load the page, parse it once, close it. Nothing
        is kept open, so each run is one load against its site's budget. The
        next run is scheduled before parsing, so a broken parser can never turn
        into a reload loop; a load that fails backs off five minutes."""
        due = self.next_fetch.get(t.name)
        if due is not None and now < due:
            return
        ok, _why = self.guard.can_load(t.site, now, quiet_applies=_quiet_applies(t.site))
        if not ok or not self._may_load(t.site, now):
            return                                  # budget or spacing: try next tick
        key = f"fetch:{t.name}"
        tab = self._load(t, key, t.url, now)
        if tab is None:
            self.next_fetch[t.name] = now + behaviour.get("fetch.retry_s")
            return
        self.next_fetch[t.name] = schedule.next_fetch_at(now, self._every(t, now))
        try:
            if t.wait_for:
                try:
                    tab.page.wait_for_selector(t.wait_for, timeout=20_000)
                except Exception:
                    self._say(t.name, f"wait_for {t.wait_for!r} not seen")
            self._parse(tab, now, force=True)
        finally:
            self._close(key)

    def _check_rows(self, t, rows, now):
        """Row-count health for fetch targets: fewer than min_rows is a suspected
        layout change. Alert, tell the Hub and note it in the job's log once per
        change, not per run."""
        if t.min_rows and len(rows) < t.min_rows:
            msg = f"only {len(rows)} row(s), expected at least {t.min_rows} - layout change?"
            if self.health_said.get(t.name) != msg:
                self.health_said[t.name] = msg
                self.alerts.send(f"{t.name}: {msg}")          # prints it; the job's log gets it here
                joblog.write(self.s.data_dir, t.name, msg)
                emit_event(self.s.data_dir, "unhealthy_source", t.name, msg, now)
            self.parser_said[t.name] = msg
        elif t.name in self.health_said:
            del self.health_said[t.name]
            msg = f"healthy again ({len(rows)} rows)"
            self.alerts.send(f"{t.name}: {msg}")
            joblog.write(self.s.data_dir, t.name, msg)

    def _deliver(self, t, rows, now) -> int:
        """Hand the rows to the sink and return how many it took (0: none). A
        delivery that went is noted in the job's log (the sink prints its own
        line): what was handed and what the sink left out, logged when that differs from the job's last delivery line
        or DELIVERY_SAID_S after it, so a job delivering every parse does not
        fill its log with the same line."""
        handed = self.sinks.deliver(t, rows, now)
        if not handed:
            return 0
        counts = (handed, len(rows) - handed)
        last = self.delivery_said.get(t.name)
        if last is None or last[0] != counts or now - last[1] >= DELIVERY_SAID_S:
            self.delivery_said[t.name] = (counts, now)
            joblog.write(self.s.data_dir, t.name, f"{handed} row(s) handed to the Hub ({t.post})"
                         + (f", {counts[1]} left out" if counts[1] else ""))
        return handed

    def _tick_matches(self, t, now):
        """Every listing (url, then `listings`) has its own kept-open tab,
        loaded when missing and relisted on its own clock; one load per tick
        at most. Match links come from every listing, interleaved, so each
        competition gets a tab before any gets two; max_tabs is the job's.

        With a window (schedule.within_h, not 0) the listings' kick-offs are
        read off their pages (pp_listing, no request) and a match is opened
        only when its kick-off is known and inside the window; an open match
        tab whose game has kicked off or left the window is retired. Links
        but no kick-off read: said once (UNREAD_KICKOFFS), nothing opened.
        Every listing open and no link inside the window: the job rests
        (_window_links). A window of 0 reads no kick-off at all."""
        m = t.matches
        listings = listing_keys(t)
        for list_key, list_url in listings:
            listing = self.tabs.get(list_key)
            if listing is None or schedule.listing_due(t, listing, now):
                self._load(t, list_key, list_url, now)
                return

        within_h = schedule.within_h(t)
        seen = self._listed(listings, now) if within_h else None

        retired = self.retired.setdefault(t.name, collections.deque(maxlen=500))
        mine = {k: tab for k, tab in self.tabs.items() if k.startswith(t.name + ":m:")}
        for key, tab in mine.items():
            f = seen.get(tab.url) if seen else None
            if f is not None and f.kickoff is not None:
                tab.kickoff = f.kickoff
            quiet = tab.capture.quiet_for(now) >= t.stale_min * 60
            gone = bool(within_h) and tab.kickoff is not None and not _inside(tab.kickoff, now, within_h)
            if gone or now - tab.opened >= m.tab_max_hours * 3600 or (quiet and tab.reloaded_stale
                                                                      and now - tab.last_load >= t.stale_min * 60):
                self._say(t.name, f"{key}: retired")
                retired.append(tab.url)
                self._close(key)
            elif quiet and now - tab.last_load >= t.stale_min * 60:
                if self._load(t, key, tab.url, now):
                    tab.reloaded_stale = True
            elif tab.worn(now):
                if self._load(t, key, tab.url, now):
                    self._open_market_tabs(t, tab, now)
            elif tab.next_clicks is not None and now >= tab.next_clicks:
                self._open_market_tabs(t, tab, now)

        links = None
        if seen is not None:
            links = self._window_links(t, listings, seen, within_h, now)
            if links is None:                   # resting now, or no kick-off read
                return
        open_urls = {tab.url for k, tab in self.tabs.items() if k.startswith(t.name + ":m:")}
        if len(open_urls) >= m.max_tabs:
            return
        if now < self.next_open.get(t.name, 0.0) or not self._may_load(t.site, now):
            return
        if links is None:
            links = interleave([match_links(self.tabs[k].page, m.link_re, u)
                                for k, u in listings if k in self.tabs])
        for url in links:
            if url in open_urls or url in retired:
                continue
            self.next_open[t.name] = schedule.next_open_at(now, m.open_gap_min)
            tab = self._load(t, f"{t.name}:m:{url}", url, now)
            if tab:
                if seen is not None and url in seen:
                    tab.kickoff = seen[url].kickoff
                self._open_market_tabs(t, tab, now)
            break

    def _listed(self, listings, now) -> dict:
        """The fixtures the job's open listings show, url -> pp_listing.Fixture
        (the first listing to show a url wins). Reads the DOM: no request."""
        seen = {}
        for key, _url in listings:
            if key in self.tabs:
                for u, f in pp_listing.fixtures(self.tabs[key].page, now).items():
                    seen.setdefault(u, f)
        return seen

    def _window_links(self, t, listings, seen, within_h, now) -> list[str] | None:
        """The match links (every listing's, interleaved) kicking off inside
        the window, in that order. None when there is nothing to open: no
        link at all (a listing not drawn yet looks the same as an empty one,
        so no rest: it is relisted as before), links whose kick-off none could
        be read (said once, UNREAD_KICKOFFS, the job not rested: a layout
        change must show), or none inside the window - then the job rests
        until within_h hours before the next game the listings show, or
        games.look_again_s, whichever is sooner."""
        links = interleave([match_links(self.tabs[k].page, t.matches.link_re, u)
                            for k, u in listings if k in self.tabs])
        if not links:
            return None
        known = {u: seen[u] for u in links if u in seen and seen[u].kickoff is not None}
        if not known:
            if self.listing_said.get(t.name) != UNREAD_KICKOFFS:
                self._say(t.name, UNREAD_KICKOFFS)
            self.listing_said[t.name] = UNREAD_KICKOFFS
            return None
        self.listing_said.pop(t.name, None)
        inside = [u for u in links if u in known and _inside(known[u].kickoff, now, within_h)]
        if inside:
            return inside
        look_again = now + behaviour.get("games.look_again_s")
        later = [f for f in known.values() if f.kickoff > now + within_h * 3600]
        if not later:
            self._start_rest(t, look_again, f"no games within {within_h:g} hours on the listing", now)
        else:
            nxt = min(later, key=lambda f: f.kickoff)
            self._start_rest(t, min(nxt.kickoff - within_h * 3600, look_again),
                             f"no games within {within_h:g} hours - next is {nxt.name}, "
                             f"{rest.when(nxt.kickoff)}", now)
        return None

    def _open_market_tabs(self, t, tab, now):
        """Click the in-page market tabs, so the page fetches the markets we
        want - on arrival, then about every `click.every_s`, since the page keeps
        refreshing only the tab on screen. Each click goes through the
        mouse, then waits for the page's own network to bring the tab's
        markets in, then reads down them. What happened to each is kept on
        the tab for the status report: loaded | clicked, nothing new |
        not offered | not clicked (budget) | click failed. A click that
        navigates counts as a load."""
        feed = re.compile(SITES[t.site].market_list or r"$^")
        page, cap = tab.page, tab.capture
        browser.dismiss_consent(page)       # a banner drawn after the load's own try
        for name in t.matches.market_tabs:
            if not self.guard.can_load(t.site, now)[0]:
                tab.market_tabs[name] = "not clicked (budget)"
                continue
            found = browser.find_tab(page, name)
            if not found:
                # Paddy's event page only grows a Corners / Cards & Fouls tab
                # as kickoff nears; days out it has Popular, Bet Builder,
                # Goals, Other Markets. So this is the market not being up
                # yet, not a locator miss - the page is kept once per run
                # so a real miss can still be checked.
                if tab.market_tabs.get(name) != "not offered":    # once, not every round
                    self._say(t.name, f"{tab.url}: no {name!r} tab; tabs drawn: "
                                      f"{', '.join(browser.visible_tabs(page)) or 'none found'}")
                tab.market_tabs[name] = "not offered"
                self._snapshot_once(t, tab, "market-tab-not-found")
                continue
            loc, label = found
            before_url, before_docs = page.url, cap.documents
            clicked_at = time.time()
            cap.mark(f"tab:{label}")
            try:
                browser.click(page, loc)
            except Exception as exc:
                tab.market_tabs[name] = "click failed"
                self._say(t.name, f"{tab.url}: market tab {label!r}: click failed: {exc}")
                continue
            loaded = browser.wait_for_feed(page, cap, clicked_at, feed)
            if page.url != before_url or cap.documents != before_docs:
                self.guard.record_load(t.site, login.safe_url(page.url), now)
            tab.market_tabs[name] = "loaded" if loaded else "clicked, nothing new"
            try:
                browser.read_down(page)
            except Exception:
                pass
        tab.next_clicks = schedule.next_clicks_at(now)
        said = ", ".join(f"{n} {o}" for n, o in tab.market_tabs.items())
        if said and said != tab.tabs_said:
            self._say(t.name, f"{tab.url}: market tabs: {said}")
            tab.tabs_said = said

    def _snapshot_once(self, t, tab, why):
        """Save the page as it is drawn (HTML + screenshot, no request) once
        per target per run, so a locator that missed can be fixed from it."""
        if (t.name, why) in self.snapshots:
            return
        self.snapshots.add((t.name, why))
        out = self.run_dir / _safe(f"{t.name}-{why}")
        try:
            out.mkdir(parents=True, exist_ok=True)
            (out / "page.url").write_text(login.safe_url(tab.page.url))
            (out / "page.html").write_text(tab.page.content(), encoding="utf-8")
            browser.screenshot(tab.page, out / "page.png")
        except Exception as exc:
            self._say(t.name, f"snapshot failed: {exc}")

    # --- output -------------------------------------------------------------
    def _parse(self, tab, now, force=False):
        t = tab.target
        if not t.parser or (not force and now < tab.next_parse):
            return
        tab.next_parse = now + PARSE_EVERY_S
        fn = PARSERS.get(t.parser)
        try:
            if fn is None:
                raise NotImplementedError(f"no parser called {t.parser!r}")
            rows = fn(tab.page, tab.capture)
        except NotImplementedError as exc:
            if t.name not in self.parser_said:
                self._say(t.name, f"{exc} - capturing only")
                self.parser_said[t.name] = f"parser not written ({t.parser}) - capturing only"
            return
        except rest.Rest as exc:
            # Nothing for this job to do until exc.until: its page closes and
            # the tick skips it until then (_resting).
            self._start_rest(t, exc.until, exc.why, now)
            return
        except Exception as exc:
            # Shown as the target's detail in status.json until a call succeeds -
            # a driver that cannot bring its page up says so there, not only here.
            # Written to the job's log only when its first line (whole, not
            # the status's cut) is new for the job: a parser failing the same
            # way every minute says it once.
            first = (str(exc).splitlines() or [""])[0]
            said = f"parser failed: {first[:120]}"
            if self.parser_said.get(t.name) != said or self.failure_said.get(t.name) != first:
                self._say(t.name, f"parser failed: {exc}")
            self.parser_said[t.name] = said
            self.failure_said[t.name] = first
            self.job_alerts.failed(t.name, first, now)
            return
        self.parser_said.pop(t.name, None)
        self.failure_said.pop(t.name, None)
        self.job_alerts.reading(t.name)
        k = schedule.next_kickoff(rows, now)
        if k is not None:
            self.next_kickoff[t.name] = k
        self._check_rows(t, rows, now)
        out = self.s.data_dir / "latest" / f"{_safe(t.name)}.json"
        out.write_text(json.dumps({"at": now, "url": tab.url, "rows": rows}, indent=1))
        if rows:
            self.last_parse_at[t.name] = now
        if t.post and rows:
            handed = self._deliver(t, rows, now)
            if handed:
                self.rows_posted[t.name] = self.rows_posted.get(t.name, 0) + handed
                self.job_alerts.delivered(t.name, now)

    # --- status, for the Hub and `python -m scraper status` ---------------------------
    def status(self, now) -> dict:
        """Per target: state, detail, last load/parse, rows posted. Per site:
        budget spent and cool-off. What `python -m scraper status` prints.
        A job past its `until` is "finished" whatever its site is doing; off
        its days or outside its window it is "paused" with the reason."""
        targets = []
        for t in [*self.targets, *self.retired_targets.values()]:
            retired = t.name in self.retired_targets
            state, detail = self._state(t, now)
            targets.append(target_entry(t, state, detail, self.last_load_at.get(t.name),
                                        self.last_parse_at.get(t.name), self.rows_posted.get(t.name, 0),
                                        self.next_kickoff.get(t.name), t.name in self.near,
                                        None if retired else self.job_alerts.standing(t.name)))
        return {"at": now, "targets_file": str(self.targets_file) if self.targets_file else None,
                "targets": targets, "sites": site_status(self.guard, now),
                "behaviour": {"changed": len(behaviour.changed()),
                              "problems": list(self.behaviour_problems)}}

    def _state(self, t, now) -> tuple[str, str]:
        """A target's (state, detail), as status.json gives them."""
        why = self._paused(t.site, now)
        off = schedule.inactive(t, now)
        if t.name in self.retired_targets:
            return "retired", "removed from targets.toml"
        if off == "finished":
            return "finished", f"ended {t.until}"
        if why and (why.startswith("cooling off") or why.startswith("backing off")):
            return "blocked", why
        if why:
            return "paused", why
        if off:
            return "paused", off
        if t.name in self.resting and now < self.resting[t.name]["until"]:
            return "paused", _rest_detail(self.resting[t.name])
        mine = [tab for tab in self.tabs.values() if tab.target.name == t.name]
        n = len(mine)
        return "running", "; ".join(x for x in (f"{n} tab{'s' if n != 1 else ''} open",
                                                market_tabs_said(mine),
                                                self.listing_said.get(t.name),
                                                self.parser_said.get(t.name)) if x)

    def _report(self, now):
        self.reporter.report(now, self.status)

    def _requests(self):
        """`python -m scraper dump <target>` drops a file; the running loop
        answers it from the page it already has open - no extra load.
        `wake <target>` likewise ends that job's rest (rest.py).
        `selfcheck` likewise, as the running browser holds the profile."""
        req = self.s.data_dir / "requests" / "selfcheck"
        if req.exists():
            req.unlink(missing_ok=True)
            try:
                seen = browser.selfcheck(self.ctx)
                (self.s.data_dir / "selfcheck.json").write_text(json.dumps(seen, indent=1))
            except Exception as exc:
                print(f"selfcheck: {exc}", flush=True)
        for req in (self.s.data_dir / "requests").glob("dump-*"):
            name = req.name[len("dump-"):]
            req.unlink(missing_ok=True)
            tabs = [(k, tab) for k, tab in self.tabs.items() if tab.target.name == name]
            if not tabs:
                if any(t.name == name for t in self.targets):
                    self._say(name, "dump: no open page for that target")
                else:                           # not a job: no log to write to
                    print(f"dump {name}: no open page for that target", flush=True)
                continue
            out = self.s.data_dir / "dumps" / f"{_safe(name)}-{datetime.now():%Y%m%d-%H%M%S}"
            out.mkdir(parents=True, exist_ok=True)
            for i, (key, tab) in enumerate(tabs):
                try:
                    (out / f"{i}.url").write_text(login.safe_url(tab.page.url))
                    (out / f"{i}.html").write_text(tab.page.content(), encoding="utf-8")
                    browser.screenshot(tab.page, out / f"{i}.png", full_page=True)
                except Exception as exc:
                    self._say(name, f"dump: {key + ': ' if key != name else ''}{exc}")
            self._say(name, f"dump: saved {out}")
        for req in (self.s.data_dir / "requests").glob("wake-*"):
            name = req.name[len("wake-"):]
            req.unlink(missing_ok=True)
            if self._end_rest(name):
                self._say(name, "woken by hand - running")
            elif any(t.name == name for t in self.targets):
                self._say(name, "wake: not resting")
            else:                               # not a job: no log to write to
                print(f"wake {name}: no such target", flush=True)

    def _say(self, name: str, msg: str) -> None:
        """A line about one job: printed as "<name>: <msg>" for the journal, and
        written to the job's own log (joblog; a write that fails is dropped)."""
        print(f"{name}: {msg}", flush=True)
        joblog.write(self.s.data_dir, name, msg)

    def _say_site(self, site, msg: str, skip=()) -> None:
        """A line in the log of every job of `site` the runner holds."""
        for name in sorted({t.name for t in self.targets if t.site == site} - set(skip)):
            self._say(name, msg)

    def _heartbeat(self, now):
        self.reporter.heartbeat(now)

    def close_all(self):
        for key in list(self.tabs):
            self._close(key)


def _inside(kickoff: float, now: float, within_h: float) -> bool:
    """A kick-off inside the window: after now, at most within_h hours ahead."""
    return now < kickoff <= now + within_h * 3600


def _rest_detail(r: dict) -> str:
    """How a rest is said, in the job's log and as its status detail."""
    return f"{r['why']} - resting until {rest.when(r['until'])}"


def reschedule_only(old, new) -> bool:
    """True when the two definitions differ only in RESCHEDULE_FIELDS."""
    return dataclasses.replace(old, **{f: getattr(new, f) for f in RESCHEDULE_FIELDS}) == new


def listing_keys(t) -> list[tuple[str, str]]:
    """A matches job's listing tabs, (key, url): its url first ("<name>:list"),
    then each of `listings` ("<name>:list:1", ...), in order."""
    return [(f"{t.name}:list", t.url),
            *((f"{t.name}:list:{i}", u) for i, u in enumerate(t.matches.listings, 1))]


def interleave(lists) -> list[str]:
    """The first of each list, then the second of each, ...; a value already
    taken is skipped."""
    seen, out = set(), []
    for i in range(max(map(len, lists), default=0)):
        for xs in lists:
            if i < len(xs) and xs[i] not in seen:
                seen.add(xs[i])
                out.append(xs[i])
    return out


def match_links(page, link_re, base_url) -> list[str]:
    """Match links on a listing page, in page order, same host only. Reads the
    DOM of the already-open page: costs no request."""
    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    except Exception:
        return []
    host = urlparse(base_url).hostname
    pat = re.compile(link_re)
    seen, out = set(), []
    for h in hrefs:
        h = h.split("#")[0]
        if h in seen or urlparse(h).hostname != host or not pat.search(h):
            continue
        seen.add(h)
        out.append(h)
    return out
