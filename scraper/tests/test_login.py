"""Signing in to Bookie Bashing (login.py) and the runner's use of it, with
stand-in pages: no browser, no real site, no real login."""
import json
import os
import time

import pytest

from scraper import joblog, login
from scraper.capture import Capture, DiskBudget
from scraper.config import Target
from scraper.tests.test_pacing import _loads, _Nothing, _runner

USER, PW = "u@example.test", "pw-TEST-ONLY"
F = login.FORMS["bookiebashing"]
OUT = "http://127.0.0.1/how-it-works/how-it-works-betbuilder-tool/"   # where a logged-out tool lands
IN = "http://127.0.0.1/tools/saves/"
AJAX = "http://127.0.0.1/wp-admin/admin-ajax.php"
SIGN_IN_POST = f"xoo-el-username=u%40example.test&xoo-el-password={PW}&_xoo_el_form=login"
ECHO = json.dumps({"error": 0, "notice": f"Welcome {USER}, you typed {PW}", "pad": "x" * 300})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("BB_USERNAME", "BB_PASSWORD", "SCRAPER_IN_CONTAINER"):
        monkeypatch.delenv(name, raising=False)


def _creds(monkeypatch):
    monkeypatch.setenv("BB_USERNAME", USER)
    monkeypatch.setenv("BB_PASSWORD", PW)


# --- stand-ins ----------------------------------------------------------------
class _Req:
    def __init__(self, method="POST", post_data=SIGN_IN_POST, rtype="xhr"):
        self.method, self._post, self.resource_type = method, post_data, rtype

    @property
    def post_data(self):
        if isinstance(self._post, Exception):
            raise self._post
        return self._post


class _Frame:
    def __init__(self, page):
        self.page = page


class _Resp:
    """A response to the sign-in's background POST, whose body repeats what was typed."""
    def __init__(self, page, req, url=AJAX, body=ECHO):
        self.url, self.status, self.request, self._body = url, 200, req, body
        self.frame = _Frame(page)
        self.headers = {"content-type": "application/json"}

    def text(self):
        return self._body

    def body(self):
        return self._body.encode()


class _Loc:
    """A locator on _BBPage: what it does depends on which part of the form it names."""
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    @property
    def first(self):
        return self

    def locator(self, sel):
        return _Loc(self.page, sel)

    def _role(self):
        self.page._boom()
        for role in ("opener", "form", "user", "password", "remember", "submit", "error"):
            if self.sel.startswith(getattr(F, role)):
                return role
        return None

    def count(self):
        role, p = self._role(), self.page
        if self.sel == "input[type=password]:visible":           # browser.logged_out
            return int(p.has_form and p.form_open)
        return {"opener": int(p.has_opener), "form": int(p.has_form)}.get(role, 0)

    def is_visible(self):
        role, p = self._role(), self.page
        if role == "form":
            return p.has_form and p.form_open
        if role == "error":
            return p.submitted and p.refusal is not None
        return True

    def wait_for(self, state=None, timeout=None):
        if not self.is_visible():
            raise TimeoutError("not visible")

    def inner_text(self, timeout=None):
        self._role()
        return self.page.refusal or ""

    def click(self, timeout=None):
        role = self._role()
        self.page.calls.append(("click", role))
        if role == "opener":
            self.page.form_open = True
        elif role == "submit":
            self.page._submit()

    def fill(self, value, timeout=None):
        role = self._role()
        self.page.calls.append(("fill", role))
        self.page.typed[role] = value

    def check(self, timeout=None):
        self.page.calls.append(("check", self._role()))


class _World:
    """What the site thinks: is this box signed in, and does it accept the login.
    A sent form lands back on the page it was sent from (the real form's
    redirect is the logged-out page itself), so only a fresh load of the tool
    url can tell whether the sign-in took."""
    def __init__(self, accept=True, refusal=None, title="Bookie Bashing"):
        self.url, self.accept, self.refusal, self.title = OUT, accept, refusal, title


class _BBPage(_Nothing):
    """A Bookie Bashing page: logged out (on OUT, with the hidden pop-up form) or
    in. Submitting the form sends the background POST (to the page's response
    listeners, as Playwright would) and then navigates, or shows the refusal."""
    def __init__(self, world=None, url=OUT, opener=True, form=True, form_open=False,
                 refusal=None, raises=False, title="Bookie Bashing"):
        self.world, self.url = world, url
        self.has_opener, self.has_form, self.form_open = opener, form, form_open
        self.refusal, self.raises, self._title = refusal, raises, title
        self.submitted = False
        self.calls, self.typed, self.handlers = [], {}, {}
        self.main_frame = object()

    def _boom(self):
        if self.raises:
            raise RuntimeError(f"boom {USER} {PW}")

    def on(self, event, fn):
        self.handlers[event] = fn

    def locator(self, sel):
        self._boom()
        return _Loc(self, sel)

    def title(self):
        self._boom()
        return self._title

    def wait_for_timeout(self, ms):
        self._boom()

    def wait_for_load_state(self, state=None, timeout=None):
        self._boom()
        self.calls.append(("wait", state))

    def _submit(self):
        self.submitted = True
        if "response" in self.handlers:
            self.handlers["response"](_Resp(self, _Req()))
        if self.refusal is not None:
            return
        self.form_open = False                  # the page navigates, or reloads where it was
        if self.world is None:
            self.url = IN
        elif self.world.accept:
            self.world.url = IN


class _Ctx:
    def __init__(self, world):
        self.world, self.pages = world, []

    def new_page(self):
        w = self.world
        page = _BBPage(w, url=w.url, refusal=w.refusal, title=w.title)
        self.pages.append(page)
        return page


BB = Target(name="bb-saves", site="bookiebashing", url=IN, every_min=10, stale_min=10_000)


def _bb_runner(tmp_path, world, targets=(BB,)):
    tmp_path.mkdir(parents=True, exist_ok=True)
    r = _runner(tmp_path, list(targets))
    r.ctx = _Ctx(world)
    return r


def _alerts(tmp_path):
    p = tmp_path / "alerts.log"
    return [line.split("  ", 1)[1] for line in p.read_text().splitlines()] if p.exists() else []


def _attempts(monkeypatch):
    calls, real = [], login.attempt
    monkeypatch.setattr(login, "attempt", lambda page, site, *a, **k: calls.append(site)
                        or real(page, site, *a, **k))
    return calls


# --- login.py -----------------------------------------------------------------
def test_credentials_need_both():
    assert login.credentials("bookiebashing", {"BB_USERNAME": USER}) is None
    assert login.credentials("bookiebashing", {"BB_PASSWORD": PW}) is None
    assert login.credentials("bookiebashing", {"BB_USERNAME": "", "BB_PASSWORD": ""}) is None
    assert login.credentials("bookiebashing", {"BB_USERNAME": "  ", "BB_PASSWORD": PW}) is None
    assert login.credentials("bookiebashing", {"BB_USERNAME": USER, "BB_PASSWORD": " \t"}) is None
    assert login.credentials("bookiebashing", {"BB_USERNAME": f" {USER} ",
                                               "BB_PASSWORD": f"{PW}\n"}) == (USER, PW)
    assert login.credentials("paddypower", {"BB_USERNAME": USER, "BB_PASSWORD": PW}) is None


def test_attempt_fills_and_submits_in_order():
    page = _BBPage()
    assert login.attempt(page, "bookiebashing", (USER, PW)) is None
    assert page.calls == [("wait", "load"), ("click", "opener"), ("fill", "user"),
                          ("fill", "password"), ("check", "remember"), ("click", "submit")]
    assert page.typed == {"user": USER, "password": PW}
    assert page.url == IN                                   # it navigated
    page = _BBPage(form_open=True)                          # already showing: no opener click
    assert login.attempt(page, "bookiebashing", (USER, PW)) is None
    assert [c for c in page.calls if c[1] == "opener"] == []
    assert page.calls[-1] == ("click", "submit")


def test_attempt_no_form():
    page = _BBPage(opener=False, form=False)
    assert login.attempt(page, "bookiebashing", (USER, PW)) == "no login form"
    assert [c for c in page.calls if c[0] != "wait"] == [] and page.typed == {}
    assert login.attempt(_BBPage(), "paddypower", (USER, PW)) == "no login form"
    # a lapsed subscription: /how-it-works while logged in, the pop-up's markup
    # there but no Login item to open it - a failed attempt, not a loop
    page = _BBPage(opener=False)
    assert login.attempt(page, "bookiebashing", (USER, PW)) == "no login form"
    assert page.typed == {}


def test_attempt_refused_is_scrubbed():
    page = _BBPage(refusal=f"The password {PW} for {USER} is wrong")
    why = login.attempt(page, "bookiebashing", (USER, PW))
    assert why.startswith("refused: ")
    assert PW not in why and USER not in why
    assert len(why) <= login.WHY_MAX
    page = _BBPage(refusal=f"Unknown user {USER}. " + "Lorem ipsum. " * 30 + PW)
    why = login.attempt(page, "bookiebashing", (USER, PW))
    assert why.startswith("refused: ") and len(why) <= login.WHY_MAX
    assert PW not in why and USER not in why
    # the longer credential goes first, so a username inside the password leaves none of it
    page = _BBPage(refusal="bad login u1 / u1secret")
    why = login.attempt(page, "bookiebashing", ("u1", "u1secret"))
    assert "secret" not in why and "u1" not in why


def test_scrub_edge_cases():
    scrub = login._scrub
    # regex characters are taken literally
    out = scrub("bad: a.b*c( and aXbbc", ("u@example.test", "a.b*c("))
    assert "a.b*c(" not in out and "aXbbc" in out
    # a one- or two-character password: every occurrence goes, any case; the rest stays
    out = scrub("The password E is wrong, e", ("u@example.test", "e"))
    assert "e" not in out.lower() and "password" in out and out.replace("*", "").strip()
    out = scrub("Wrong password ab for u@example.test (AB)", ("u@example.test", "ab"))
    assert "ab" not in out.lower() and "u@example.test" not in out
    assert out.startswith("Wrong password ***")
    # a password that contains the username, and the reverse
    out = scrub("login u@example.test / u@example.test-pw", ("u@example.test", "u@example.test-pw"))
    assert "example" not in out and "-pw" not in out
    out = scrub("login ab / xaby", ("xaby", "ab"))
    assert "x" not in out and "ab" not in out
    # URL-encoded (both ways) and HTML-escaped forms
    pw = "p&w d%<1>"
    text = f"sent password=p%26w%20d%25%3C1%3E or p%26w+d%25%3C1%3E, shown p&amp;w d%&lt;1&gt;"
    out = scrub(text, ("u@example.test", pw))
    assert "p%26w" not in out and "p&amp;w" not in out and "d%" not in out
    assert out == "sent password=*** or ***, shown ***"
    # cut after scrubbing, never before
    assert len(scrub("x" * 500 + PW, (USER, PW))) == login.WHY_MAX


def test_attempt_never_raises():
    page = _BBPage(raises=True)
    why = login.attempt(page, "bookiebashing", (USER, PW))
    assert why and PW not in why and USER not in why


def test_note_round_trip_and_corrupt_file(tmp_path):
    assert login.failed(tmp_path, "bookiebashing") is None
    login.mark_failed(tmp_path, "bookiebashing", "refused: wrong", 1000.0)
    assert login.failed(tmp_path, "bookiebashing") == {"failed_at": 1000.0, "why": "refused: wrong"}
    login.clear(tmp_path, "bookiebashing")
    assert login.failed(tmp_path, "bookiebashing") is None
    (tmp_path / login.NOTE_FILE).write_text("{not json")
    assert login.failed(tmp_path, "bookiebashing") is None
    login.mark_failed(tmp_path, "bookiebashing", "x" * 500, 2000.0)
    assert len(login.failed(tmp_path, "bookiebashing")["why"]) <= login.WHY_MAX
    login.clear(tmp_path, "bookiebashing")
    shut = tmp_path / "shut"
    shut.mkdir()
    shut.chmod(0o500)
    try:
        login.mark_failed(shut, "bookiebashing", "refused", 1.0)    # does not raise
        login.clear(shut, "bookiebashing")
        assert login.failed(shut, "bookiebashing") is None
        login.mark_failed(tmp_path / "missing" / "dir", "bookiebashing", "refused", 1.0)
    finally:
        shut.chmod(0o700)


# --- the capture never keeps the sign-in's response -----------------------------
class _CapPage:
    def __init__(self):
        self.handlers, self.main_frame = {}, object()

    def on(self, event, fn):
        self.handlers[event] = fn


def _cap(tmp_path):
    page, budget = _CapPage(), DiskBudget(10)
    return page, Capture(page, tmp_path / "cap", budget), budget


def _written(tmp_path):
    return [p for p in (tmp_path / "cap").iterdir()]


def test_capture_drops_the_sign_in_response(tmp_path):
    page, cap, budget = _cap(tmp_path)
    left = budget.left
    assert len(ECHO) >= 300
    page.handlers["response"](_Resp(page, _Req()))
    assert _written(tmp_path) == []
    assert cap.since(0) == [] and not cap.pinned
    assert budget.left == left


def test_capture_keeps_other_posts(tmp_path):
    page, cap, budget = _cap(tmp_path)
    left = budget.left
    page.handlers["response"](_Resp(page, _Req(post_data="action=other")))
    assert [r[2] for r in cap.since(0)] == [AJAX]
    assert any(p.name.endswith("-http.txt") for p in _written(tmp_path))
    assert budget.left < left


def test_capture_counts_a_refused_sign_in_and_keeps_nothing(tmp_path):
    page, cap, budget = _cap(tmp_path)
    left = budget.left
    for status in (403, 429):
        resp = _Resp(page, _Req())
        resp.status = status
        page.handlers["response"](resp)
    assert cap.refused_within(600) == 2                      # counts toward the block rule
    assert cap.since(0) == [] and not cap.pinned and _written(tmp_path) == []
    assert budget.left == left


def test_signs_in_without_a_method_is_not_a_sign_in():
    from scraper import capture

    class _NoMethod:
        post_data = SIGN_IN_POST
    assert capture._signs_in(_NoMethod()) is False
    assert capture._signs_in(_Req()) is True
    assert capture._signs_in(_Req(method="GET")) is False


def test_capture_drops_a_post_it_cannot_read(tmp_path):
    page, cap, budget = _cap(tmp_path)
    page.handlers["response"](_Resp(page, _Req(post_data=UnicodeDecodeError(
        "utf-8", b"\xff", 0, 1, "invalid start byte"))))
    assert cap.since(0) == [] and _written(tmp_path) == []


# --- the runner -------------------------------------------------------------------
def test_runner_signs_in_then_runs(tmp_path, monkeypatch):
    _creds(monkeypatch)
    calls = _attempts(monkeypatch)
    world = _World(accept=True)
    bb = Target(name="bb-saves", site="bookiebashing", url=IN, every_min=10, stale_min=10_000,
                parser="not-written")             # a parser that only says it was driven
    r = _bb_runner(tmp_path, world, targets=(bb,))
    t0 = time.time()
    r.tick(t0)
    assert calls == ["bookiebashing"]
    assert "bb-saves" not in r.parser_said              # the page signed in on is not parsed
    assert _alerts(tmp_path) == []
    st = r.sites["bookiebashing"]
    assert st.blocked_for is None and st.retry_at == 0.0
    assert not r._paused("bookiebashing", t0 + 1)
    r.tick(t0 + 200)                                        # the tool url again, through the guard
    assert r.guard.spent("bookiebashing", t0 + 200)[0] == 2
    assert calls == ["bookiebashing"]
    assert _alerts(tmp_path) == []
    assert login.failed(tmp_path, "bookiebashing") is None
    said = [line["msg"] for line in joblog.read(tmp_path, "bb-saves")]
    assert said[:2] == ["logged out - signing in", "signed in"]
    assert "bb-saves" in r.parser_said                  # and now it runs
    assert "bookiebashing" not in r.login_pending


def _fail_once(tmp_path, monkeypatch):
    """Logged out, the form sent, and the next read still logged out."""
    _creds(monkeypatch)
    calls = _attempts(monkeypatch)
    r = _bb_runner(tmp_path, _World(accept=False))
    t0 = time.time()
    r.tick(t0)
    assert calls == ["bookiebashing"] and _alerts(tmp_path) == []
    r.tick(t0 + 200)
    return r, calls, t0


def test_runner_failed_sign_in_alerts_once_and_stops(tmp_path, monkeypatch):
    r, calls, t0 = _fail_once(tmp_path, monkeypatch)
    alerts = _alerts(tmp_path)
    assert len(alerts) == 1
    assert "signing in failed" in alerts[0] and "still logged out after signing in" in alerts[0]
    assert alerts[0] == (f"bookiebashing: logged out on {OUT} - signing in failed (still logged "
                         "out after signing in). Not trying again until you log in on the Hub's "
                         "screen or run `resume bookiebashing`. Backing off.")
    st = r.sites["bookiebashing"]
    assert st.blocked_for == "logged out" and st.retry_at > t0 + 200
    assert login.failed(tmp_path, "bookiebashing")["why"] == "still logged out after signing in"
    for h in range(1, 6):                                   # later logged-out reads, after the back-off
        r.tick(t0 + 3600 * h)
    assert len(_loads(r)) >= 3                              # it did read the page again
    assert calls == ["bookiebashing"]
    assert len(_alerts(tmp_path)) == 1


def test_runner_failure_survives_restart(tmp_path, monkeypatch):
    _r, calls, t0 = _fail_once(tmp_path, monkeypatch)
    r = _bb_runner(tmp_path, _World(accept=True))
    r.tick(t0 + 400)
    r.tick(t0 + 4000)
    assert calls == ["bookiebashing"]                       # only the first runner's attempt
    assert r.sites["bookiebashing"].blocked_for == "logged out"


def test_runner_no_credentials_is_unchanged(tmp_path, monkeypatch):
    calls = _attempts(monkeypatch)
    r = _bb_runner(tmp_path, _World())
    r.tick(time.time())
    assert calls == []
    assert _alerts(tmp_path) == [f"bookiebashing: logged out on {OUT} - needs you on the "
                                 "Hub's screen. Backing off."]
    assert not (tmp_path / login.NOTE_FILE).exists()


def test_runner_never_signs_in_on_a_check_or_on_paddy(tmp_path, monkeypatch):
    _creds(monkeypatch)
    calls = _attempts(monkeypatch)
    # A Cloudflare check on the logged-out page, from a load
    r = _bb_runner(tmp_path / "a", _World(title="Just a moment..."))
    r.tick(time.time())
    assert r.sites["bookiebashing"].blocked_for == "Cloudflare check"
    # ...and on a page already open (signed in, then the check is drawn)
    world = _World()
    world.url = IN
    r = _bb_runner(tmp_path / "b", world)
    t0 = time.time()
    r.tick(t0)
    page = r.tabs["bb-saves"].page
    page.url, page._title = OUT, "Just a moment..."
    r.tick(t0 + 15)
    assert r.sites["bookiebashing"].blocked_for == "Cloudflare check"
    # Paddy, on a page that would read as logged out on Bookie Bashing
    pp = Target(name="pp", site="paddypower", url="http://127.0.0.1/how-it-works/x",
                stale_min=10_000)
    r = _bb_runner(tmp_path / "c", _World(), targets=(pp,))
    r.ctx.world.url = pp.url
    t0 = time.time()
    r.tick(t0)
    r.tabs["pp"].page.form_open = True                      # a visible password field
    r.tick(t0 + 15)
    assert calls == []


def _resume(tmp_path, monkeypatch, site="bookiebashing"):
    from scraper.__main__ import main
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPER_TARGETS", str(tmp_path / "targets.toml"))
    main(["resume", site])


def test_resume_forgets_a_failed_sign_in(tmp_path, monkeypatch, capsys):
    r, calls, t0 = _fail_once(tmp_path, monkeypatch)           # a running service, stopped
    r.tick(t0 + 215)
    assert calls == ["bookiebashing"]
    capsys.readouterr()
    _resume(tmp_path, monkeypatch)
    assert login.failed(tmp_path, "bookiebashing") is None
    assert capsys.readouterr().out.rstrip().endswith(
        " A failed sign-in is forgotten: it will try once more the next time it finds the "
        "site logged out.")
    r.tick(t0 + 230)                                         # the running service notices
    assert calls == ["bookiebashing", "bookiebashing"]
    _resume(tmp_path, monkeypatch, "paddypower")
    assert "sign-in" not in capsys.readouterr().out


def test_failure_alert_goes_out_after_a_resume_mid_back_off(tmp_path, monkeypatch):
    r, calls, t0 = _fail_once(tmp_path, monkeypatch)
    assert len(_alerts(tmp_path)) == 1
    st = r.sites["bookiebashing"]
    assert st.blocked_for == "logged out" and st.retry_at > t0 + 230
    _resume(tmp_path, monkeypatch)
    r.tick(t0 + 230)                                         # backing off: sent on the open page
    assert calls == ["bookiebashing", "bookiebashing"]
    r.tick(st.retry_at + 1)                                  # the reload, still logged out
    alerts = _alerts(tmp_path)
    assert len(alerts) == 2 and "signing in failed (still logged out" in alerts[1]


def test_runner_unwritable_note_still_stops_at_one_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "_write", lambda data_dir, notes: False)   # disk full
    r, calls, t0 = _fail_once(tmp_path, monkeypatch)
    for i in range(1, 40):
        r.tick(t0 + 200 + 15 * i)
    for h in range(1, 6):
        r.tick(t0 + 3600 * h)
    assert login.failed(tmp_path, "bookiebashing") is None   # nothing reached disk
    assert calls == ["bookiebashing"]
    alerts = _alerts(tmp_path)
    assert len(alerts) == 1 and "signing in failed (still logged out" in alerts[0]


def test_pending_is_dropped_when_the_tab_goes(tmp_path, monkeypatch):
    _creds(monkeypatch)
    calls = _attempts(monkeypatch)
    r = _bb_runner(tmp_path, _World(accept=False))
    t0 = time.time()
    r.tick(t0)
    assert "bookiebashing" in r.login_pending
    r.apply_targets([])                                      # the target removed: its tab closes
    r.tick(t0 + 15)
    assert "bookiebashing" not in r.login_pending
    r.apply_targets([BB])
    r.tick(t0 + 200)                                         # logged out again: a real attempt
    assert calls == ["bookiebashing", "bookiebashing"]
    assert login.failed(tmp_path, "bookiebashing") is None and _alerts(tmp_path) == []


def test_chrome_env_has_no_login_variables():
    from scraper import browser
    assert set(login.env_names()) == {"BB_USERNAME", "BB_PASSWORD"}
    env = {"HOME": "/home/x", "BB_USERNAME": USER, "BB_PASSWORD": PW, "SCRAPER_DATA_DIR": "/d"}
    for kw in (browser.launch_options(headless=False, environ=env),
               browser.launch_options(headless=True, environ=env),
               browser.launch_options(environ={**env, "SCRAPER_IN_CONTAINER": "1"})):
        assert "BB_USERNAME" not in kw["env"] and "BB_PASSWORD" not in kw["env"]
        assert kw["env"]["HOME"] == "/home/x" and kw["env"]["SCRAPER_DATA_DIR"] == "/d"
    assert env["BB_PASSWORD"] == PW                          # the caller's environment is untouched


def _files_with(root, needle: bytes):
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            with open(p, "rb") as f:
                if needle in f.read():
                    found.append(p)
    return found


def test_password_never_written(tmp_path, monkeypatch, capsys):
    _creds(monkeypatch)
    # the failed path: sent, next read still logged out
    _fail_once(tmp_path / "failed", monkeypatch)
    # the refused path: the site's text repeats both, and the sign-in's own
    # response (which repeats them too) reaches the tab's real Capture
    refused = _World(accept=False, refusal=f"The password {PW} for {USER} is wrong")
    r = _bb_runner(tmp_path / "refused", refused)
    r.tick(time.time())
    assert any("refused: " in a for a in _alerts(tmp_path / "refused"))
    page = r.ctx.pages[0]
    assert page.submitted and "response" in page.handlers   # the Capture was listening
    out = capsys.readouterr()
    assert PW not in out.out and PW not in out.err
    assert _files_with(tmp_path, PW.encode()) == []
    assert _files_with(tmp_path, USER.encode()) == []
