"""Signing in to a site whose kept-open page is found logged out.

The Scraper types one login only: Bookie Bashing's, Callum's own, from the
Scraper's .env (BB_USERNAME, BB_PASSWORD; Callum, 11 Nov 2026: "bb will need
to be able to login"). It signs in on the page that is already open - the
logged-out page carries the form - so signing in costs no page load. Plain
fill and click, nothing else.

One attempt per logout. A failed one is noted in <data>/login.json, and
nothing is tried again until the site is seen logged in or `resume <site>` is
run; the note survives a restart. A challenge is never answered: the runner
never calls this for a page browser.blocked() reports.

The password is never written anywhere. Page text (the site's refusal) has
both credentials removed before it is returned, and the capture never keeps
the sign-in's own response (capture.py, password_fields()).
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


@dataclass(frozen=True)
class Form:
    user_env: str
    pass_env: str
    opener: str           # what shows a hidden form; the first visible one is clicked
    form: str
    user: str             # the fields and button, inside the form
    password: str
    remember: str
    submit: str
    error: str            # the site's refusal text, on the page
    password_field: str   # the password's name in the posted form (capture.py)


# Bookie Bashing (the logged-out page's own markup, 11 Nov 2026): a logged-out
# tool url lands on /how-it-works/..., whose login form is a hidden pop-up
# opened from the "Login" menu item (desktop and mobile menus, and a tab
# inside the pop-up). It posts in the background, then the page navigates.
FORMS = {
    "bookiebashing": Form(
        user_env="BB_USERNAME", pass_env="BB_PASSWORD",
        opener="li.xoo-el-login-tgr", form="form.xoo-el-form-login",
        user='input[name="xoo-el-username"]', password='input[name="xoo-el-password"]',
        remember='input[name="xoo-el-rememberme"]', submit="button.xoo-el-login-btn",
        error='[data-section="login"] .xoo-el-notice-error .xoo-el-noticetxt',
        password_field="xoo-el-password"),
}

NOTE_FILE = "login.json"     # <data>/login.json {"<site>": {"failed_at": <epoch>, "why": "<text>"}}
WHY_MAX = 120
POLL_MS = 250                # between looks at the page after the button is pressed


def password_fields() -> tuple[str, ...]:
    return tuple(f.password_field for f in FORMS.values())


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

    try:
        # A page at domcontentloaded may not have bound the opener's click yet.
        page.wait_for_load_state("load", timeout=left())
        form = page.locator(f.form).first
        if not form.is_visible():
            opener = page.locator(f"{f.opener}:visible").first
            if not opener.count():
                return "no login form"       # also a lapsed subscription: no Login item
            opener.click(timeout=left())
            form.wait_for(state="visible", timeout=left())
        form.locator(f.user).fill(creds[0], timeout=left())
        form.locator(f.password).fill(creds[1], timeout=left())
        form.locator(f.remember).check(timeout=left())
        before = page.url
        form.locator(f.submit).click(timeout=left())
        error = page.locator(f.error).first
        while time.monotonic() < end:
            try:
                if error.is_visible():
                    text = error.inner_text(timeout=left())
                    if text.strip():
                        return _scrub("refused: " + text, creds)
                if page.url != before or not form.is_visible():
                    return None
            except Exception:
                if page.url != before:       # navigating: the page's context went
                    return None
            page.wait_for_timeout(min(POLL_MS, left()))
        return None
    except Exception as exc:
        return _scrub(exc.__class__.__name__, creds)


def _scrub(text: str, creds: tuple[str, str]) -> str:
    """Both credentials out (as typed, URL- and HTML-encoded, any case; the
    longest first, so one inside the other leaves nothing), then cut."""
    forms = {v for c in creds if c for v in (c, quote(c), quote_plus(c), html.escape(c))}
    for v in sorted(forms, key=len, reverse=True):
        text = re.sub(re.escape(v), "***", text, flags=re.I)
    return " ".join(text.split())[:WHY_MAX]


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


def failed(data_dir, site: str) -> dict | None:
    """The note of a failed attempt, or None; a corrupt file reads as None."""
    note = _read(data_dir).get(site)
    return note if isinstance(note, dict) else None


def mark_failed(data_dir, site: str, why: str, now: float) -> bool:
    """True when the note reached disk. Never raises."""
    notes = _read(data_dir)
    notes[site] = {"failed_at": now, "why": why[:WHY_MAX]}
    return _write(data_dir, notes)


def clear(data_dir, site: str) -> None:
    notes = _read(data_dir)
    if site in notes:
        del notes[site]
        _write(data_dir, notes)
