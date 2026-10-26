"""The loop: keep each target's pages open, and touch the site as little as possible.

A kept-open page is refreshed by the site's own scripts, so after the first
load most targets cost NOTHING per tick — the capture just listens. A page is
reloaded only when its target asks for it (`every_min`) or it has gone quiet
for `stale_min`. Every load goes through the guard first.

Target kinds:
  page     one URL, one tab (e.g. Bookie Bashing's saves page)
  matches  a listing page (kept open) whose match links are opened, a few at
           a time, in their own kept-open tabs (e.g. a Paddy competition)
"""
import collections
import json
import random
import re
import time
from datetime import datetime
from urllib.parse import urlparse

from . import browser
from .capture import Capture, DiskBudget
from .config import host_allowed
from .parsers import PARSERS
from .sites import BACKOFF_MIN, SITES

PARSE_EVERY_S = 60
HEALTH_PING_S = 300


class Tab:
    def __init__(self, target, url, page, capture):
        self.target, self.url, self.page, self.capture = target, url, page, capture
        self.opened = time.time()
        self.last_load = self.opened
        self.reloaded_stale = False        # reloaded once for going quiet
        self.next_parse = 0.0


class SiteState:
    def __init__(self):
        self.blocked_for = None
        self.backoff = BACKOFF_MIN[0]
        self.retry_at = 0.0


class Runner:
    def __init__(self, settings, ctx, guard, alerts, run_dir):
        self.s, self.ctx, self.guard, self.alerts = settings, ctx, guard, alerts
        self.run_dir = run_dir
        self.budget = DiskBudget(settings.capture_mb)
        self.tabs: dict[str, Tab] = {}             # key -> Tab
        self.sites = {name: SiteState() for name in SITES}
        self.paused_said: dict[str, str] = {}
        self.last_open: dict[str, float] = {}
        self.retired: dict[str, collections.deque] = {}
        self.parser_said: set[str] = set()
        self.next_health = 0.0
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

    def _on_blocked(self, site, reason, now):
        st = self.sites[site]
        if SITES[site].block_means_cooloff:
            self.guard.start_cooloff(site, reason, now)
            self._close_site(site)
            self.alerts.send(f"{site}: {reason} - every page closed, cooling off "
                             f"{self.guard.cooloff_hours:g} h. Needs `resume` after that.")
            return
        if reason != st.blocked_for:
            self.alerts.send(f"{site}: {reason} - needs you over VNC. Backing off.")
        st.blocked_for = reason
        st.retry_at = now + st.backoff * 60 * random.uniform(0.9, 1.2)
        st.backoff = min(st.backoff * 2, BACKOFF_MIN[1])

    def _on_clear(self, site):
        st = self.sites[site]
        if st.blocked_for:
            self.alerts.send(f"{site}: clear again ({st.blocked_for} sorted).")
        st.blocked_for, st.backoff, st.retry_at = None, BACKOFF_MIN[0], 0.0

    # --- loading ------------------------------------------------------------
    def _load(self, target, key, url, now) -> Tab | None:
        """Open (or reload) one tab, through the guard. None if not done."""
        site = target.site
        ok, _why = self.guard.can_load(site, now, quiet_applies=SITES[site].quiet_hours)
        if not ok or not host_allowed(url):
            return None
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
        except Exception as exc:
            self.guard.record_load(site, url, now)     # a failed load still went out
            print(f"{key}: load failed: {exc.__class__.__name__}: {exc}", flush=True)
            return None

        reason = browser.blocked(tab.page, resp.status if resp else None)
        if reason is None and SITES[site].needs_login and browser.logged_out(tab.page):
            reason = "logged out"
        if reason:
            self._on_blocked(site, reason, now)
            return None
        self._on_clear(site)
        browser.dismiss_consent(tab.page)
        browser.settle(tab.page)
        return tab

    # --- the tick -----------------------------------------------------------
    def tick(self, now=None):
        now = now or time.time()
        for site in SITES:
            why = self._paused(site, now)
            if why != self.paused_said.get(site):
                print(f"{site}: {'paused - ' + why if why else 'running'}", flush=True)
            self.paused_said[site] = why
            # Cool-off and quiet hours mean NO traffic, so the pages close. A
            # back-off leaves them open: a logged-out tab is where you log in.
            if why and not why.startswith("backing off"):
                self._close_site(site)

        for t in self.s.targets:
            if self._paused(t.site, now):
                continue
            if t.matches:
                self._tick_matches(t, now)
            else:
                self._tick_page(t, t.name, t.url, now)

        for tab in list(self.tabs.values()):
            self._parse(tab, now)
        self._requests()
        self._heartbeat(now)

    def _tick_page(self, t, key, url, now):
        tab = self.tabs.get(key)
        if tab is None:
            self._load(t, key, url, now)
            return
        due = t.every_min and now - tab.last_load >= t.every_min * 60 * random.uniform(0.75, 1.35)
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
        if now - listing.last_load >= m.relist_min * 60 * random.uniform(0.9, 1.3):
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
        gap = m.open_gap_min * 60 * random.uniform(1.0, 1.6)
        if now - self.last_open.get(t.name, 0) < gap:
            return
        for url in match_links(listing.page, m.link_re, t.url):
            if url in open_urls or url in retired:
                continue
            self.last_open[t.name] = now
            tab = self._load(t, f"{t.name}:m:{url}", url, now)
            if tab:
                self._open_market_tabs(t, tab, now)
            break

    def _open_market_tabs(self, t, tab, now):
        """Click the in-page market tabs once, so the page fetches (and then
        keeps refreshing) every market we want. A click that navigates counts
        as a load."""
        for name in t.matches.market_tabs:
            pat = re.compile(re.escape(name), re.I)
            for role in ("tab", "link", "button"):
                loc = tab.page.get_by_role(role, name=pat)
                try:
                    if loc.count() and loc.first.is_visible():
                        before = tab.page.url
                        if not self.guard.can_load(t.site, now)[0]:
                            return
                        loc.first.click(timeout=5000)
                        tab.page.wait_for_timeout(random.randint(2500, 6000))
                        if tab.page.url != before:
                            self.guard.record_load(t.site, tab.page.url, now)
                        break
                except Exception:
                    continue

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
                self.parser_said.add(t.name)
            return
        except Exception as exc:
            print(f"{t.name}: parser failed: {exc}", flush=True)
            return
        out = self.s.data_dir / "latest" / f"{_safe(t.name)}.json"
        out.write_text(json.dumps({"at": now, "url": tab.url, "rows": rows}, indent=1))
        if t.post and rows:
            self._post(t, rows)

    def _post(self, t, rows):
        if not self.s.ingest_key:
            return
        import httpx
        try:
            r = httpx.post(self.s.puntmore_url.rstrip("/") + t.post, json={"prices": rows},
                           headers={"X-API-Key": self.s.ingest_key}, timeout=30)
            r.raise_for_status()
            print(f"{t.name}: sent {len(rows)} -> {r.json()}", flush=True)
        except Exception as exc:
            print(f"{t.name}: send failed: {exc}", flush=True)

    def _requests(self):
        """`python -m scraper dump <target>` drops a file; the running loop
        answers it from the page it already has open - no extra load."""
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
