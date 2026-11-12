"""The runner against a fake site on localhost - a real browser, no real traffic.

Needs Playwright and a Chromium. Set SCRAPER_CHROME_PATH if Playwright's own
browser isn't installed.
"""
import http.server
import json
import re
import threading
import time

import pytest

pytest.importorskip("playwright")

from scraper import config  # noqa: E402
from scraper.alerts import Alerts  # noqa: E402
from scraper.guard import Guard  # noqa: E402
from scraper.runner import Runner  # noqa: E402
from scraper.sites import SITES  # noqa: E402

LISTING = """<html><title>Football</title><body>
<a href="/football/arsenal-v-chelsea">Arsenal v Chelsea</a>
<a href="/football/spurs-v-everton">Spurs v Everton</a>
<a href="/football/leeds-v-wolves">Leeds v Wolves</a>
<a href="/football/fulham-v-brentford">Fulham v Brentford</a>
<a href="/football/arsenal-v-chelsea#stats">dup</a>
<a href="/promo">promo</a></body></html>"""

# A second competition's listing, for a job with `listings` (one link repeats
# the first listing's, so it is opened once).
LISTING2 = """<html><title>Serie A</title><body>
<a href="/football/milan-v-roma">Milan v Roma</a>
<a href="/football/arsenal-v-chelsea">Arsenal v Chelsea</a>
<a href="/football/inter-v-juventus">Inter v Juventus</a></body></html>"""

# Two kinds of tab control, as sites draw them: an ARIA tab, and a plain div in
# a tab bar. A click fetches that tab's markets, as Paddy's event page does.
MATCH = """<html><title>Match</title><body>
<div class="tabs__bar"><button role="tab">Corners</button>
<div class="tabs__item" data-t="cards">Cards &amp; Fouls</div><div class="tabs__item">Shots</div></div>
<div id=m style="height:3000px"></div><div id=p></div><script>
async function poll(){ const r = await fetch('/api/prices?m='+location.pathname);
  document.getElementById('p').textContent = (await r.json()).prices.join(','); }
poll(); setInterval(poll, 1000);
for (const el of document.querySelectorAll('[role=tab], .tabs__item'))
  el.addEventListener('click', async () => {
    const r = await fetch('/smspp/event-page/v5?tab=' + encodeURIComponent(el.textContent));
    document.getElementById('m').textContent = (await r.json()).markets.join(','); });
</script></body></html>"""

LOGIN = "<html><title>Sign in</title><body><form><input type=password></form></body></html>"


PAGE = "<html><title>Saves</title><body><table><tr><td>7.5</td></tr></table></body></html>"

# Bookie Bashing's logged-out page as it was on 11 Nov 2026 (login.py), cut
# down: a "Login" menu item (and a hidden mobile copy) opens a hidden pop-up,
# whose form posts in the background, then the page navigates or the refusal
# is drawn in the section's notice.
BB_USER, BB_PASS = "u@example.test", "pw-TEST-ONLY"
BB_LOGIN = """<html><title>How It Works</title><body>
<ul><li class="xoo-el-login-tgr menu-item"><a class="menu-link">Login</a></li></ul>
<ul style="display:none"><li class="xoo-el-login-tgr menu-item"><a class="menu-link">Login</a></li></ul>
<div class="xoo-el-container xoo-el-style-popup" style="visibility: hidden;">
 <ul class="xoo-el-tabs"><li data-tab="login" class="xoo-el-login-tgr">Login</li></ul>
 <div data-section="login" class="xoo-el-section"><div class="xoo-el-fields">
  <div class="xoo-el-notice"></div>
  <form class="xoo-el-action-form xoo-el-form-login">
   <input type="text" name="xoo-el-username" placeholder="Username / Email">
   <input type="password" name="xoo-el-password" placeholder="Password">
   <label><input type="checkbox" name="xoo-el-rememberme" value="forever"> Remember me</label>
   <input type="hidden" name="_xoo_el_form" value="login">
   <button type="submit" class="button btn xoo-el-action-btn xoo-el-login-btn">Sign in</button>
  </form></div></div></div>
<script>
for (const el of document.querySelectorAll('.xoo-el-login-tgr'))
  el.addEventListener('click', () => {
    document.querySelector('.xoo-el-container').style.visibility = 'visible'; });
document.querySelector('.xoo-el-form-login').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await fetch('/wp-admin/admin-ajax.php',
                        {method: 'POST', body: new URLSearchParams(new FormData(e.target))});
  const j = await r.json();
  if (j.error) document.querySelector('[data-section=login] .xoo-el-notice').innerHTML = j.notice;
  else setTimeout(() => { location.href = j.redirect; }, 300);
});
</script></body></html>"""

# The same page with the site's submit handler never bound (its script failed
# to load): the button would have Chrome send the form itself, as a GET to
# this url with the fields in the query.
BB_LOGIN_UNBOUND = re.sub(r"document\.querySelector\('\.xoo-el-form-login'\)\.addEventListener"
                          r"\('submit'.*?\n\}\);\n", "", BB_LOGIN, flags=re.S)
assert "addEventListener('submit'" not in BB_LOGIN_UNBOUND


class Handler(http.server.BaseHTTPRequestHandler):
    blocked_path = None
    seen: list = []                              # every path asked for

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html"):
        b = body.encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path
        Handler.seen.append(path)
        if Handler.blocked_path and path.startswith(Handler.blocked_path):
            return self._send(429, "<title>Too many</title>")
        if path == "/football":
            return self._send(200, LISTING)
        if path == "/serie-a":
            return self._send(200, LISTING2)
        if path.startswith("/football/"):
            return self._send(200, MATCH)
        if path.startswith("/smspp/event-page/"):
            tab = path.split("tab=")[-1]
            return self._send(200, json.dumps({"markets": [tab + " Match Bet"] * 20}),
                              "application/json")
        if path.startswith("/api/prices"):
            return self._send(200, json.dumps({"prices": [1.91] * 40, "pad": "x" * 300}),
                              "application/json")
        if path == "/bb":
            return self._send(200, LOGIN)
        if path == "/bbpage":
            return self._send(200, PAGE)
        if path == "/bbtool":                    # logged out: sent elsewhere, no form
            self.send_response(302)
            self.send_header("location", "/how-it-works")
            self.end_headers()
            return
        if path == "/how-it-works/bb-login":
            return self._send(200, BB_LOGIN)
        if path.startswith("/how-it-works/bb-login-unbound"):
            return self._send(200, BB_LOGIN_UNBOUND)
        if path == "/how-it-works":
            return self._send(200, "<html><title>How It Works</title><body>Subscribe</body></html>")
        self._send(404, "no")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        if self.path == "/wp-admin/admin-ajax.php":     # the BB login form, one test login
            from urllib.parse import parse_qs
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            if (form.get("xoo-el-username"), form.get("xoo-el-password"),
                    form.get("xoo-el-rememberme")) == (BB_USER, BB_PASS, "forever"):
                return self._send(200, json.dumps({"error": 0, "redirect": "/how-it-works/bb-login"}),
                                  "application/json")         # as the real one: back where it was
            notice = ('<div class="xoo-el-notice-error"><span class="xoo-el-noticetxt">The password '
                      f'you entered for the username {form.get("xoo-el-username")} is incorrect.'
                      '</span></div>')
            return self._send(200, json.dumps({"error": 1, "notice": notice}), "application/json")
        self._send(404, "no")


@pytest.fixture
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    Handler.blocked_path = None
    Handler.seen = []
    srv.shutdown()


def _runner(tmp_path, targets, p, targets_file=None):
    from scraper import browser
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=20, targets=targets,
                        targets_file=targets_file)
    guard = Guard(tmp_path / "g.sqlite3", {n: (r.per_hour, r.per_day) for n, r in SITES.items()})
    guard.quiet = (0, 0)
    ctx = browser.launch(p, s, headless=True)
    return Runner(s, ctx, guard, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples"), ctx


def _pp(base):
    # Ticks are at fake times far ahead of the page's real activity, so a long
    # stale_min keeps the open tabs from looking quiet.
    return config.Target(name="pp", site="paddypower", url=base + "/football", parser="pp_most",
                         stale_min=120,
                         matches=config.Matches(max_tabs=3, market_tabs=("Cards", "Corners")))


def test_keeps_match_tabs_open_and_captures(tmp_path, site):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [_pp(site)], p)
        t0 = time.time()
        for off in (0, 1, 400, 800, 1200, 1300):   # open gap is 4-6.4 min
            r.tick(t0 + off)
            ctx.pages[0].wait_for_timeout(1500)
        open_matches = sorted(k for k in r.tabs if ":m:" in k)
        assert len(open_matches) == 3                       # capped at max_tabs
        assert not any("fulham" in k for k in open_matches)  # the 4th waits
        assert r.guard.spent("paddypower", t0 + 1300)[0] == 4  # 1 listing + 3 matches, no reloads
        samples = list((tmp_path / "samples").rglob("*-http.txt"))
        assert any('"prices"' in f.read_text() for f in samples)
        r.close_all()
        ctx.close()


def test_a_job_with_two_listings_interleaves_their_matches(tmp_path, site):
    """`listings`: a second competition's listing page in the same job. Each
    listing has its own kept-open tab (one load per tick, the url's first);
    the match links are interleaved, so each listing gets a tab before either
    gets two, all under the one max_tabs; each listing is relisted on its own
    clock; and the job's tabs, listings included, close as one."""
    import dataclasses
    from playwright.sync_api import sync_playwright
    pp = _pp(site)
    # no market tabs: their click rounds are slow and not what is tested here
    t = dataclasses.replace(pp, matches=dataclasses.replace(pp.matches, market_tabs=(),
                                                            listings=(site + "/serie-a",)))
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [t], p)
        t0 = time.time()
        opened, spent = [], 0
        # Paddy loads are spaced 90-162 s, match tabs 4-6.4 min; all before a
        # page's first recycle (RECYCLE_H, 25.5 min at the soonest).
        for off in (0, 1, 170, 340, 730, 1120, 1500):
            before = set(r.tabs)
            r.tick(t0 + off)
            ctx.pages[0].wait_for_timeout(500)
            now_spent = r.guard.spent("paddypower", t0 + off)[0]
            assert now_spent - spent <= 1                    # one load per tick at most
            spent = now_spent
            opened += sorted(set(r.tabs) - before)
        assert opened == ["pp:list", "pp:list:1",
                          f"pp:m:{site}/football/arsenal-v-chelsea",
                          f"pp:m:{site}/football/milan-v-roma",
                          f"pp:m:{site}/football/spurs-v-everton"]
        assert spent == 5                                    # 2 listings + 3 matches (max_tabs)
        assert r.status(t0 + 1500)["targets"][0]["detail"].startswith("5 tabs open")

        # each listing on its own clock: the second is due, the first is not
        for tab in r.tabs.values():                          # no page recycle in the way
            tab.recycle_at = t0 + 99_999
        first, second = r.tabs["pp:list"], r.tabs["pp:list:1"]
        first.due_at, second.due_at = t0 + 99_999, t0 + 2300
        r.tick(t0 + 2400)
        assert r.guard.spent("paddypower", t0 + 2400)[0] == 6
        assert r.tabs["pp:list:1"].last_load == t0 + 2400 and r.tabs["pp:list"].last_load == t0
        first.due_at = t0 + 2700
        r.tick(t0 + 2800)
        assert r.guard.spent("paddypower", t0 + 2800)[0] == 7
        assert r.tabs["pp:list"].last_load == t0 + 2800
        assert r.tabs["pp:list:1"].last_load == t0 + 2400

        r.apply_targets([pp])                               # listings changed: the job reopens
        assert not r.tabs
        r.close_all()
        ctx.close()


def test_block_closes_site_and_cools_off(tmp_path, site):
    from playwright.sync_api import sync_playwright
    Handler.blocked_path = "/football/spurs"
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [_pp(site)], p)
        t0 = time.time()
        for off in (0, 400, 800):                           # spurs is the 2nd match
            r.tick(t0 + off)
        assert r.guard.cooloff_active("paddypower", t0 + 800)
        assert not r.tabs                                   # every Paddy page closed
        assert "cooling off" in (tmp_path / "alerts.log").read_text()
        assert list((tmp_path / "blocks").glob("*.png"))    # what it looked like
        n = r.guard.spent("paddypower", t0 + 800)[0]
        r.tick(t0 + 1200)
        assert r.guard.spent("paddypower", t0 + 1200)[0] == n  # no traffic while cooling off
        ctx.close()


def test_logged_out_alerts_once_and_keeps_tab(tmp_path, site):
    from playwright.sync_api import sync_playwright
    bb = config.Target(name="bb", site="bookiebashing", url=site + "/bb", every_min=10)
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [bb], p)
        t0 = time.time()
        r.tick(t0)
        assert "bb" in r.tabs                               # left open to log in over VNC
        assert r.sites["bookiebashing"].blocked_for == "logged out"
        r.tick(t0 + 60)                                     # backing off: no reload
        assert r.guard.spent("bookiebashing", t0 + 60)[0] == 1
        assert (tmp_path / "alerts.log").read_text().count("logged out") == 1
        ctx.close()


def test_a_redirect_away_from_the_tool_is_logged_out(tmp_path, site):
    """Bookie Bashing's logged-out answer to a tool url is a redirect to its
    "How it works" page, with no visible password field. That is logged out
    too - and the parser is not driven against it."""
    from playwright.sync_api import sync_playwright
    bb = config.Target(name="bb", site="bookiebashing", url=site + "/bbtool", parser="bb_saves")
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [bb], p)
        t0 = time.time()
        r.tick(t0)
        assert r.sites["bookiebashing"].blocked_for == "logged out"
        r.tick(t0 + 61)
        assert "parser" not in (r.parser_said.get("bb") or "")  # not driven while paused
        ctx.close()


def test_bb_login_form(tmp_path, site):
    """login.attempt on a page built like Bookie Bashing's logged-out page:
    it opens the hidden pop-up, signs in and the page reloads where it was
    (the pop-up gone); a wrong login comes back as the site's refusal,
    without the username in it."""
    from playwright.sync_api import sync_playwright
    from scraper import login
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [], p)
        page = ctx.new_page()
        page.goto(site + "/how-it-works/bb-login")
        why = login.attempt(page, "bookiebashing", (BB_USER, "not-the-password"), timeout_s=10)
        assert why.startswith("refused: ") and "is incorrect" in why
        assert BB_USER not in why and "not-the-password" not in why
        page.goto(site + "/how-it-works/bb-login")
        assert login.attempt(page, "bookiebashing", (BB_USER, BB_PASS), timeout_s=10) is None
        page.wait_for_load_state()
        assert not page.locator("form.xoo-el-form-login").is_visible()
        ctx.close()


def test_bb_login_form_sent_by_chrome_never_leaves_it(tmp_path, site):
    """The site's script never bound the form: pressing the button would
    have Chrome send it as a GET with the password in the url. That request
    is aborted inside Chrome: the server never sees it, and the page's url
    never carries the password."""
    from playwright.sync_api import sync_playwright
    from scraper import login
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [], p)
        page = ctx.new_page()
        page.goto(site + "/how-it-works/bb-login-unbound")
        why = login.attempt(page, "bookiebashing", (BB_USER, BB_PASS), timeout_s=10)
        assert why == "form not sent by the site's script"
        assert BB_PASS not in page.url and "xoo-el-password" not in page.url
        page.goto(site + "/bbpage")                         # nothing left routed
        assert not any("xoo-el-password" in path for path in Handler.seen)
        ctx.close()


def test_config_change_closes_removed_tabs_and_opens_new_ones(tmp_path, site):
    """An edit to targets.toml reaches the running scraper on its next check:
    the removed target's tabs close, the new target opens - no restart - and
    data/status.json says so."""
    import os
    from playwright.sync_api import sync_playwright
    from scraper.runner import TARGETS_CHECK_S
    toml = tmp_path / "targets.toml"

    def write(text):                                         # an mtime that has moved
        toml.write_text(text)
        st = toml.stat()
        os.utime(toml, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

    pp = _pp(site)
    write(f'[[target]]\nname="pp"\nsite="paddypower"\nurl="{pp.url}"\nparser="pp_most"\n'
          f'stale_min=120\n[target.matches]\nmax_tabs={pp.matches.max_tabs}\n'
          f'market_tabs={list(pp.matches.market_tabs)!r}\n'.replace("\'", '"'))
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, config.load_targets(toml), p, targets_file=toml)
        status = lambda: json.loads((tmp_path / "status.json").read_text())
        t0 = time.time()
        r.tick(t0)
        r.tick(t0 + 200)                                     # loads are spaced
        assert any(k.startswith("pp:m:") for k in r.tabs) and "pp:list" in r.tabs
        assert [(t["name"], t["state"]) for t in status()["targets"]] == [("pp", "running")]

        write(f'[[target]]\nname="bb"\nsite="bookiebashing"\nurl="{site}/bbpage"\n'
              'every_min=10\nparser="bb_saves"\n')         # edited by HubAI
        r.tick(t0 + 230)                                     # not due yet
        assert "pp:list" in r.tabs and "bb" not in r.tabs
        r.tick(t0 + 200 + TARGETS_CHECK_S + 5)
        assert not any(k.startswith("pp") for k in r.tabs)   # every pp tab closed
        assert "bb" in r.tabs                                # the new one opened
        r.tick(t0 + 200 + 2 * TARGETS_CHECK_S + 10)          # the next status write
        states = {t["name"]: t for t in status()["targets"]}
        assert states["pp"]["state"] == "retired" and states["bb"]["state"] == "running"
        # The fake "Saves" page is not the bet builder, so its driver fails -
        # and that failure is the target's detail in status.json.
        assert "parser failed" in states["bb"]["detail"]
        assert {s["site"] for s in status()["sites"]} == set(SITES)
        assert status()["sites"][0]["loads_hour"] >= 1

        write("")
        r.tick(t0 + 200 + 3 * TARGETS_CHECK_S + 15)
        assert not r.tabs
        r.close_all()
        ctx.close()


def test_market_tabs_are_clicked_and_their_markets_arrive(tmp_path, site):
    """Each market tab is found (an ARIA tab, or a plain div in a tab bar; a
    prefix finds "Cards & Fouls"), clicked with the mouse, and its markets
    arrive and are pinned. One that is not there is reported, and the page is
    saved once so the locator can be fixed from it."""
    from playwright.sync_api import sync_playwright
    t = config.Target(name="pp", site="paddypower", url=site + "/football", stale_min=120,
                      matches=config.Matches(max_tabs=1,
                                             market_tabs=("Corners", "Cards", "Goalscorer")))
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [t], p)
        t0 = time.time()
        r.tick(t0)
        r.tick(t0 + 400)
        tab = next(tab for k, tab in r.tabs.items() if ":m:" in k)
        assert tab.market_tabs == {"Corners": "loaded", "Cards": "loaded",
                                   "Goalscorer": "not offered"}
        labels = {p[4] for p in tab.capture.pinned if "event-page" in p[2]}
        assert labels == {"tab:Corners", "tab:Cards & Fouls"}
        detail = r.status(t0 + 400)["targets"][0]["detail"]
        assert "Corners loaded 1/1" in detail and "Goalscorer not offered 1/1" in detail
        assert (tmp_path / "samples" / "pp-market-tab-not-found" / "page.html").exists()
        assert r.guard.spent("paddypower", t0 + 400)[0] == 2   # clicks are not page loads
        r.close_all()
        ctx.close()


def test_open_page_refused_cools_off_with_a_screenshot(tmp_path, site):
    """No load fails: the tab is already open when the site starts refusing
    its price requests. The tick sees it on the open page and stops."""
    from playwright.sync_api import sync_playwright
    t = config.Target(name="pp", site="paddypower", url=site + "/football", stale_min=120,
                      matches=config.Matches(max_tabs=1))
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [t], p)
        t0 = time.time()
        r.tick(t0)
        r.tick(t0 + 400)
        assert any(":m:" in k for k in r.tabs)
        r.tick(t0 + 415)
        assert not r.guard.cooloff_active("paddypower", t0 + 415)   # all well so far
        Handler.blocked_path = "/api/prices"
        ctx.pages[0].wait_for_timeout(4500)                       # it polls every second
        r.tick(t0 + 430)
        assert r.guard.cooloff_active("paddypower", t0 + 430)
        assert not r.tabs
        log = (tmp_path / "alerts.log").read_text()
        assert "repeated 403/429 on an open page" in log and "cooling off" in log
        assert list((tmp_path / "blocks").glob("*-paddypower.png"))
        ctx.close()


def test_cooloff_alerts_once_when_served_and_once_when_running(tmp_path):
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=1, targets=[])
    guard = Guard(tmp_path / "g.sqlite3", {n: (r.per_hour, r.per_day) for n, r in SITES.items()})
    guard.quiet = (0, 0)
    r = Runner(s, None, guard, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples")
    t0 = time.time()
    guard.start_cooloff("paddypower", "HTTP 429", t0)
    log = lambda: (tmp_path / "alerts.log").read_text() if (tmp_path / "alerts.log").exists() else ""
    r.tick(t0 + 60)
    r.tick(t0 + 3600)
    assert "waiting on `resume`" not in log()
    served = t0 + 24 * 3600 + 5
    r.tick(served)
    r.tick(served + 60)
    assert log().count("waiting on `resume`") == 1
    assert "running again" not in log()
    guard.resume("paddypower")
    r.tick(served + 120)
    r.tick(served + 180)
    assert log().count("running again") == 1


def test_selfcheck_reports_what_a_site_sees(tmp_path):
    """One coherent UK Chrome, set the way Chrome sets itself: its own en-GB
    language list (not a bare override), London time, a real window. And it
    is a person's Chrome, not a test run: Playwright's --enable-automation is
    not passed and navigator.webdriver is false (Callum's call, 26 Oct 2026)."""
    from playwright.sync_api import sync_playwright
    from scraper import browser
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [], p)
        (tmp_path / "requests" / "selfcheck").touch()
        r.tick()
        seen = json.loads((tmp_path / "selfcheck.json").read_text())
        ctx.close()
    assert seen["language"] == "en-GB" and seen["languages"][0] == "en-GB"
    assert len(seen["languages"]) > 1                 # Chrome's own list
    assert seen["timezone"] == "Europe/London"
    # A real 1600x1000 window, not the old emulated viewport. The inner height
    # depends on the browser's own chrome (857 in Google Chrome, the full 1000
    # in CI's bundled headless Chromium), so only the window itself is fixed.
    assert seen["outer"] == [1600, 1000] and seen["inner"][1] <= 1000
    assert seen["webdriver"] is False
    # The flags are read off chrome://version, which CI's headless shell does
    # not render; webdriver being false above is the effect of them anyway.
    if seen["command_line_read"]:
        assert seen["enable_automation"] is False
        assert seen["automation_controlled_disabled"] is True
    lines = browser.selfcheck_lines(seen, "test")
    assert any(line.split()[:2] == ["navigator.webdriver", "false"] for line in lines)


def test_screenshot_of_a_tab_that_is_not_in_front(tmp_path):
    """browser.screenshot brings the tab forward and takes the picture with a
    time limit: a tab behind another is never drawn, and a picture of it
    waited 30 s (Playwright) or for ever (a direct capture) on the Scraper's
    own screen, 12 Nov 2026."""
    from playwright.sync_api import sync_playwright
    from scraper import browser
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=20, targets=[])
    with sync_playwright() as p:
        ctx = browser.launch(p, s, headless=True)
        try:
            behind = ctx.new_page()
            behind.goto("data:text/html,<body style='margin:0'><div style='height:3000px;background:teal'>x</div>")
            front = ctx.new_page()
            front.goto("data:text/html,<p>in front</p>")
            browser.screenshot(behind, tmp_path / "view.png")
            browser.screenshot(behind, tmp_path / "full.png", full_page=True)
            sizes = {}
            for name in ("view", "full"):
                raw = (tmp_path / f"{name}.png").read_bytes()
                assert raw[:8] == b"\x89PNG\r\n\x1a\n"
                sizes[name] = int.from_bytes(raw[20:24], "big")        # the PNG's height
            assert sizes["full"] >= 3000 > sizes["view"]
            assert front.inner_text("p") == "in front"
            # Bounded: a picture that will not come is an error in seconds, never a wait for ever.
            import inspect
            src = inspect.getsource(browser.screenshot)
            assert "bring_to_front()" in src and "timeout=SCREENSHOT_TIMEOUT_MS" in src
            assert 0 < browser.SCREENSHOT_TIMEOUT_MS <= 15_000 and "new_cdp_session" not in src
        finally:
            ctx.close()


def test_the_runner_takes_no_playwright_screenshot():
    """Every picture goes through browser.screenshot (above)."""
    import pathlib
    src = (pathlib.Path(__file__).parents[1] / "runner.py").read_text()
    assert ".page.screenshot(" not in src and src.count("browser.screenshot(") == 3
