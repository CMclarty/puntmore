"""Signing in per site (login.py): each site whose jobs need a login has one
Form and two names in the Scraper's .env, and the rest is the same machinery
for every site. Team Casino is the second. Stand-in pages, no browser, no
real site, no real login; test_e2e_teamcasino.py drives the real form in
Chrome."""
import time

import pytest

from scraper import browser, config, joblog, login
from scraper.capture import Capture, DiskBudget
from scraper.config import Target
from scraper.sites import SITES
from scraper.tests.test_pacing import _Nothing, _runner

TC_USER, TC_PW = "tc@example.test", "tc-pw-TEST-ONLY"
BB_USER, BB_PW = "bb@example.test", "bb-pw-TEST-ONLY"
TC_URL = "https://www.teamcasino.com/reload-offers-dashboard"
BB_IN = "http://127.0.0.1/tools/saves/"
BB_OUT = "http://127.0.0.1/how-it-works/how-it-works-betbuilder-tool/"
ALL_ENV = ("BB_USERNAME", "BB_PASSWORD", "TC_USERNAME", "TC_PASSWORD", "SCRAPER_IN_CONTAINER")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ALL_ENV:
        monkeypatch.delenv(name, raising=False)


def _tc_creds(monkeypatch):
    monkeypatch.setenv("TC_USERNAME", TC_USER)
    monkeypatch.setenv("TC_PASSWORD", TC_PW)


def _bb_creds(monkeypatch):
    monkeypatch.setenv("BB_USERNAME", BB_USER)
    monkeypatch.setenv("BB_PASSWORD", BB_PW)


# --- the forms and the login per site ---------------------------------------------
def test_credentials_are_read_per_site():
    env = {"BB_USERNAME": BB_USER, "BB_PASSWORD": BB_PW,
           "TC_USERNAME": f" {TC_USER} ", "TC_PASSWORD": f"{TC_PW}\n"}
    assert login.credentials("bookiebashing", env) == (BB_USER, BB_PW)
    assert login.credentials("teamcasino", env) == (TC_USER, TC_PW)
    # one site's login is never another's
    assert login.credentials("teamcasino", {"BB_USERNAME": BB_USER, "BB_PASSWORD": BB_PW}) is None
    assert login.credentials("bookiebashing", {"TC_USERNAME": TC_USER, "TC_PASSWORD": TC_PW}) is None
    assert login.credentials("teamcasino", {"TC_USERNAME": TC_USER}) is None
    assert login.credentials("teamcasino", {"TC_USERNAME": TC_USER, "TC_PASSWORD": " "}) is None
    for site in ("paddypower", "generic"):
        assert login.credentials(site, env) is None


def test_every_site_that_needs_a_login_has_one_form_and_back():
    assert {s for s, rules in SITES.items() if rules.needs_login} == set(login.FORMS)
    assert login.FORMS["teamcasino"].user_env == "TC_USERNAME"
    assert login.FORMS["teamcasino"].pass_env == "TC_PASSWORD"
    assert set(login.env_names()) == {"BB_USERNAME", "BB_PASSWORD", "TC_USERNAME", "TC_PASSWORD"}


def test_chrome_is_not_given_any_sites_login():
    env = {"HOME": "/home/x", "BB_USERNAME": BB_USER, "BB_PASSWORD": BB_PW,
           "TC_USERNAME": TC_USER, "TC_PASSWORD": TC_PW}
    for kw in (browser.launch_options(headless=False, environ=env),
               browser.launch_options(headless=True, environ=env)):
        assert not {"BB_USERNAME", "BB_PASSWORD", "TC_USERNAME", "TC_PASSWORD"} & set(kw["env"])
        assert kw["env"]["HOME"] == "/home/x"


def test_team_casinos_form_is_its_login_form_only():
    """Every part is looked for inside #loginrequest (the reset form beside it
    has the same button class); nothing to open, nothing to tick, no refusal
    text to read (never seen), and the two-factor box is only watched."""
    f = login.FORMS["teamcasino"]
    assert f.form == "form#loginrequest"
    assert (f.user, f.password) == ('input[name="login"]', 'input[name="password"]')
    assert f.submit == 'button.continueBtnTC[type="submit"]'
    assert f.opener is None and f.remember is None and f.error is None
    assert f.password_field == "password"
    assert "2fa" in f.two_factor
    assert "resetrequest" not in repr(f)


# --- the password is never in a url, for every site ----------------------------------
@pytest.mark.parametrize("url", [
    f"https://www.teamcasino.com/reload-offers-dashboard?login=tc%40example.test&password={TC_PW}",
    f"https://www.teamcasino.com/reload-offers-dashboard?password={TC_PW}#x",
    f"http://127.0.0.1/how-it-works/x/?xoo-el-username=u&xoo-el-password={BB_PW}&x=1",
    f"https://example.test/a;password={TC_PW}",
])
def test_a_url_naming_a_password_field_is_cut_and_routed_away(url):
    assert login._names_a_password(url)
    safe = login.safe_url(url)
    assert TC_PW not in safe and BB_PW not in safe and "password=" not in safe


@pytest.mark.parametrize("url", [
    "https://www.teamcasino.com/reload-offers-dashboard",
    "https://www.teamcasino.com/wp-admin/admin-ajax.php",
    "https://www.teamcasino.com/lost-password/?step=2",
    "https://example.test/x?forgot_password=1&reset-password=2",
    "https://www.paddypower.com/football?passwordless=true",
    "https://example.test/password=in/the/path",
])
def test_a_url_with_no_password_field_is_left_alone(url):
    assert not login._names_a_password(url)
    assert login.safe_url(url) == url


# --- the capture never keeps a sign-in --------------------------------------------
class _CapPage:
    def __init__(self):
        self.handlers, self.main_frame = {}, object()

    def on(self, event, fn):
        self.handlers[event] = fn


class _Frame:
    def __init__(self, page):
        self.page = page


class _Req:
    def __init__(self, method, body, rtype):
        self.method, self.post_data, self.resource_type = method, body, rtype


class _Resp:
    def __init__(self, page, url, req, body, ctype="application/json"):
        self.url, self.status, self.request, self._body = url, 200, req, body
        self.frame = _Frame(page)
        self.headers = {"content-type": ctype}

    def text(self):
        return self._body

    def body(self):
        return self._body.encode()


TC_POST = f"login=tc%40example.test&password={TC_PW}&2facode=&remember=forever&action=login_request"
ECHO = '{"success": true, "data": "Welcome tc@example.test"' + ', "pad": "' + "x" * 300 + '"}'


def _cap(tmp_path):
    page, budget = _CapPage(), DiskBudget(10)
    return page, Capture(page, tmp_path / "cap", budget)


@pytest.mark.parametrize("url, rtype", [
    ("https://www.teamcasino.com/wp-admin/admin-ajax.php", "xhr"),      # the site's script
    (TC_URL, "document"),                                               # Chrome's own POST
])
def test_capture_drops_team_casinos_sign_in(tmp_path, url, rtype):
    page, cap = _cap(tmp_path)
    page.handlers["response"](_Resp(page, url, _Req("POST", TC_POST, rtype), ECHO))
    assert cap.since(0) == [] and not cap.pinned
    assert list((tmp_path / "cap").iterdir()) == []


@pytest.mark.parametrize("body", [
    "user_login=tc%40example.test&action=forgot_password_process",      # the reset form
    '{"marketIds": ["1.2"], "note": "passwordless"}',
    "action=other&passwords_seen=0",
])
def test_capture_keeps_a_post_with_no_password_field(tmp_path, body):
    page, cap = _cap(tmp_path)
    url = "https://www.teamcasino.com/wp-admin/admin-ajax.php"
    page.handlers["response"](_Resp(page, url, _Req("POST", body, "xhr"), ECHO))
    assert [r[2] for r in cap.since(0)] == [url]


@pytest.mark.parametrize("body", [
    f"login=tc%40example.test\r\npassword={TC_PW}\r\n2facode=\r\n",     # a text/plain form
    f"login=tc%40example.test\npassword={TC_PW}\n",
    f"xoo-el-username=u\r\nxoo-el-password={BB_PW}\r\n",
])
def test_a_text_plain_form_body_is_a_sign_in(tmp_path, body):
    """enctype="text/plain" puts each field on its own line: a field starts
    after a newline too, as the old substring match found it."""
    assert login.names_a_password(body)
    page, cap = _cap(tmp_path)
    page.handlers["response"](_Resp(page, TC_URL, _Req("POST", body, "document"), ECHO))
    assert cap.since(0) == []
    assert not login.names_a_password("note=forgot\r\nforgot_password=1\r\n")


def test_capture_still_drops_bookie_bashings_sign_in(tmp_path):
    for body in (f"xoo-el-username=u&xoo-el-password={BB_PW}&_xoo_el_form=login",
                 f'------x\r\nContent-Disposition: form-data; name="xoo-el-password"\r\n\r\n{BB_PW}',
                 f'{{"xoo-el-password": "{BB_PW}"}}'):
        page, cap = _cap(tmp_path / str(len(body)))
        page.handlers["response"](_Resp(page, "http://127.0.0.1/wp-admin/admin-ajax.php",
                                        _Req("POST", body, "xhr"), ECHO))
        assert cap.since(0) == [], body


# --- hosts ---------------------------------------------------------------------------
def test_team_casino_hosts():
    for url in (TC_URL, "https://teamcasino.com/x"):
        assert config.host_allowed(url, "teamcasino")
        assert not config.host_allowed(url, "generic")          # never under the generic budget
        assert not config.host_allowed(url, "paddypower")
        assert not config.host_allowed(url, "bookiebashing")
        assert config.host_allowed(url)                         # the capture's "a site's own host"
    for url in ("https://www.paddypower.com/x", "https://www.bookiebashing.net/x",
                "https://tips.example.com/x", "https://teamcasino.com.evil.test/x"):
        assert not config.host_allowed(url, "teamcasino"), url
    assert config.host_allowed("http://127.0.0.1:8000/reload-offers-dashboard", "teamcasino")
    # unchanged for the others
    assert config.host_allowed("https://www.paddypower.com/football", "paddypower")
    assert config.host_allowed("https://www.bookiebashing.net/tools/", "bookiebashing")
    assert config.host_allowed("https://tips.example.com/today", "generic")


def test_a_team_casino_target_keeps_its_page_and_a_generic_one_cannot_name_it():
    t = config._target({"name": "tc", "site": "teamcasino", "url": TC_URL})
    assert t.mode == "keep" and t.parser is None
    with pytest.raises(ValueError, match="not on an allowed host for site 'generic'"):
        config._target({"name": "g", "site": "generic", "url": TC_URL})
    with pytest.raises(ValueError, match="not on an allowed host for site 'teamcasino'"):
        config._target({"name": "tc", "site": "teamcasino", "url": "https://tips.example.com/x"})


def test_team_casino_is_a_site_with_its_own_limits():
    from scraper import behaviour, look
    rules = SITES["teamcasino"]
    assert rules.needs_login and not rules.block_means_cooloff   # as Bookie Bashing: a back-off
    assert rules.logged_out == "form#loginrequest:visible, form#resetrequest:visible"
    assert (behaviour.get("teamcasino.per_hour"), behaviour.get("teamcasino.per_day"),
            behaviour.get("teamcasino.quiet_hours")) == (6, 30, False)
    assert look.refused("teamcasino") is None                 # its pages are learned with look
    from scraper.guard import from_behaviour
    assert from_behaviour()["limits"]["teamcasino"] == (6, 30)


# --- logged out, per site -------------------------------------------------------------
class _Shown:
    def __init__(self, n):
        self.n = n

    @property
    def first(self):
        return self

    def count(self):
        return self.n

    def is_visible(self):
        return self.n > 0


def _parts(sel):
    """A selector list's parts, each without its `:visible` (a stand-in shows
    only what is shown, so it means nothing here)."""
    return {p.strip().removesuffix(":visible") for p in sel.split(",")}


class _Seen(_Nothing):
    """A page that shows exactly the selectors in `shown`."""
    def __init__(self, url, shown=()):
        self.url, self.shown = url, set(shown)

    def locator(self, sel):
        if sel == PW_FIELD:
            return _Shown(int(sel in self.shown))
        return _Shown(len(_parts(sel) & self.shown))


PW_FIELD = "input[type=password]:visible"


def test_team_casino_is_logged_out_only_while_its_form_shows():
    out = _Seen(TC_URL, {"form#loginrequest", PW_FIELD})
    assert browser.logged_out(out, TC_URL, "teamcasino")
    # "Forgot Password?" hides the login form and shows the reset form: still out
    assert browser.logged_out(_Seen(TC_URL, {"form#resetrequest"}), TC_URL, "teamcasino")
    # logged in: a password field elsewhere on the page (a "change password"
    # box, say) is not a logout here, and the url never says anything
    assert not browser.logged_out(_Seen(TC_URL, {PW_FIELD}), TC_URL, "teamcasino")
    assert not browser.logged_out(_Seen(TC_URL + "/how-it-works/login"), TC_URL, "teamcasino")


def test_bookie_bashing_logged_out_is_unchanged():
    assert browser.logged_out(_Seen(BB_IN, {PW_FIELD}), BB_IN, "bookiebashing")
    assert browser.logged_out(_Seen(BB_OUT), BB_IN, "bookiebashing")
    assert not browser.logged_out(_Seen(BB_IN), BB_IN, "bookiebashing")
    # Team Casino's marker means nothing on Bookie Bashing
    assert not browser.logged_out(_Seen(BB_IN, {"form#loginrequest"}), BB_IN, "bookiebashing")
    # and the old call, with no site, is the old rule
    assert browser.logged_out(_Seen(BB_OUT), BB_IN)
    assert browser.logged_out(_Seen(BB_IN, {PW_FIELD}))


# --- the runner, two sites with logins --------------------------------------------------
class _World:
    """Each site's side: signed in or not, and whether it takes the login.
    `reset_view`: Team Casino's page shows its reset form, not the login form."""
    def __init__(self, tc_accept=True, bb_accept=True, tc_in=False, bb_in=False):
        self.accept = {"teamcasino": tc_accept, "bookiebashing": bb_accept}
        self.signed_in = {"teamcasino": tc_in, "bookiebashing": bb_in}
        self.reset_view = False


class _Page(_Nothing):
    """Team Casino's members page keeps its url and shows its form while
    logged out; Bookie Bashing's logged-out tool lands on /how-it-works/."""
    def __init__(self, world):
        self.world, self.wanted, self.handlers = world, None, {}

    def on(self, event, fn):
        self.handlers[event] = fn

    @property
    def site(self):
        return "teamcasino" if "teamcasino" in (self.wanted or "") else "bookiebashing"

    @property
    def url(self):
        if self.site == "bookiebashing" and not self.world.signed_in["bookiebashing"]:
            return BB_OUT
        return self.wanted or "about:blank"

    def goto(self, url, **k):
        self.wanted = url
        return _Nothing()

    def locator(self, sel):
        out = not self.world.signed_in[self.site]
        if self.site == "teamcasino":
            shown = {"form#resetrequest" if self.world.reset_view else "form#loginrequest"}
            return _Shown(len(_parts(sel) & shown) if out else 0)
        return _Shown(0)                    # Bookie Bashing: its logout is the url


class _Ctx:
    def __init__(self, world):
        self.world, self.pages = world, []

    def new_page(self):
        page = _Page(self.world)
        self.pages.append(page)
        return page


TC = Target(name="tc-offers", site="teamcasino", url=TC_URL, every_min=10, stale_min=10_000)
BB = Target(name="bb-saves", site="bookiebashing", url=BB_IN, every_min=10, stale_min=10_000)


def _sites_runner(tmp_path, world, targets):
    tmp_path.mkdir(parents=True, exist_ok=True)
    r = _runner(tmp_path, list(targets))
    r.ctx = _Ctx(world)
    return r


def _attempts(monkeypatch, world):
    """login.attempt as the site would answer it: a sign-in it accepts leaves
    the page logged in (Team Casino reloads in place; Bookie Bashing navigates)."""
    calls = []

    def attempt(page, site, creds, timeout_s=20.0):
        calls.append((site, creds))
        if world.accept[site]:
            world.signed_in[site] = True
        return None
    monkeypatch.setattr(login, "attempt", attempt)
    return calls


def _alerts(tmp_path):
    p = tmp_path / "alerts.log"
    return [line.split("  ", 1)[1] for line in p.read_text().splitlines()] if p.exists() else []


def _said(tmp_path, name):
    return [line["msg"] for line in joblog.read(tmp_path, name)]


def _until_loaded(r, t, site, n):
    """Tick 15 s apart until `site` has n loads; the time after."""
    for _ in range(200):
        if r.guard.spent(site, t)[0] >= n:
            return t
        r.tick(t)
        t += 15
    raise AssertionError(f"{site}: {r.guard.spent(site, t)[0]} loads, wanted {n}")


def test_team_casino_signs_in_with_its_own_login_then_runs(tmp_path, monkeypatch):
    _tc_creds(monkeypatch)
    world = _World()
    calls = _attempts(monkeypatch, world)
    r = _sites_runner(tmp_path, world, [TC])
    t0 = time.time()
    r.tick(t0)
    assert calls == [("teamcasino", (TC_USER, TC_PW))]
    assert "teamcasino" in r.login_pending
    # the load, and the page's own navigation after the form, both counted
    assert r.guard.spent("teamcasino", t0)[0] == 2
    _until_loaded(r, t0 + 200, "teamcasino", 3)                # the members url again, through the guard
    assert "teamcasino" not in r.login_pending
    assert _said(tmp_path, "tc-offers")[:2] == ["logged out - signing in", "signed in"]
    assert _alerts(tmp_path) == [] and login.failed(tmp_path, "teamcasino") is None
    assert len(login.sent(tmp_path, "teamcasino", time.time())) == 1


FAILED_ALERT = (f"teamcasino: logged out on {TC_URL} - signing in failed (still logged out after "
                "signing in). Not trying again until you log in on the Hub's screen or run "
                "`resume teamcasino`. Backing off.")


def _failed_once(tmp_path, monkeypatch):
    """Logged out, the form sent, and the next read still logged out."""
    _tc_creds(monkeypatch)
    world = _World(tc_accept=False)
    calls = _attempts(monkeypatch, world)
    r = _sites_runner(tmp_path, world, [TC])
    t0 = time.time()
    r.tick(t0)
    t = _until_loaded(r, t0 + 200, "teamcasino", 3)
    return r, world, calls, t


def test_team_casino_still_logged_out_is_a_failed_sign_in_that_waits(tmp_path, monkeypatch):
    """As Bookie Bashing's: the form sent and the page still showing it is a
    failed sign-in - noted, one alert, a back-off with the tab left open, no
    second sign-in - until `resume teamcasino`, which gives one more try at
    once (no cool-off, no hours to serve)."""
    r, world, calls, t = _failed_once(tmp_path, monkeypatch)
    assert calls == [("teamcasino", (TC_USER, TC_PW))]
    assert login.failed(tmp_path, "teamcasino")["why"] == "still logged out after signing in"
    st = r.sites["teamcasino"]
    assert st.blocked_for == "logged out" and st.retry_at > t
    assert r.guard.cooloff("teamcasino") is None             # never a cool-off
    assert "tc-offers" in r.tabs                             # left open to sort on the screen
    assert _alerts(tmp_path) == [FAILED_ALERT]
    assert _said(tmp_path, "tc-offers")[-2:] == [
        "signing in failed (still logged out after signing in)", "blocked: logged out - backing off"]
    loads = r.guard.db.execute("SELECT COUNT(*) FROM loads").fetchone()[0]
    for h in range(1, 6):                                    # past the back-offs: read again, still out
        r.tick(t + 3600 * h)
    assert r.guard.db.execute("SELECT COUNT(*) FROM loads").fetchone()[0] > loads
    assert calls == [("teamcasino", (TC_USER, TC_PW))]
    assert _alerts(tmp_path) == [FAILED_ALERT]
    from scraper.tests.test_login import _resume
    _resume(tmp_path, monkeypatch, "teamcasino")
    assert login.failed(tmp_path, "teamcasino") is None
    world.accept["teamcasino"] = True
    t += 3600 * 5 + 15
    assert r.sites["teamcasino"].retry_at > t                # still backing off: sent on the open page
    r.tick(t)
    assert len(calls) == 2                                   # one more try, at once
    r.tick(t + 15)                                           # the open page now reads logged in
    assert world.signed_in["teamcasino"] and r.sites["teamcasino"].blocked_for is None
    assert _said(tmp_path, "tc-offers")[-2:] == ["logged out - signing in", "signed in"]


def test_logged_in_by_hand_clears_the_failure_but_the_reset_form_does_not(tmp_path, monkeypatch):
    """After a failed sign-in a person sorts it on the Scraper's screen. Their
    "Forgot Password?" click (the reset form shown, the login form hidden) is
    still logged out: the note stays. Logged in, it is forgotten."""
    r, world, calls, t = _failed_once(tmp_path, monkeypatch)
    world.reset_view = True
    for i in range(1, 5):
        r.tick(t + 15 * i)
    assert login.failed(tmp_path, "teamcasino") is not None
    assert r.sites["teamcasino"].blocked_for == "logged out"
    world.reset_view, world.signed_in["teamcasino"] = False, True
    r.tick(t + 90)
    assert login.failed(tmp_path, "teamcasino") is None
    assert r.sites["teamcasino"].blocked_for is None
    assert _alerts(tmp_path)[-1] == "teamcasino: clear again (logged out sorted)."
    assert calls == [("teamcasino", (TC_USER, TC_PW))]


def test_team_casino_with_no_login_in_env_is_a_logout_that_waits(tmp_path, monkeypatch):
    """As Bookie Bashing's with no login: one alert, a back-off, the tab left
    open, no sign-in; another site's login is no use here."""
    _bb_creds(monkeypatch)
    world = _World()
    calls = _attempts(monkeypatch, world)
    r = _sites_runner(tmp_path, world, [TC])
    t0 = time.time()
    for i in range(40):
        r.tick(t0 + 15 * i)
    for h in range(1, 4):
        r.tick(t0 + 3600 * h)
    assert calls == []
    assert r.guard.cooloff("teamcasino") is None
    assert "tc-offers" in r.tabs
    assert r.sites["teamcasino"].blocked_for == "logged out"
    assert _alerts(tmp_path) == [f"teamcasino: logged out on {TC_URL} - needs you on the "
                                 "Hub's screen. Backing off."]
    assert _said(tmp_path, "tc-offers") == ["blocked: logged out - backing off"]


def test_a_failed_sign_in_on_one_site_does_not_stop_the_other(tmp_path, monkeypatch):
    _tc_creds(monkeypatch)
    _bb_creds(monkeypatch)
    world = _World(tc_accept=True, bb_accept=False)
    calls = _attempts(monkeypatch, world)
    r = _sites_runner(tmp_path, world, [BB, TC])
    t = time.time()
    for _ in range(80):
        r.tick(t)
        t += 15
    assert [c[0] for c in calls].count("bookiebashing") == 1
    assert [c[0] for c in calls].count("teamcasino") == 1
    assert login.failed(tmp_path, "bookiebashing")["why"] == "still logged out after signing in"
    assert login.failed(tmp_path, "teamcasino") is None
    assert world.signed_in["teamcasino"] and "signed in" in _said(tmp_path, "tc-offers")
    assert r.guard.cooloff("teamcasino") is None
    # and the other way round
    world2 = _World(tc_accept=False, bb_accept=True)
    calls2 = _attempts(monkeypatch, world2)
    r = _sites_runner(tmp_path / "b", world2, [BB, TC])
    t = time.time()
    for _ in range(80):
        r.tick(t)
        t += 15
    assert sorted(c[0] for c in calls2) == ["bookiebashing", "teamcasino"]
    assert login.failed(tmp_path / "b", "teamcasino")["why"] == "still logged out after signing in"
    assert login.failed(tmp_path / "b", "bookiebashing") is None
    assert "signed in" in _said(tmp_path / "b", "bb-saves")


def test_the_sign_in_cap_is_counted_per_site(tmp_path, monkeypatch):
    """login.max_signins in login.window_h, each site on its own: Bookie
    Bashing at its cap does not stop Team Casino signing in, and the reverse."""
    _tc_creds(monkeypatch)
    _bb_creds(monkeypatch)
    now = time.time()
    for i in range(3):
        login.record_sent(tmp_path, "bookiebashing", now - 60 * (i + 1))
    world = _World()
    calls = _attempts(monkeypatch, world)
    r = _sites_runner(tmp_path, world, [BB, TC])
    t = now
    for _ in range(40):
        r.tick(t)
        t += 15
    assert [c[0] for c in calls] == ["teamcasino"]
    assert login.failed(tmp_path, "bookiebashing")["why"] == "signed out again after 3 sign-ins in 6 h"
    assert login.failed(tmp_path, "teamcasino") is None
    assert len(login.sent(tmp_path, "bookiebashing", t)) == 3
    assert len(login.sent(tmp_path, "teamcasino", t)) == 1
    # the reverse
    d = tmp_path / "rev"
    d.mkdir()
    for i in range(3):
        login.record_sent(d, "teamcasino", now - 60 * (i + 1))
    world = _World()
    calls = _attempts(monkeypatch, world)
    r = _sites_runner(d, world, [BB, TC])
    t = now
    for _ in range(40):
        r.tick(t)
        t += 15
    assert [c[0] for c in calls] == ["bookiebashing"]
    assert login.failed(d, "teamcasino")["why"] == "signed out again after 3 sign-ins in 6 h"
    assert login.failed(d, "bookiebashing") is None


def test_resume_says_a_failed_sign_in_is_forgotten_for_team_casino(tmp_path, monkeypatch, capsys):
    from scraper.tests.test_login import _resume
    login.mark_failed(tmp_path, "teamcasino", "still logged out after signing in", 1.0)
    login.mark_failed(tmp_path, "bookiebashing", "refused: x", 1.0)
    _resume(tmp_path, monkeypatch, "teamcasino")
    assert "A failed sign-in is forgotten" in capsys.readouterr().out
    assert login.failed(tmp_path, "teamcasino") is None
    assert login.failed(tmp_path, "bookiebashing") is not None   # one site's resume only
