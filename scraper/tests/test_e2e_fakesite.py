"""The runner against a fake site on localhost - a real browser, no real traffic.

Needs Playwright and a Chromium. Set SCRAPER_CHROME_PATH if Playwright's own
browser isn't installed.
"""
import http.server
import json
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


class Handler(http.server.BaseHTTPRequestHandler):
    blocked_path = None
    config = {"targets": []}          # what /api/scraper/config serves
    reports: list = []                # what /api/scraper/status received

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
        if Handler.blocked_path and path.startswith(Handler.blocked_path):
            return self._send(429, "<title>Too many</title>")
        if path == "/football":
            return self._send(200, LISTING)
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
        if path == "/api/scraper/config":
            return self._send(200, json.dumps(Handler.config), "application/json")
        self._send(404, "no")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        if self.path == "/api/scraper/status":
            Handler.reports.append(json.loads(body))
            return self._send(200, "{}", "application/json")
        self._send(404, "no")


@pytest.fixture
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Handler.config, Handler.reports = {"targets": []}, []
    yield f"http://127.0.0.1:{srv.server_port}"
    Handler.blocked_path = None
    srv.shutdown()


def _runner(tmp_path, targets, p, server=None):
    from scraper import browser
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, puntmore_url=server or "http://x",
                        ingest_key="k" if server else None, capture_mb=20, targets=targets,
                        from_server=bool(server), targets_source="server" if server else "toml")
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


def test_block_closes_site_and_cools_off(tmp_path, site):
    from playwright.sync_api import sync_playwright
    Handler.blocked_path = "/football/spurs"
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [_pp(site)], p)
        t0 = time.time()
        for off in (0, 1, 400):
            r.tick(t0 + off)
        assert r.guard.cooloff_active("paddypower", t0 + 400)
        assert not r.tabs                                   # every Paddy page closed
        assert "cooling off" in (tmp_path / "alerts.log").read_text()
        n = r.guard.spent("paddypower", t0 + 400)[0]
        r.tick(t0 + 800)
        assert r.guard.spent("paddypower", t0 + 800)[0] == n  # no traffic while cooling off
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


def test_config_change_closes_removed_tabs_and_opens_new_ones(tmp_path, site):
    """A change on the page reaches the running scraper on its next config
    fetch: the removed target's tabs close, the new target opens - no restart -
    and the status report says so."""
    from playwright.sync_api import sync_playwright
    from scraper.runner import CONFIG_EVERY_S
    bb = {"name": "bb", "site": "bookiebashing", "url": site + "/bbpage", "every_min": 10,
          "parser": "bb_saves"}
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [_pp(site)], p, server=site)
        t0 = time.time()
        r.tick(t0)
        r.tick(t0 + 1)
        assert any(k.startswith("pp:m:") for k in r.tabs) and "pp:list" in r.tabs
        first = Handler.reports[-1]["targets"]
        assert [(t["name"], t["state"]) for t in first] == [("pp", "running")]

        Handler.config = {"targets": [bb]}                   # edited on the page
        r.tick(t0 + 60)                                      # not due yet
        assert "pp:list" in r.tabs and "bb" not in r.tabs
        r.tick(t0 + CONFIG_EVERY_S + 5)
        assert not any(k.startswith("pp") for k in r.tabs)   # every pp tab closed
        assert "bb" in r.tabs                                # the new one opened
        assert json.loads((tmp_path / "config.json").read_text()) == Handler.config
        states = {t["name"]: t for t in Handler.reports[-1]["targets"]}
        assert states["pp"]["state"] == "retired" and states["bb"]["state"] == "running"
        assert "parser not written" in states["bb"]["detail"]
        assert {s["site"] for s in Handler.reports[-1]["sites"]} == set(SITES)
        assert Handler.reports[-1]["sites"][0]["loads_hour"] >= 1

        Handler.config = {"targets": []}
        r.tick(t0 + 2 * CONFIG_EVERY_S + 10)
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
                                   "Goalscorer": "not found"}
        labels = {p[4] for p in tab.capture.pinned if "event-page" in p[2]}
        assert labels == {"tab:Corners", "tab:Cards & Fouls"}
        detail = r.status(t0 + 400)["targets"][0]["detail"]
        assert "Corners loaded 1/1" in detail and "Goalscorer not found 1/1" in detail
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
                        alert_webhook=None, healthcheck_url=None, puntmore_url="http://x",
                        ingest_key=None, capture_mb=1, targets=[])
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
