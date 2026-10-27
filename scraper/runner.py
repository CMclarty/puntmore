"""The loop: keep each target's pages open, and touch the site as little as possible.

A kept-open page is refreshed by the site's own scripts, so after the first
load most targets cost NOTHING per tick — the capture just listens. A page is
reloaded only when its target asks for it (`every_min`) or it has gone quiet
for `stale_min`. Every load goes through the guard first.

Target kinds:
  page     one URL, one tab (e.g. Bookie Bashing's saves page)
  matches  a listing page (kept open) whose match links are opened, a few at
           a time, in their own kept-open tabs (e.g. a Paddy competition)

Targets set on Puntmore's /scraper page are re-fetched every CONFIG_EVERY_S:
a removed or changed target's tabs close, a new one opens on the next tick —
no restart. The runner reports its state back every STATUS_EVERY_S.
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
from .capture import Capture, DiskBudget
from .config import host_allowed
from .parsers import PARSERS
from .sites import BACKOFF_MIN, SITES

PARSE_EVERY_S = 60
HEALTH_PING_S = 300
CONFIG_EVERY_S = 300
STATUS_EVERY_S = 60
STATUS_PATH = "/api/scraper/status"
# Jitter on timed reloads, drawn ONCE per load. (Drawing it afresh every 15 s
# tick, as before, made a reload fire at the low end nearly every time.)
RELOAD_JITTER = (0.75, 1.35)     # every_min
RELIST_JITTER = (0.9, 1.3)       # a listing's relist_min
ANY_LOAD_GAP_S = (20, 45)        # between any two page loads, whatever the site


class Tab:
    def __init__(self, target, url, page, capture):
        self.target, self.url, self.page, self.capture = target, url, page, capture
        self.opened = time.time()
        self.last_load = self.opened
        self.reloaded_stale = False        # reloaded once for going quiet
        self.due_at: float | None = None   # next timed reload, drawn once per load
        self.next_parse = 0.0
        self.market_tabs: dict[str, str] = {}   # market tab name -> what the click did


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
        self.next_load_at: dict[str, float] = {}      # site -> earliest next page load
        self.next_any_load = 0.0                      # ... of any site
        self.retired: dict[str, collections.deque] = {}
        self.parser_said: dict[str, str] = {}
        self.next_health = 0.0
        self.targets = list(settings.targets)
        self.source = settings.targets_source         # server | cache | toml
        self.next_config: float | None = None
        self.next_status = 0.0
        self.last_load_at: dict[str, float] = {}       # target name -> last good load
        self.last_parse_at: dict[str, float] = {}      # ... last parse that returned rows
        self.rows_posted: dict[str, int] = {}
        self.retired_targets: dict[str, config.Target] = {}   # removed by a config change
        self.snapshots: set = set()                    # (target, why) saved this run
        (settings.data_dir / "latest").mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "requests").mkdir(parents=True, exist_ok=True)

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
        if SITES[site].block_means_cooloff:
            self.guard.start_cooloff(site, reason, now)
            self._close_site(site)
            self.alerts.send(f"{site}: {reason}{where} - every page closed, cooling off "
                             f"{self.guard.cooloff_hours:g} h. Needs `resume` after that.", image)
            return
        if reason != st.blocked_for:
            self.alerts.send(f"{site}: {reason}{where} - needs you over VNC. Backing off.", image)
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
        """Spacing between page loads: one person opens one page, looks at
        it, and only then opens the next. Nothing is opened in a burst - not
        at startup, not when quiet hours end, not when several tabs go stale
        together."""
        return now >= self.next_load_at.get(site, 0.0) and now >= self.next_any_load

    def _spaced(self, site, now):
        lo = SITES[site].load_gap_s
        self.next_load_at[site] = now + lo * random.uniform(1.0, 1.8)
        self.next_any_load = now + random.uniform(*ANY_LOAD_GAP_S)

    def _load(self, target, key, url, now) -> Tab | None:
        """Open (or reload) one tab, through the guard and the spacing. None
        if not done."""
        site = target.site
        if not self._may_load(site, now):
            return None
        ok, _why = self.guard.can_load(site, now, quiet_applies=SITES[site].quiet_hours)
        if not ok or not host_allowed(url):
            return None
        self._spaced(site, now)
        tab = self.tabs.get(key)
        try:
            if tab is None:
                page = self.ctx.new_page()
                cap = Capture(page, self.run_dir / _safe(key), self.budget)
                tab = Tab(target, url, page, cap)
                self.tabs[key] = tab
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            else:
                resp = tab.page.reload(wait_until="domcontentloaded", timeout=60_000)
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
        self._refresh_config(now)
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
        for t in self.targets:
            if self._paused(t.site, now):
                continue
            if t.matches:
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

    # --- config from Puntmore ----------------------------------------------------
    def _refresh_config(self, now):
        if not self.s.from_server:
            return
        if self.next_config is None:                 # load() has just fetched it
            self.next_config = now + CONFIG_EVERY_S
            return
        if now < self.next_config:
            return
        self.next_config = now + CONFIG_EVERY_S
        try:
            targets = config.fetch_server(self.s.puntmore_url, self.s.ingest_key, self.s.data_dir)
        except Exception as exc:                     # keep what is running
            print(f"config: refresh failed ({exc.__class__.__name__}: {exc}); "
                  "keeping the current targets", flush=True)
            return
        self.source = "server"
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
        if t.every_min and tab.due_at is None:     # drawn once per load, not per tick
            tab.due_at = tab.last_load + t.every_min * 60 * random.uniform(*RELOAD_JITTER)
        due = t.every_min and now >= tab.due_at
        stale = tab.capture.quiet_for(now) >= t.stale_min * 60 and now - tab.last_load >= t.stale_min * 60
        if due or stale:
            self._load(t, key, url, now)

    def _tick_matches(self, t, now):
        m = t.matches
        list_key = f"{t.name}:list"
        listing = self.tabs.get(list_key)
        if listing is None:
            self._load(t, list_key, t.url, now)
            return
        if listing.due_at is None:
            listing.due_at = listing.last_load + m.relist_min * 60 * random.uniform(*RELIST_JITTER)
        if now >= listing.due_at:
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

        open_urls = {tab.url for k, tab in self.tabs.items() if k.startswith(t.name + ":m:")}
        if len(open_urls) >= m.max_tabs:
            return
        if now < self.next_open.get(t.name, 0.0) or not self._may_load(t.site, now):
            return
        for url in match_links(listing.page, m.link_re, t.url):
            if url in open_urls or url in retired:
                continue
            self.next_open[t.name] = now + m.open_gap_min * 60 * random.uniform(1.0, 1.6)
            tab = self._load(t, f"{t.name}:m:{url}", url, now)
            if tab:
                self._open_market_tabs(t, tab, now)
            break

    def _open_market_tabs(self, t, tab, now):
        """Click the in-page market tabs once, so the page fetches (and then
        keeps refreshing) the markets we want. Each click goes through the
        mouse, then waits for the page's own network to bring the tab's
        markets in, then reads down them. What happened to each is kept on
        the tab for the status report: loaded | clicked, nothing new |
        not offered | not clicked (budget) | click failed. A click that
        navigates counts as a load."""
        feed = re.compile(SITES[t.site].market_list or r"$^")
        page, cap = tab.page, tab.capture
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
        if tab.market_tabs:
            print(f"{tab.url}: market tabs: "
                  + ", ".join(f"{n} {o}" for n, o in tab.market_tabs.items()), flush=True)

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
    def _parse(self, tab, now):
        t = tab.target
        if not t.parser or now < tab.next_parse:
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
            # Shown as the target's detail on /scraper until a call succeeds -
            # a driver that cannot bring its page up says so there, not only here.
            print(f"{t.name}: parser failed: {exc}", flush=True)
            self.parser_said[t.name] = f"parser failed: {str(exc).splitlines()[0][:120]}"
            return
        self.parser_said.pop(t.name, None)
        out = self.s.data_dir / "latest" / f"{_safe(t.name)}.json"
        out.write_text(json.dumps({"at": now, "url": tab.url, "rows": rows}, indent=1))
        if rows:
            self.last_parse_at[t.name] = now
        if t.post and rows and self._post(t, rows):
            self.rows_posted[t.name] = self.rows_posted.get(t.name, 0) + len(rows)

    def _post(self, t, rows) -> bool:
        if not self.s.ingest_key:
            return False
        import httpx
        try:
            r = httpx.post(self.s.puntmore_url.rstrip("/") + t.post, json={"prices": rows},
                           headers={"X-API-Key": self.s.ingest_key}, timeout=30)
            r.raise_for_status()
            print(f"{t.name}: sent {len(rows)} -> {r.json()}", flush=True)
            return True
        except Exception as exc:
            print(f"{t.name}: send failed: {exc}", flush=True)
            return False

    # --- status back to Puntmore -------------------------------------------------
    def status(self, now) -> dict:
        """Per target: state, detail, last load/parse, rows posted. Per site:
        budget spent and cool-off. What `python -m scraper status` prints."""
        targets = []
        for t in [*self.targets, *self.retired_targets.values()]:
            retired = t.name in self.retired_targets
            why = self._paused(t.site, now)
            if retired:
                state, detail = "retired", "removed on the page"
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
            targets.append({"name": t.name, "site": t.site, "url": t.url, "state": state,
                            "detail": detail, "last_load": self.last_load_at.get(t.name),
                            "last_parse": self.last_parse_at.get(t.name),
                            "rows_posted": self.rows_posted.get(t.name, 0)})
        return {"at": now, "source": self.source,
                "targets": targets, "sites": site_status(self.guard, now)}

    def _report(self, now):
        if now < self.next_status:
            return
        self.next_status = now + STATUS_EVERY_S
        body = self.status(now)
        try:
            (self.s.data_dir / "status.json").write_text(json.dumps(body, indent=1))
        except OSError:
            pass
        if not (self.s.from_server and self.s.ingest_key):
            return
        import httpx
        try:
            r = httpx.post(self.s.puntmore_url.rstrip("/") + STATUS_PATH,
                           json={"targets": body["targets"], "sites": body["sites"]},
                           headers={"X-API-Key": self.s.ingest_key}, timeout=15)
            r.raise_for_status()
        except Exception as exc:
            print(f"status: send failed: {exc}", flush=True)

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
        (self.s.data_dir / "heartbeat").write_text(str(int(now)))
        if self.s.healthcheck_url and now >= self.next_health:
            self.next_health = now + HEALTH_PING_S
            import httpx
            try:
                httpx.get(self.s.healthcheck_url, timeout=10)
            except Exception:
                pass

    def close_all(self):
        for key in list(self.tabs):
            self._close(key)


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


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:120]
