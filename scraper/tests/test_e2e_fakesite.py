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

MATCH = """<html><title>Match</title><body>
<button role="tab">Cards</button><button role="tab">Corners</button>
<div id=p></div><script>
async function poll(){ const r = await fetch('/api/prices?m='+location.pathname);
  document.getElementById('p').textContent = (await r.json()).prices.join(','); }
poll(); setInterval(poll, 1000);
</script></body></html>"""

LOGIN = "<html><title>Sign in</title><body><form><input type=password></form></body></html>"


class Handler(http.server.BaseHTTPRequestHandler):
    blocked_path = None

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
        if path.startswith("/api/prices"):
            return self._send(200, json.dumps({"prices": [1.91] * 40, "pad": "x" * 300}),
                              "application/json")
        if path == "/bb":
            return self._send(200, LOGIN)
        self._send(404, "no")


@pytest.fixture
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    Handler.blocked_path = None
    srv.shutdown()


def _runner(tmp_path, targets, p):
    from scraper import browser
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, puntmore_url="http://x",
                        ingest_key=None, capture_mb=20, targets=targets)
    guard = Guard(tmp_path / "g.sqlite3", {n: (r.per_hour, r.per_day) for n, r in SITES.items()})
    guard.quiet = (0, 0)
    ctx = browser.launch(p, s, headless=True)
    return Runner(s, ctx, guard, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples"), ctx


def _pp(base):
    # Ticks are at fake times far ahead of the page's real activity, so a long
    # stale_min keeps the open tabs from looking quiet.
    return config.Target(name="pp", site="paddypower", url=base + "/football", parser="pp_match",
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
        assert samples and '"prices"' in samples[0].read_text()
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
