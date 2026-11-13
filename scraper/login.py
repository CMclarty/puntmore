"""Signing in to a site whose kept-open page is found logged out.

The Scraper signs in to the sites its jobs need, each with Callum's own login
from the Scraper's .env (13 Nov 2026, Callum: "the scraper needs to know how
to login to the needed sites"): a site that needs one has one Form below and
two names in .env, and everything else here is the same for every site.
Bookie Bashing: BB_USERNAME, BB_PASSWORD (11 Nov 2026). Team Casino:
TC_USERNAME, TC_PASSWORD (13 Nov 2026). It signs in on the page that is
already open - the logged-out page carries the form - so signing in costs no
page load of its own. Plain fill and click, nothing else.

One attempt per logout, and at most `login.max_signins` in `login.window_h`
hours for each site (behaviour.py; 3 in 6 by default): a site that keeps
ending the session (a one-session rule set off by Callum's own devices) then
waits for a person. Each sign-in's time is written to <data>/login.json
before the form is sent; if it cannot be written, there is no sign-in. A
failed one is noted there too, and nothing is tried again on that site until
it is seen logged in or `resume <site>` is run (which also forgets the
times); the note and the times survive a restart. A challenge is never
answered, and a two-factor code is never typed: the runner never calls this
for a page browser.blocked() reports, and a site asking for a code is a
failed sign-in that waits for a person.

The password is never written anywhere, and never goes in a url. Page text
(the site's refusal) has both credentials removed before it is returned, and
the capture never keeps the sign-in's own response (capture.py,
password_fields()). A sign-in goes as a POST, the password in its body:
sent by the site's script (both sites' pages do this) or, if that script
never ran, by Chrome itself from a form whose method is POST (Team Casino's
is). Any request whose url names a password field - a form Chrome sends
itself as a GET, as Bookie Bashing's would be - is aborted inside Chrome,
for every site, and safe_url() keeps such a url out of anything written.
"""
import html
import json
import os
import pathlib
import re
import tempfile
import time
from dataclasses import dataclass
from urllib.parse import quote, quote_plus

from . import behaviour


@dataclass(frozen=True)
class Form:
    user_env: str
    pass_env: str
    form: str
    user: str             # the fields and button, inside the form
    password: str
    submit: str
    password_field: str   # the password's name in the posted form (capture.py, safe_url)
    opener: str | None = None      # what shows a hidden form (the first visible one is
                                   # clicked); None: the form is shown on the page
    remember: str | None = None    # a box to tick; None: none to tick
    error: str | None = None       # the site's refusal text, on the page; None: not known
    two_factor: str | None = None  # inside the form, shown when the site asks for a
                                   # two-factor code: a failure; a code is never typed


FORMS = {
    # Bookie Bashing (the logged-out page's own markup, 11 Nov 2026): a logged-out
    # tool url lands on /how-it-works/..., whose login form is a hidden pop-up
    # opened from the "Login" menu item (desktop and mobile menus, and a tab
    # inside the pop-up). It posts in the background, then the page navigates.
    "bookiebashing": Form(
        user_env="BB_USERNAME", pass_env="BB_PASSWORD",
        opener="li.xoo-el-login-tgr", form="form.xoo-el-form-login",
        user='input[name="xoo-el-username"]', password='input[name="xoo-el-password"]',
        remember='input[name="xoo-el-rememberme"]', submit="button.xoo-el-login-btn",
        error='[data-section="login"] .xoo-el-notice-error .xoo-el-noticetxt',
        password_field="xoo-el-password"),
    # Team Casino (its members page, https://www.teamcasino.com/reload-offers-dashboard,
    # saved logged out on 13 Nov 2026): the url stays put and the form is
    # shown at once - nothing to open. <form id="loginrequest" method="POST">
    # holds the email ("login"), the password, a two-factor box hidden until
    # the site asks for a code, and hidden remember=forever and
    # action=login_request, which go as they are (the "Remember Me" box beside
    # them is not the remember field and is left alone). The page's own
    # script (jQuery) stops the native submit, POSTs the form to
    # /wp-admin/admin-ajax.php and reloads the page after a success; it shows
    # the two-factor box when the answer asks for a code. A second form,
    # #resetrequest (a password reset), has the same button class: every part
    # is looked for inside #loginrequest, and the reset form is never touched.
    # What the site says to a wrong password has never been seen, so no
    # refusal is read: a sign-in that did not take is found by the next read
    # of the members page still showing the form ("still logged out after
    # signing in", runner.py), and waits for a person.
    "teamcasino": Form(
        user_env="TC_USERNAME", pass_env="TC_PASSWORD",
        form="form#loginrequest",
        user='input[name="login"]', password='input[name="password"]',
        submit='button.continueBtnTC[type="submit"]',
        two_factor='[id="2fa-code-input"]',      # an id starting with a digit: no #2fa... in CSS
        password_field="password"),
}

# <data>/login.json {"<site>": {"failed_at": <epoch>, "why": "<text>", "sent": [<epoch>, ...]}}
NOTE_FILE = "login.json"
WHY_MAX = 120
POLL_MS = 250                # between looks at the page after the button is pressed
NATIVE = "form not sent by the site's script"     # a request naming the password in its url
TWO_FACTOR = "the site asked for a two-factor code"


def password_fields() -> tuple[str, ...]:
    return tuple(f.password_field for f in FORMS.values())


def _field_re(name: str) -> re.Pattern:
    """A field called exactly `name`: in a url's query or a form body
    (`name=`, first or after ? & ; # or a newline - a text/plain form puts
    each field on its own line), or quoted (multipart's name="...", a JSON
    key). Not part of a longer name: a site's `forgot_password=` or
    `reset-password=` is not Team Casino's `password` field."""
    n = re.escape(name)
    return re.compile(rf'(?:^|[?&;#\r\n]){n}=|"{n}"')


def names_a_password(text: str) -> bool:
    """`text` (a url, or a request's post data) carries one of the forms'
    password fields."""
    return any(_field_re(name).search(text) for name in password_fields())


def safe_url(url: str) -> str:
    """`url` without its query and fragment when they name a password field
    (a form Chrome sent itself); else unchanged. For every url written."""
    if not _names_a_password(url):
        return url
    cut = [i for i in (url.find("?"), url.find("#"), url.find(";")) if i >= 0]
    return url[:min(cut)] if cut else url


def _names_a_password(url: str) -> bool:
    return names_a_password(url)


def env_names() -> tuple[str, ...]:
    """Every login variable; Chrome's environment leaves them out (browser.py)."""
    return tuple(n for f in FORMS.values() for n in (f.user_env, f.pass_env))


def credentials(site: str, env=os.environ) -> tuple[str, str] | None:
    """The site's login from the environment, or None unless both are set."""
    f = FORMS.get(site)
    if f is None:
        return None
    user = (env.get(f.user_env) or "").strip()
    password = (env.get(f.pass_env) or "").strip()
    return (user, password) if user and password else None


def attempt(page, site: str, creds: tuple[str, str], timeout_s: float = 20.0) -> str | None:
    """Fill and send the site's login form on `page`. None when it was sent and
    no refusal was shown within timeout_s (the next read says whether it took);
    else a short reason, scrubbed of both credentials. Never raises, and every
    call is bounded, so the whole of it returns within about timeout_s."""
    f = FORMS.get(site)
    if f is None:
        return "no login form"
    end = time.monotonic() + timeout_s

    def left() -> int:
        return max(1, int((end - time.monotonic()) * 1000))

    step = "load"
    try:
        # A page at domcontentloaded may not have bound the opener's click yet.
        page.wait_for_load_state("load", timeout=left())
        form = page.locator(f.form).first
        if not form.is_visible():
            if f.opener is None:
                return "no login form"
            opener = page.locator(f"{f.opener}:visible").first
            if not opener.count():
                return "no login form"       # also a lapsed subscription: no Login item
            step = "pop-up"
            opener.click(timeout=left())
            form.wait_for(state="visible", timeout=left())
        step = "username"
        form.locator(f.user).fill(creds[0], timeout=left())
        step = "password"
        form.locator(f.password).fill(creds[1], timeout=left())
        step = "submit"
        if f.remember is not None:
            form.locator(f.remember).check(timeout=left())
        return _send(page, f, form, creds, end, left)
    except Exception as exc:
        name = exc.__class__.__name__
        return _scrub(f"{name} ({step})" if "Timeout" in name else name, creds)


def _send(page, f, form, creds, end, left) -> str | None:
    """Press the button and watch. While it runs, any request whose url names
    a password field (a form sent by Chrome itself as a GET, because the
    site's script never bound it) is aborted inside Chrome, whatever the
    site. A POST carries the password in its body and goes, whether the
    site's script sends it or Chrome does (a POST form whose script never
    ran). The route lives only for this (Playwright turns the HTTP cache off
    while one is set)."""
    native = []

    def stop(route):
        native.append(True)
        route.abort()

    page.route(_names_a_password, stop)
    try:
        before = page.url
        form.locator(f.submit).click(timeout=left())
        # One pump whatever the deadline says: Chrome's own GET may reach the
        # route only now, and after unroute it would go out with the password.
        page.wait_for_timeout(POLL_MS)
        error = page.locator(f.error).first if f.error else None
        two_factor = form.locator(f.two_factor).first if f.two_factor else None
        while time.monotonic() < end:
            if native:
                return NATIVE
            try:
                if error is not None and error.is_visible():
                    text = error.inner_text(timeout=left())
                    if text.strip():
                        return _scrub("refused: " + text, creds)
                if two_factor is not None and two_factor.is_visible():
                    return TWO_FACTOR
                if page.url != before or not form.is_visible():
                    return NATIVE if native else None
            except Exception:
                if page.url != before:       # navigating: the page's context went
                    return NATIVE if native else None
            page.wait_for_timeout(min(POLL_MS, left()))
        return NATIVE if native else None
    finally:
        try:
            page.unroute(_names_a_password, stop)
        except Exception:
            pass


def _scrub(text: str, creds: tuple[str, str]) -> str:
    """Both credentials out (as typed, URL- and HTML-encoded, any case, runs
    of whitespace as one space - as the page's inner_text draws them; the
    longest first, so one inside the other leaves nothing), then cut."""
    text = " ".join(text.split())
    forms = {" ".join(v.split()) for c in creds if c
             for v in (c, quote(c), quote_plus(c), html.escape(c))}
    for v in sorted(forms - {""}, key=len, reverse=True):
        text = re.sub(re.escape(v), "***", text, flags=re.I)
    return text[:WHY_MAX]


# --- the note: the last attempt failed ------------------------------------------
def _path(data_dir) -> pathlib.Path:
    return pathlib.Path(data_dir) / NOTE_FILE


def _read(data_dir) -> dict:
    try:
        notes = json.loads(_path(data_dir).read_text())
    except (OSError, ValueError):
        return {}
    return notes if isinstance(notes, dict) else {}


def _write(data_dir, notes: dict) -> bool:
    """Atomic (temp file + rename). False when it could not; never raises."""
    try:
        p = _path(data_dir)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(notes, fh)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except Exception:
        return False


def _entry(notes: dict, site: str) -> dict:
    entry = notes.get(site)
    return dict(entry) if isinstance(entry, dict) else {}


def failed(data_dir, site: str) -> dict | None:
    """The note of a failed attempt, or None; a corrupt file reads as None."""
    entry = _entry(_read(data_dir), site)
    return {"failed_at": entry.get("failed_at"), "why": entry["why"]} if "why" in entry else None


def mark_failed(data_dir, site: str, why: str, now: float) -> bool:
    """True when the note reached disk. Never raises."""
    notes = _read(data_dir)
    notes[site] = {**_entry(notes, site), "failed_at": now, "why": why[:WHY_MAX]}
    return _write(data_dir, notes)


def _recent(entry: dict, now: float) -> list[float]:
    times = entry.get("sent")
    return [t for t in (times if isinstance(times, list) else [])
            if isinstance(t, (int, float)) and now - t < behaviour.get("login.window_h") * 3600]


def sent(data_dir, site: str, now: float) -> list[float]:
    """When the site's sign-ins were sent, within login.window_h of now."""
    return _recent(_entry(_read(data_dir), site), now)


def record_sent(data_dir, site: str, now: float) -> bool:
    """Write down a sign-in before it is sent (older times pruned). True when
    it reached disk; if not, the caller does not sign in. Never raises."""
    notes = _read(data_dir)
    entry = _entry(notes, site)
    notes[site] = {**entry, "sent": _recent(entry, now) + [now]}
    return _write(data_dir, notes)


def clear(data_dir, site: str, times: bool = False) -> None:
    """Forget a failed attempt (seen logged in); with `times` (`resume`), the
    sign-ins sent as well. Never raises."""
    notes = _read(data_dir)
    entry = _entry(notes, site)
    keep = {k: v for k, v in entry.items()
            if k not in ("failed_at", "why") and not (times and k == "sent")}
    if keep == entry:
        return
    if keep:
        notes[site] = keep
    else:
        del notes[site]
    _write(data_dir, notes)
