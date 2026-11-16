"""Team Casino's sign-in in a real Chrome, against its real logged-out page
(tests/fixtures/teamcasino/logged_out.html, cut from the page saved on 13 Nov
2026) served from localhost. Nothing here loads teamcasino.com.

Needs Playwright and a Chromium. Set SCRAPER_CHROME_PATH if Playwright's own
browser isn't installed.
"""
import dataclasses
import http.server
import json
import os
import pathlib
import re
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright")

from scraper import browser, config, login  # noqa: E402
from scraper.alerts import Alerts  # noqa: E402
from scraper.guard import Guard  # noqa: E402
from scraper.runner import Runner  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "teamcasino" / "logged_out.html"
USER, PASS = "tc@example.test", "tc-pw-TEST-ONLY"
TWO_FACTOR_USER = "2fa@example.test"
TWO_FACTOR_ASKED = "Please enter the Two-Factor Authentication code."
MEMBERS = "/reload-offers-dashboard"

OUT_PAGE = FIXTURE.read_text()
# The page whose script never ran: Chrome sends the form itself, a POST to the
# page's own url (action=""), the password in the body.
UNBOUND = re.sub(r"<!-- STAND-IN.*?</script>", "", OUT_PAGE, flags=re.S)
assert "addEventListener('submit'" not in UNBOUND and 'method="POST"' in UNBOUND
# The same with the form's method GET: Chrome would put the password in the url.
AS_GET = UNBOUND.replace('<form id="loginrequest" method="POST"', '<form id="loginrequest" method="GET"')
assert AS_GET != UNBOUND

# A stand-in members page once logged in (the real one, seen on 13 Nov 2026, is
# parsers/tc_reload.py's fixture, fixtures/teamcasino/dashboard.html): no login
# form and, to be sure it is not taken for one, a "change password" box with a
# visible password field.
IN_PAGE = """<html><head><title>Reload Offers Dashboard - Team Casino</title></head><body>
<h1>Reload offers</h1><table><tr><td>Offer A</td><td>£10</td></tr></table>
<form id="changepassword" method="POST"><input type="password" name="new_password">
<button type="submit">Change</button></form></body></html>"""


# The signed-in dashboard for a fetch job (parsers/tc_reload.py's fixture, cut
# from the real page), every src= taken out so Chrome asks nothing of
# teamcasino.com; and the same offers drawn in LATE_MS after the page arrives,
# as the real page shows "Searching for most profitable offers.." first.
DASHBOARD = re.sub(r'\ssrc="[^"]*"', "", (FIXTURE.parent / "dashboard.html").read_text(encoding="utf-8"))
_MAIN = '<div id="offers-main-wrap" style="display: block;">'
_OFFERS = DASHBOARD[DASHBOARD.index(_MAIN) + len(_MAIN):DASHBOARD.rindex("</div>\n</body>")]
assert "</script" not in _OFFERS and "data-offer-id" in _OFFERS
LATE_MS = 2500
LATE = f"""<html><head><title>Reload Offers Dashboard - Team Casino</title></head><body>
<div id="offers-loading-icon" style=""><p>Searching for most profitable offers..</p></div>
<div id="offers-main-wrap" style="display: none;"></div>
<script>setTimeout(function () {{
  var main = document.getElementById('offers-main-wrap');
  main.innerHTML = {json.dumps(_OFFERS)};
  main.style.display = 'block';
  document.getElementById('offers-loading-icon').style.display = 'none';
}}, {LATE_MS});</script></body></html>"""
WAIT_FOR = "#offers-main-wrap table[data-offer-id]"


class Handler(http.server.BaseHTTPRequestHandler):
    seen: list = []                      # (method, path, body)
    signed_in = False                    # the site's side: this browser's session

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html", cookie=None):
        b = body.encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b)))
        if cookie:
            self.send_header("set-cookie", cookie)
        self.end_headers()
        self.wfile.write(b)

    def _in(self):
        return "tc_session=in" in (self.headers.get("cookie") or "")

    def do_GET(self):
        Handler.seen.append(("GET", self.path, ""))
        path = urlparse(self.path).path
        if path == MEMBERS:
            return self._send(200, IN_PAGE if self._in() else OUT_PAGE)
        if path == "/unbound" + MEMBERS:
            return self._send(200, IN_PAGE if self._in() else UNBOUND)
        if path == "/get" + MEMBERS:
            return self._send(200, AS_GET)
        if path == "/in":
            return self._send(200, IN_PAGE)
        if path == "/offers" + MEMBERS:
            return self._send(200, DASHBOARD if self._in() else OUT_PAGE)
        if path == "/late" + MEMBERS:
            return self._send(200, LATE)
        self._send(404, "no")

    def _login(self, form):
        if (form.get("login"), form.get("password"), form.get("remember"),
                form.get("action")) == (USER, PASS, "forever", "login_request") and not form.get("2facode"):
            return True, "Login successful, redirecting..."
        if form.get("login") == TWO_FACTOR_USER:
            return False, TWO_FACTOR_ASKED
        return False, "Wrong details."

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
        Handler.seen.append(("POST", self.path, body))
        form = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
        ok, said = self._login(form)
        cookie = "tc_session=in; Path=/" if ok else None
        if self.path == "/wp-admin/admin-ajax.php":                  # the site's script
            return self._send(200, json.dumps({"success": ok, "data": said}), "application/json",
                              cookie)
        if urlparse(self.path).path == "/unbound" + MEMBERS:         # Chrome's own POST
            return self._send(200, IN_PAGE if ok else UNBOUND, cookie=cookie)
        self._send(404, "no")


@pytest.fixture
def site():
    Handler.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _headless() -> bool:
    """Headless unless SCRAPER_TEST_HEADED=1 (the proof on the Scraper's own
    screen; DISPLAY must be that screen's, never the desktop's)."""
    return os.environ.get("SCRAPER_TEST_HEADED") != "1"


def _ctx(p, tmp_path):
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=20, targets=[])
    return browser.launch(p, s, headless=_headless())


def _posts():
    return [(path, body) for method, path, body in Handler.seen if method == "POST"]


def _fields(body):
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


def test_the_real_page_reads_as_logged_out_and_a_members_page_does_not(tmp_path, site):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _ctx(p, tmp_path)
        page = ctx.new_page()
        page.goto(site + MEMBERS)
        assert page.get_by_text("Please Log In To View This Page").is_visible()
        assert browser.logged_out(page, site + MEMBERS, "teamcasino")
        page.goto(site + "/in")
        assert page.locator("input[type=password]:visible").count() == 1
        assert not browser.logged_out(page, site + MEMBERS, "teamcasino")
        ctx.close()


def test_the_reset_form_in_place_of_the_login_form_is_still_logged_out(tmp_path, site):
    """The page's "Forgot Password?" (resetBtn(): .login-wrap hidden, the reset
    form's wrap shown) leaves #loginrequest in the page but hidden. That is
    still logged out - read as logged in, it would clear a failed sign-in's
    note. Neither form shown (the page never does this itself) reads as
    logged in: the mark is a form shown, not a form present."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _ctx(p, tmp_path)
        page = ctx.new_page()
        page.goto(site + MEMBERS)
        page.evaluate("""() => {
            document.querySelector('.login-wrap').style.display = 'none';
            document.querySelector('.forgot-your-pass-wrap').style.display = 'block'; }""")
        assert not page.locator("form#loginrequest").is_visible()
        assert page.locator("form#loginrequest").count() == 1
        assert page.locator("form#resetrequest").is_visible()
        assert browser.logged_out(page, site + MEMBERS, "teamcasino")
        page.evaluate("() => { document.querySelector('.forgot-your-pass-wrap').style.display = 'none'; }")
        assert not browser.logged_out(page, site + MEMBERS, "teamcasino")
        ctx.close()


def test_sign_in_sends_the_login_form_once_and_nothing_else(tmp_path, site):
    """One POST, from #loginrequest only: the email and password typed, the
    two-factor box left empty, the hidden remember/action as the page has
    them; the reset form beside it never touched. The page reloads logged in."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _ctx(p, tmp_path)
        page = ctx.new_page()
        page.goto(site + MEMBERS)
        Handler.seen = []
        assert login.attempt(page, "teamcasino", (USER, PASS), timeout_s=15) is None
        page.wait_for_load_state()
        assert not browser.logged_out(page, site + MEMBERS, "teamcasino")
        assert page.get_by_text("Reload offers").is_visible()
        posts = _posts()
        assert len(posts) == 1
        path, body = posts[0]
        assert path == "/wp-admin/admin-ajax.php"
        assert _fields(body) == {"login": USER, "password": PASS, "2facode": "",
                                 "remember": "forever", "action": "login_request"}
        assert not any("forgot_password" in b or "user_login" in b for _m, _p, b in Handler.seen)
        assert not any(PASS in path or "password=" in path for _m, path, _b in Handler.seen)
        ctx.close()


def test_a_login_the_site_does_not_take_leaves_the_page_logged_out(tmp_path, site):
    """Its refusal text is not known, so none is read: the form was sent, the
    page still shows it, and the next read says the sign-in did not take."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _ctx(p, tmp_path)
        page = ctx.new_page()
        page.goto(site + MEMBERS)
        why = login.attempt(page, "teamcasino", (USER, "not-the-password"), timeout_s=5)
        assert why is None
        assert browser.logged_out(page, site + MEMBERS, "teamcasino")
        assert len(_posts()) == 1
        ctx.close()


def test_a_two_factor_code_is_never_typed(tmp_path, site):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _ctx(p, tmp_path)
        page = ctx.new_page()
        page.goto(site + MEMBERS)
        t0 = time.monotonic()
        why = login.attempt(page, "teamcasino", (TWO_FACTOR_USER, PASS), timeout_s=15)
        assert why == login.TWO_FACTOR
        assert time.monotonic() - t0 < 10                   # said at once, not at the deadline
        assert page.locator('#loginrequest [name="2facode"]').input_value() == ""
        assert len(_posts()) == 1 and _fields(_posts()[0][1])["2facode"] == ""
        ctx.close()


def test_a_form_chrome_sends_itself_as_a_post_is_allowed(tmp_path, site):
    """The page's script never ran: Chrome posts the form to the page's own
    url, the password in the body. That is how the form is built, so it goes;
    no url ever carries the password."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _ctx(p, tmp_path)
        page = ctx.new_page()
        page.goto(site + "/unbound" + MEMBERS)
        assert login.attempt(page, "teamcasino", (USER, PASS), timeout_s=15) is None
        page.wait_for_load_state()
        posts = _posts()
        assert [path for path, _b in posts] == ["/unbound" + MEMBERS]
        assert _fields(posts[0][1])["password"] == PASS
        assert not browser.logged_out(page, site + "/unbound" + MEMBERS, "teamcasino")
        assert PASS not in page.url and "password" not in page.url
        ctx.close()


def test_a_form_that_would_put_the_password_in_the_url_is_stopped(tmp_path, site):
    """For every site: a request naming a password field in its url is
    aborted inside Chrome; the server never sees it."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _ctx(p, tmp_path)
        page = ctx.new_page()
        page.goto(site + "/get" + MEMBERS)
        Handler.seen = []
        why = login.attempt(page, "teamcasino", (USER, PASS), timeout_s=10)
        assert why == login.NATIVE
        assert PASS not in page.url and "password=" not in page.url
        page.goto(site + "/in")                                # nothing left routed
        assert not any(PASS in path or "password=" in path for _m, path, _b in Handler.seen)
        ctx.close()


def _runner(tmp_path, targets, p):
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=20, targets=targets)
    guard = Guard(tmp_path / "g.sqlite3")
    guard.quiet = (0, 0)
    ctx = browser.launch(p, s, headless=_headless())
    return Runner(s, ctx, guard, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples"), ctx


def _files_with(root, needle: bytes):
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if "profile" in dirpath:                          # Chrome's own profile: not ours
                continue
            with open(os.path.join(dirpath, name), "rb") as f:
                if needle in f.read():
                    found.append(os.path.join(dirpath, name))
    return found


def test_the_runner_signs_in_to_team_casino_and_keeps_the_page(tmp_path, site, monkeypatch):
    """A Team Casino job with no parser: found logged out, signed in on the
    open page (the reload after it counted as a load), the members url
    loaded again through the guard and kept open. Nothing written anywhere
    holds the password or the login."""
    from playwright.sync_api import sync_playwright
    monkeypatch.setenv("TC_USERNAME", USER)
    monkeypatch.setenv("TC_PASSWORD", PASS)
    tc = config.Target(name="tc-offers", site="teamcasino", url=site + MEMBERS, every_min=60,
                       stale_min=600)
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [tc], p)
        t0 = time.time()
        r.tick(t0)
        assert "teamcasino" in r.login_pending
        assert r.guard.spent("teamcasino", t0)[0] == 2         # the load and the page's own reload
        r.tick(t0 + 200)
        assert r.guard.spent("teamcasino", t0 + 200)[0] == 3
        assert "teamcasino" not in r.login_pending
        assert "tc-offers" in r.tabs and not browser.logged_out(r.tabs["tc-offers"].page,
                                                                site + MEMBERS, "teamcasino")
        assert login.failed(tmp_path, "teamcasino") is None
        assert not (tmp_path / "alerts.log").exists()
        assert len([1 for path, _b in _posts() if path == "/wp-admin/admin-ajax.php"]) == 1
        r.close_all()
        ctx.close()
    assert _files_with(tmp_path, PASS.encode()) == []
    assert _files_with(tmp_path, USER.encode()) == []


def test_the_runner_waits_for_a_person_after_a_sign_in_that_did_not_take(tmp_path, site, monkeypatch):
    from playwright.sync_api import sync_playwright
    monkeypatch.setenv("TC_USERNAME", USER)
    monkeypatch.setenv("TC_PASSWORD", "not-the-password")
    tc = config.Target(name="tc-offers", site="teamcasino", url=site + MEMBERS, every_min=60,
                       stale_min=600)
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [tc], p)
        t0 = time.time()
        r.tick(t0)
        r.tick(t0 + 200)
        assert login.failed(tmp_path, "teamcasino")["why"] == "still logged out after signing in"
        assert r.guard.cooloff("teamcasino") is None          # a back-off, as Bookie Bashing's
        assert r.sites["teamcasino"].blocked_for == "logged out"
        assert "tc-offers" in r.tabs                          # left open to sort on the screen
        r.tick(t0 + 400)
        assert len(_posts()) == 1                             # one sign-in, no second
        alerts = (tmp_path / "alerts.log").read_text()
        assert "signing in failed (still logged out after signing in)" in alerts
        assert "`resume teamcasino`. Backing off." in alerts
        r.close_all()
        ctx.close()
    assert _files_with(tmp_path, b"not-the-password") == []


@pytest.fixture
def quick_arrival(tmp_path):
    """No wait or look-down on arrival (behaviour.toml in the job's data
    dir), so a page is read as soon as it is loaded; the defaults after."""
    from scraper import behaviour
    for key in ("arrive.wait_ms", "arrive.passes", "arrive.pause_ms"):
        behaviour.set_(tmp_path, key, "0-0")
    yield
    behaviour.load(tmp_path / "no-overrides")


def _only_the_test_site(ctx, site):
    """Belt and braces: anything not for the test's own server is refused."""
    ctx.route("**/*", lambda route: route.fallback() if route.request.url.startswith(site)
              else route.abort())


def _fetch_job(site, path, **kw):
    return config.Target(name="tc-fetch", site="teamcasino", url=site + path + MEMBERS, mode="fetch",
                         parser="tc_reload", every_min=100, **kw)


def test_a_fetch_job_found_logged_out_signs_in_and_reads_on_the_next_tick(tmp_path, site, monkeypatch,
                                                                         quick_arrival):
    """A fetch load that finds Team Casino logged out signs in on that page
    (login.py: one attempt, counted in login.json, capped), is due again at
    once, and its next load, through the guard and the spacing, reads the
    offers and closes the page: 3 loads (the visit, the sign-in's own
    navigation, the visit again)."""
    from playwright.sync_api import sync_playwright
    monkeypatch.setenv("TC_USERNAME", USER)
    monkeypatch.setenv("TC_PASSWORD", PASS)
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [_fetch_job(site, "/offers", wait_for=WAIT_FOR)], p)
        _only_the_test_site(ctx, site)
        t0 = time.time()
        r.tick(t0)
        assert "teamcasino" in r.login_pending
        assert r.guard.spent("teamcasino", t0)[0] == 2
        assert len(login.sent(tmp_path, "teamcasino", t0)) == 1
        r.tick(t0 + 200)
        assert r.guard.spent("teamcasino", t0 + 200)[0] == 3
        assert "teamcasino" not in r.login_pending and r.tabs == {}
        st = r.status(t0 + 200)["targets"][0]
        assert "12 rows read, sent nowhere (no post)" in st["detail"]
        assert len(json.loads((tmp_path / "latest" / "tc-fetch.json").read_text())["rows"]) == 12
        r.tick(t0 + 400)
        assert r.guard.spent("teamcasino", t0 + 400)[0] == 3       # not due again for 75 min+
        assert len([1 for path, _b in _posts() if path == "/wp-admin/admin-ajax.php"]) == 1
        r.close_all()
        ctx.close()
    assert _files_with(tmp_path, PASS.encode()) == []


def test_a_fetch_job_waits_for_the_offers_with_wait_for(tmp_path, site, quick_arrival):
    """The page shows "Searching for most profitable offers.." and draws the
    offers in later: with wait_for the read waits for them; without it the
    read is the loading notice, no rows."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        r, ctx = _runner(tmp_path, [_fetch_job(site, "/late", wait_for=WAIT_FOR)], p)
        _only_the_test_site(ctx, site)
        t0 = time.time()
        r.tick(t0)
        assert r.tabs == {}
        assert len(json.loads((tmp_path / "latest" / "tc-fetch.json").read_text())["rows"]) == 12
        bare = dataclasses.replace(_fetch_job(site, "/late"), name="tc-bare",
                                   url=site + "/late" + MEMBERS + "?bare")      # no wait_for
        r.apply_targets([bare])
        r.tick(t0 + 200)
        assert r.tabs == {}
        assert json.loads((tmp_path / "latest" / "tc-bare.json").read_text())["rows"] == []
        assert r.read_said["tc-bare"] == "still loading: Searching for most profitable offers.."
        r.close_all()
        ctx.close()
