"""A look: a few plain steps on a job's already-open page, then the page saved.

Parsers here are written only from real saved pages, and some page states need
a click first (a panel opened, a preset loaded). `python -m scraper look <job>
'<steps as JSON>'` asks the RUNNING service to take those steps on that job's
open page and save it, as `dump` does (html, url, a full-page screenshot, in
data/dumps/<job>-<time>/), so a page state is learned in minutes, not by
shipping a parser that clicks.

The steps, in order, each a one-key object:

  {"click": "<text>"}      the first VISIBLE element whose own text is exactly
                           that (buttons, then links, then any element; case
                           as given), clicked with browser.click
  {"select": {"field": "<label>", "option": "<text>"}}
                           the <select> in the builder field whose button is
                           <label> (bb_saves._field_select; "nth": 1 for the
                           second block), set to the option whose text equals
                           <text>, or starts with it with "prefix": true
  {"select": {"has": "<an option's text>", "option": "<text>"}}
                           the select that holds that option instead (selects
                           with no button, as the stat chain)
  {"type": {"field": "<label>", "text": "<text>"}}
                           the input in that field, cleared and typed (as
                           bb_saves._type_line does)
  {"wait_ms": n}           at most 5000
  {"dump": "<note>"}       save the page now too (one look, several states)

At most MAX_STEPS. Every step is one line in the job's log (`look: clicked
"Show/Hide Presets"`). A step that cannot be done stops the run and says
which and why; the page is still saved. Nothing in a look loads a page: if
the url changes after a step (or the page took a new document) the run stops,
says so, and the runner counts it with the guard as a load, as
runner._open_market_tabs does. Every Playwright call in it has at most
STEP_TIMEOUT_MS, so a look never holds the loop for long.

A look is refused for a Paddy Power job, here and at the command line: the
Scraper clicks nothing there but a cookie banner and market tabs (the
register: it never adds to a betslip).
"""
import json
import re
import time

from . import browser
from . import login
from .parsers.bb_saves import _field_select, _options, _select_label, _select_prefix

MAX_STEPS = 12
MAX_WAIT_MS = 5000
STEP_TIMEOUT_MS = 8000          # every Playwright call in a look
FIND_WAIT_MS = 2000             # how long a step looks for its control to be drawn
AFTER_STEP_MS = 300             # before the url is looked at: a click's navigation starts late
REFUSED_SITES = ("paddypower",)
NOT_FOR_PADDY = ("look is not for Paddy Power pages: the Scraper clicks nothing there but a "
                 "cookie banner and market tabs")

_SELECT_KEYS = {"field", "has", "option", "nth", "prefix"}
_TYPE_KEYS = {"field", "text", "nth"}


class LookError(ValueError):
    """Steps that are not the right shape; the message says which and why."""


class StepFailed(Exception):
    """A step that could not be done on the page."""


def refused(site: str) -> str | None:
    """Why a look at a job of `site` is refused, or None."""
    return NOT_FOR_PADDY if site in REFUSED_SITES else None


def parse_steps(raw) -> list[dict]:
    """The steps from their JSON text (or already parsed), checked for shape;
    LookError says which step is wrong and why. They are returned as given."""
    if isinstance(raw, (str, bytes)):
        try:
            steps = json.loads(raw)
        except ValueError as exc:
            raise LookError(f"the steps are not JSON: {exc}") from None
    else:
        steps = raw
    if not isinstance(steps, list):
        raise LookError('the steps must be a JSON list, e.g. [{"click": "Update Data"}]')
    if len(steps) > MAX_STEPS:
        raise LookError(f"at most {MAX_STEPS} steps ({len(steps)} given)")
    for i, step in enumerate(steps, 1):
        _check(i, step)
    return steps


def _text(v) -> bool:
    return isinstance(v, str) and v != ""


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _check(i: int, step) -> None:
    def bad(why):
        raise LookError(f"step {i}: {why}")
    if not isinstance(step, dict) or len(step) != 1:
        bad('each step is an object with one key: click, select, type, wait_ms or dump')
    (kind, arg), = step.items()
    if kind == "click":
        if not _text(arg):
            bad("click needs the text of what to click")
    elif kind == "select":
        if not isinstance(arg, dict):
            bad('select needs an object: {"field": "<label>", "option": "<text>"}')
        for k in arg:
            if k not in _SELECT_KEYS:
                bad(f"select does not take {k!r} (it takes {', '.join(sorted(_SELECT_KEYS))})")
        if ("field" in arg) == ("has" in arg) or not _text(arg.get("field", arg.get("has"))):
            bad("select needs a field or has (one of them)")
        if not _text(arg.get("option")):
            bad("select needs an option")
        if "nth" in arg and not _whole(arg["nth"]):
            bad("nth is a whole number from 0")
        if "prefix" in arg and not isinstance(arg["prefix"], bool):
            bad("prefix is true or false")
    elif kind == "type":
        if not isinstance(arg, dict) or not _text(arg.get("field")) or not isinstance(arg.get("text"), str):
            bad('type needs a field and text: {"field": "<label>", "text": "<text>"}')
        for k in arg:
            if k not in _TYPE_KEYS:
                bad(f"type does not take {k!r} (it takes {', '.join(sorted(_TYPE_KEYS))})")
        if "nth" in arg and not _whole(arg["nth"]):
            bad("nth is a whole number from 0")
    elif kind == "wait_ms":
        if not _whole(arg) or arg > MAX_WAIT_MS:
            bad(f"wait_ms is at most {MAX_WAIT_MS} (whole ms, 0 or more)")
    elif kind == "dump":
        if not isinstance(arg, str):
            bad("dump needs a note (text)")
    else:
        bad(f"unknown step {kind!r} (click, select, type, wait_ms or dump)")


# --- on the page --------------------------------------------------------------------
def _quote(s: str) -> str:
    """A string for a Playwright selector, quoted and escaped."""
    return json.dumps(s)


def _found(page, find):
    """find() until it gives something, for up to FIND_WAIT_MS."""
    deadline = time.monotonic() + FIND_WAIT_MS / 1000
    while True:
        got = find()
        if got is not None or time.monotonic() >= deadline:
            return got
        page.wait_for_timeout(250)


def _present(loc):
    """The locator when it matches something now, else None."""
    return loc if loc.count() else None


def _own_text(el) -> str:
    return " ".join(el.inner_text().split())


def _clickable(page, text: str):
    """The first visible button, then link, then any element whose text is
    exactly `text` (whitespace evened out, case as given)."""
    for loc, check in ((page.get_by_role("button", name=text, exact=True), True),
                       (page.get_by_role("link", name=text, exact=True), True),
                       (page.get_by_text(text, exact=True), False)):
        for i in range(min(loc.count(), 20)):
            el = loc.nth(i)
            if el.is_visible() and (not check or _own_text(el) == text):
                return el
    return None


def _click(page, text, save):
    el = _found(page, lambda: _clickable(page, text))
    if el is None:
        raise StepFailed(f'no visible element whose text is "{text}"')
    browser.click(page, el)
    return f'clicked "{text}"'


def _select(page, arg, save):
    nth = arg.get("nth", 0)
    if "field" in arg:
        what = f'"{arg["field"]}"' + (f" (block {nth + 1})" if nth else "")
        sel = _found(page, lambda: _present(_field_select(page, arg["field"], nth)))
        missing = f'no select in the field "{arg["field"]}"' + (f" (nth {nth})" if nth else "")
    else:
        what = f'the select holding "{arg["has"]}"'
        loc = page.locator(f"select:has(option:text-is({_quote(arg['has'])}))")
        sel = _found(page, lambda: loc.nth(nth) if loc.count() > nth else None)
        missing = f'no select holds an option "{arg["has"]}"'
    if sel is None:
        raise StepFailed(missing)
    option = arg["option"]
    chosen = _select_prefix(sel, option) if arg.get("prefix") else (option if _select_label(sel, option) else None)
    if chosen is None:
        raise StepFailed(f'no option {"starting " if arg.get("prefix") else ""}"{option}" '
                         f'(options: {", ".join(_options(sel))})')
    return f"set {what} to \"{chosen}\""


def _field_input(page, label, nth=0):
    """The input in the builder field whose button is `label` (the rule of
    bb_saves._field_select, an input in place of the select)."""
    return page.locator(f"div.field:has(> div.control > button:has-text({_quote(label)})) input").nth(nth)


def _type(page, arg, save):
    inp = _found(page, lambda: _present(_field_input(page, arg["field"], arg.get("nth", 0))))
    if inp is None:
        raise StepFailed(f'no input in the field "{arg["field"]}"')
    browser.click(page, inp)
    page.keyboard.press("Control+A")
    page.keyboard.type(arg["text"])
    page.keyboard.press("Tab")
    return f'typed "{arg["text"]}" in "{arg["field"]}"'


def _wait(page, ms, save):
    ms = min(int(ms), MAX_WAIT_MS)
    page.wait_for_timeout(ms)
    return f"waited {ms} ms"


def _dump(page, note, save):
    stem = save(note or "dump")
    return f'saved "{note}" as {stem}.*'


_STEPS = {"click": _click, "select": _select, "type": _type, "wait_ms": _wait, "dump": _dump}


def _slug(note: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", note.lower()).strip("-")[:40] or "dump"


def save(page, out, stem) -> None:
    """The page as it is drawn, as `dump` saves it: <stem>.url (without
    anything secret, login.safe_url), <stem>.html, <stem>.png (the whole
    page). No request."""
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.url").write_text(login.safe_url(page.url))
    (out / f"{stem}.html").write_text(page.content(), encoding="utf-8")
    browser.screenshot(page, out / f"{stem}.png", full_page=True, timeout_ms=STEP_TIMEOUT_MS)


def _documents(capture) -> int | None:
    return getattr(capture, "documents", None)


def run(page, steps, out, say, capture=None, on_load=None) -> bool:
    """Take `steps` (parse_steps' shape) on `page`, then save it to `out`.
    `say(msg)` gets one line per step (the runner prefixes "look: " and writes
    the job's log); `on_load(url)` is called once if a step moved the page
    (the runner counts it with the guard). True when every step was done."""
    page.set_default_timeout(STEP_TIMEOUT_MS)
    saved = [0]

    def save_as(note) -> str | None:
        saved[0] += 1
        stem = f"{saved[0]}-{_slug(note)}"
        try:
            save(page, out, stem)
        except Exception as exc:
            say(f"saving {stem} failed: {exc.__class__.__name__}: {exc}")
        return out / stem

    ok = True
    n = len(steps)
    for i, step in enumerate(steps, 1):
        (kind, arg), = step.items()
        url, docs = page.url, _documents(capture)
        try:
            done = _STEPS[kind](page, arg, save_as)
            if kind in ("click", "select", "type"):
                page.wait_for_timeout(AFTER_STEP_MS)
        except Exception as exc:
            why = str(exc) if isinstance(exc, StepFailed) else f"{exc.__class__.__name__}: {exc}"
            say(f"step {i} of {n} ({json.dumps(step)}) failed: {(why.splitlines() or [''])[0]} - stopped")
            ok = False
            break
        if page.url != url or _documents(capture) != docs:
            say(f"step {i} of {n} ({json.dumps(step)}) changed the page (now "
                f"{login.safe_url(page.url)}) - stopped; counted as a load")
            if on_load is not None:
                on_load(page.url)
            ok = False
            break
        say(done)
    save_as("end" if ok else "stopped")
    say(f"saved {out}")
    return ok
