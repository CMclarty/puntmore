"""The loop: keep each target's pages open, and touch the site as little as possible.

A kept-open page is refreshed by the site's own scripts, so after the first
load most targets cost NOTHING per tick — the capture just listens. A page is
reloaded only when its target asks for it (`every_min`) or it has gone quiet
for `stale_min`. Every load goes through the guard first.

Every load of an open tab is a fresh page (the old one closed), and a tab is
given one every RECYCLE_H anyway. Playwright holds every request and response
a page has seen until that page closes - a reload frees none of it - so a tab
kept open for hours filled the container's memory cap and hung the scraper
(27-28 Oct 2026).

Target kinds:
  page     one URL, one tab (e.g. Bookie Bashing's saves page)
  matches  a listing page (kept open) whose match links are opened, a few at
           a time, in their own kept-open tabs (e.g. a Paddy competition)

Targets are targets.toml on the Hub, changed only through scraper/targets.py
(an atomic rename, so a half-written file is never read). The file is checked every
TARGETS_CHECK_S: after an edit a removed or changed target's tabs close, a new
one opens on the next tick - no restart. A file that will not parse is
reported and the running targets are kept. The runner writes its state to
data/status.json every STATUS_EVERY_S, which the Hub reads.

This module wires the pieces: schedule.py says when a target is due,
sinks.py delivers its rows (Render or the Hub), status.py writes the
heartbeat and status.json, guard.py says yes or no to every load.
"""
import collections
import json
import random
import re
import time
from datetime import datetime
from urllib.parse import urlparse

from . import browser
from . import config
from . import schedule
from .capture import Capture, DiskBudget
from .config import host_allowed
from .parsers import PARSERS
from .schedule import (ANY_LOAD_GAP_S, CLICK_EVERY_S, CLICK_JITTER, RECYCLE_H,  # noqa: F401
                       RECYCLE_JITTER, RELIST_JITTER, RELOAD_JITTER)
from .sinks import Sinks, _safe, deliver_to_hub, emit_event  # noqa: F401  (re-exported)
from .sites import BACKOFF_MIN, SITES
from .status import (HEALTH_PING_S, STATUS_EVERY_S, Reporter, market_tabs_said,  # noqa: F401
                     site_status, target_entry)

PARSE_EVERY_S = 60
TARGETS_CHECK_S = 60             # a stat of targets.toml; read only when it changed


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

    def fresh_page(self, page, now):
        self.page = page
        self.crashed = False
        self.recycle_at = schedule.recycle_at(now)
        page.on("crash", lambda _p: setattr(self, "crashed", True))

    def worn(self, now) -> bool:
        """Due a fresh page: its time is up, or its renderer died (a crashed
        page fails every parse until it is replaced)."""
        return self.crashed or now >= self.recycle_at


class SiteState:
    def __init__(self):
        self.blocked_for = None
        self.backoff = BACKOFF_MIN[0]
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
        self.pacer = schedule.Pacer(SITES)            # spacing between page loads
        self.retired: dict[str, collections.deque] = {}
        self.parser_said: dict[str, str] = {}
        self.targets = list(settings.targets)
        self.targets_file = settings.targets_file
        self.targets_mtime = self._mtime()            # load() has just read it
        self.next_targets_check = 0.0
        self.reporter = Reporter(settings)
        self.last_load_at: dict[str, float] = {}       # target name -> last good load
        self.last_parse_at: dict[str, float] = {}      # ... last parse that returned rows
        self.rows_posted: dict[str, int] = {}
        self.retired_targets: dict[str, config.Target] = {}   # removed by a config change
        self.snapshots: set = set()                    # (target, why) saved this run
        (settings.data_dir / "latest").mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "requests").mkdir(parents=True, exist_ok=True)
        # Bet Finder (fetch) targets: next run per target, and the health state
        # the Hub is told about (min_rows breaches, blocks) so it only wakes
        # the local AI on a change.
        self.next_fetch: dict[str, float] = {}
        self.health_said: dict[str, str] = {}
        self.sinks = Sinks(settings)

    # --- site-wide state ----------------------------------------------------
    def _paused(self, site, now) -> str | None:
        rules = SITES[site]
        if self.guard.cooloff_active(site, now):
            return "cooling off - run `resume`"
        if rules.quiet_hours and self.guard.in_quiet(now):
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
        screenshot of the page, also kept in data/blocks/."""
        st = self.sites[site]
        image = self._block_shot(site, tab) if tab else None
        where = f" on {tab.page.url}" if tab else ""
        emit_event(self.s.data_dir, "blocked_source", site, f"{reason}{where}", now)
        if SITES[site].block_means_cooloff:
            self.guard.start_cooloff(site, reason, now)
            self._close_site(site)
            self.alerts.send(f"{site}: {reason}{where} - every page closed, cooling off "
                             f"{self.guard.cooloff_hours:g} h. Needs `resume` after that.", image)
            return
        if reason != st.blocked_for:
            where_to = "over VNC" if browser.in_container() else "on the Hub's screen"
            self.alerts.send(f"{site}: {reason}{where} - needs you {where_to}. Backing off.", image)
        st.blocked_for = reason
        st.retry_at = now + st.backoff * 60 * random.uniform(0.9, 1.2)
        st.backoff = min(st.backoff * 2, BACKOFF_MIN[1])

    def _block_shot(self, site, tab):
        out = self.s.data_dir / "blocks"
        try:
            out.mkdir(parents=True, exist_ok=True)
            for old in sorted(out.glob("*.png"))[:-49]:
                old.unlink(missing_ok=True)
            path = out / f"{datetime.now():%Y%m%d-%H%M%S}-{site}.png"
            tab.page.screenshot(path=str(path))
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
        if st.blocked_for:
            self.alerts.send(f"{site}: clear again ({st.blocked_for} sorted).")
        st.blocked_for, st.backoff, st.retry_at = None, BACKOFF_MIN[0], 0.0

    # --- loading ------------------------------------------------------------
    def _may_load(self, site, now) -> bool:
        return self.pacer.may_load(site, now)

    def _load(self, target, key, url, now) -> Tab | None:
        """Open (or reload) one tab, through the guard and the spacing. None
        if not done."""
        site = target.site
        if not self._may_load(site, now):
            return None
        ok, _why = self.guard.can_load(site, now, quiet_applies=SITES[site].quiet_hours)
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
            print(f"{key}: load failed: {exc.__class__.__name__}: {exc}", flush=True)
            return None

        reason = browser.blocked(tab.page, resp.status if resp else None)
        if reason is None and SITES[site].needs_login and browser.logged_out(tab.page, url):
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
        # TODO(runner): honour Target.window and Target.priority here. They are
        # stored and validated (config._target) and shown by `targets list`,
        # but not acted on yet: a target outside its window should be treated
        # like quiet hours (its tabs closed, not loaded), and when a site's
        # hourly budget cannot cover every due target, loads should go to the
        # highest priority first instead of file order.
        for t in self.targets:
            if self._paused(t.site, now):
                continue
            if t.mode == "fetch":
                self._tick_fetch(t, now)
            elif t.matches:
                self._tick_matches(t, now)
            else:
                self._tick_page(t, t.name, t.url, now)

        for tab in list(self.tabs.values()):
            # A paused site's tab stays open (to log in over VNC) but is not
            # driven: parsing a login page every minute is noise, not data.
            if not self._paused(tab.target.site, now):
                self._parse(tab, now)
        self._requests()
        self._heartbeat(now)
        self._report(now)

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

    def apply_targets(self, targets):
        """Swap in a new target list. A target that is gone, or whose
        definition changed, has its tabs closed; new and changed targets open
        through the normal tick (and its guard) from the next tick on."""
        old = {t.name: t for t in self.targets}
        new = {t.name: t for t in targets}
        if old == new:
            return
        for name, t in old.items():
            if new.get(name) != t:
                for key in [k for k, tab in self.tabs.items() if tab.target.name == name]:
                    self._close(key)
                self.retired.pop(name, None)
                self.next_open.pop(name, None)
                self.parser_said.pop(name, None)
            if name not in new:
                self.retired_targets[name] = t
        for name in new:
            self.retired_targets.pop(name, None)
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        changed = sorted(n for n in set(old) & set(new) if old[n] != new[n])
        print("config: " + "; ".join(f"{label} {', '.join(names)}" for label, names in
                                     (("added", added), ("removed", removed), ("changed", changed))
                                     if names), flush=True)
        self.targets = list(targets)

    def _tick_page(self, t, key, url, now):
        tab = self.tabs.get(key)
        if tab is None:
            self._load(t, key, url, now)
            return
        if schedule.page_due(t, tab, now):
            self._load(t, key, url, now)

    def _tick_fetch(self, t, now):
        """A Bet Finder source: load the page, parse it once, close it. Nothing
        is kept open, so each run is one load against its site's budget. The
        next run is scheduled before parsing, so a broken parser can never turn
        into a reload loop; a load that fails backs off five minutes."""
        due = self.next_fetch.get(t.name)
        if due is not None and now < due:
            return
        ok, _why = self.guard.can_load(t.site, now, quiet_applies=SITES[t.site].quiet_hours)
        if not ok or not self._may_load(t.site, now):
            return                                  # budget or spacing: try next tick
        key = f"fetch:{t.name}"
        tab = self._load(t, key, t.url, now)
        if tab is None:
            self.next_fetch[t.name] = now + schedule.FETCH_RETRY_S
            return
        self.next_fetch[t.name] = schedule.next_fetch_at(now, t.every_min)
        try:
            if t.wait_for:
                try:
                    tab.page.wait_for_selector(t.wait_for, timeout=20_000)
                except Exception:
                    print(f"{t.name}: wait_for {t.wait_for!r} not seen", flush=True)
            self._parse(tab, now, force=True)
        finally:
            self._close(key)

    def _check_rows(self, t, rows, now):
        """Row-count health for fetch targets: fewer than min_rows is a suspected
        layout change. Alert and tell the Hub once per change, not per run."""
        if t.min_rows and len(rows) < t.min_rows:
            msg = f"only {len(rows)} row(s), expected at least {t.min_rows} - layout change?"
            if self.health_said.get(t.name) != msg:
                self.health_said[t.name] = msg
                self.alerts.send(f"{t.name}: {msg}")
                emit_event(self.s.data_dir, "unhealthy_source", t.name, msg, now)
            self.parser_said[t.name] = msg
        elif t.name in self.health_said:
            del self.health_said[t.name]
            self.alerts.send(f"{t.name}: healthy again ({len(rows)} rows)")

    def _deliver(self, t, rows, now) -> bool:
        return self.sinks.deliver(t, rows, now)

    def _tick_matches(self, t, now):
        m = t.matches
        list_key = f"{t.name}:list"
        listing = self.tabs.get(list_key)
        if listing is None:
            self._load(t, list_key, t.url, now)
            return
        if schedule.listing_due(t, listing, now):
            self._load(t, list_key, t.url, now)
            return

        retired = self.retired.setdefault(t.name, collections.deque(maxlen=500))
        mine = {k: tab for k, tab in self.tabs.items() if k.startswith(t.name + ":m:")}
        for key, tab in mine.items():
            quiet = tab.capture.quiet_for(now) >= t.stale_min * 60
            if now - tab.opened >= m.tab_max_hours * 3600 or (quiet and tab.reloaded_stale
                                                              and now - tab.last_load >= t.stale_min * 60):
                print(f"{key}: retired", flush=True)
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

        open_urls = {tab.url for k, tab in self.tabs.items() if k.startswith(t.name + ":m:")}
        if len(open_urls) >= m.max_tabs:
            return
        if now < self.next_open.get(t.name, 0.0) or not self._may_load(t.site, now):
            return
        for url in match_links(listing.page, m.link_re, t.url):
            if url in open_urls or url in retired:
                continue
            self.next_open[t.name] = schedule.next_open_at(now, m.open_gap_min)
            tab = self._load(t, f"{t.name}:m:{url}", url, now)
            if tab:
                self._open_market_tabs(t, tab, now)
            break

    def _open_market_tabs(self, t, tab, now):
        """Click the in-page market tabs, so the page fetches the markets we
        want - on arrival, then every CLICK_EVERY_S, since the page keeps
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
                    print(f"{tab.url}: no {name!r} tab; tabs drawn: "
                          f"{', '.join(browser.visible_tabs(page)) or 'none found'}", flush=True)
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
                print(f"{tab.url}: market tab {label!r}: click failed: {exc}", flush=True)
                continue
            loaded = browser.wait_for_feed(page, cap, clicked_at, feed)
            if page.url != before_url or cap.documents != before_docs:
                self.guard.record_load(t.site, page.url, now)
            tab.market_tabs[name] = "loaded" if loaded else "clicked, nothing new"
            try:
                browser.read_down(page)
            except Exception:
                pass
        tab.next_clicks = schedule.next_clicks_at(now)
        said = ", ".join(f"{n} {o}" for n, o in tab.market_tabs.items())
        if said and said != tab.tabs_said:
            print(f"{tab.url}: market tabs: {said}", flush=True)
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
            (out / "page.url").write_text(tab.page.url)
            (out / "page.html").write_text(tab.page.content(), encoding="utf-8")
            tab.page.screenshot(path=str(out / "page.png"))
        except Exception as exc:
            print(f"{t.name}: snapshot failed: {exc}", flush=True)

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
                print(f"{t.name}: {exc} - capturing only", flush=True)
                self.parser_said[t.name] = f"parser not written ({t.parser}) - capturing only"
            return
        except Exception as exc:
            # Shown as the target's detail in status.json until a call succeeds -
            # a driver that cannot bring its page up says so there, not only here.
            print(f"{t.name}: parser failed: {exc}", flush=True)
            self.parser_said[t.name] = f"parser failed: {str(exc).splitlines()[0][:120]}"
            return
        self.parser_said.pop(t.name, None)
        self._check_rows(t, rows, now)
        out = self.s.data_dir / "latest" / f"{_safe(t.name)}.json"
        out.write_text(json.dumps({"at": now, "url": tab.url, "rows": rows}, indent=1))
        if rows:
            self.last_parse_at[t.name] = now
        if t.post and rows and self._deliver(t, rows, now):
            self.rows_posted[t.name] = self.rows_posted.get(t.name, 0) + len(rows)

    # --- status, for the Hub and `python -m scraper status` ---------------------------
    def status(self, now) -> dict:
        """Per target: state, detail, last load/parse, rows posted. Per site:
        budget spent and cool-off. What `python -m scraper status` prints."""
        targets = []
        for t in [*self.targets, *self.retired_targets.values()]:
            retired = t.name in self.retired_targets
            why = self._paused(t.site, now)
            if retired:
                state, detail = "retired", "removed from targets.toml"
            elif why and (why.startswith("cooling off") or why.startswith("backing off")):
                state, detail = "blocked", why
            elif why:
                state, detail = "paused", why
            else:
                mine = [tab for tab in self.tabs.values() if tab.target.name == t.name]
                n = len(mine)
                state = "running"
                detail = "; ".join(x for x in (f"{n} tab{'s' if n != 1 else ''} open",
                                               market_tabs_said(mine),
                                               self.parser_said.get(t.name)) if x)
            targets.append(target_entry(t, state, detail, self.last_load_at.get(t.name),
                                        self.last_parse_at.get(t.name), self.rows_posted.get(t.name, 0)))
        return {"at": now, "targets_file": str(self.targets_file) if self.targets_file else None,
                "targets": targets, "sites": site_status(self.guard, now)}

    def _report(self, now):
        self.reporter.report(now, self.status)

    def _requests(self):
        """`python -m scraper dump <target>` drops a file; the running loop
        answers it from the page it already has open - no extra load.
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
                print(f"dump {name}: no open page for that target", flush=True)
                continue
            out = self.s.data_dir / "dumps" / f"{_safe(name)}-{datetime.now():%Y%m%d-%H%M%S}"
            out.mkdir(parents=True, exist_ok=True)
            for i, (key, tab) in enumerate(tabs):
                try:
                    (out / f"{i}.url").write_text(tab.page.url)
                    (out / f"{i}.html").write_text(tab.page.content(), encoding="utf-8")
                    tab.page.screenshot(path=str(out / f"{i}.png"), full_page=True)
                except Exception as exc:
                    print(f"dump {key}: {exc}", flush=True)
            print(f"dump {name}: saved {out}", flush=True)

    def _heartbeat(self, now):
        self.reporter.heartbeat(now)

    def close_all(self):
        for key in list(self.tabs):
            self._close(key)


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
