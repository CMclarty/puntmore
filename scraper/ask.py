"""Requests for the running service, answered while it runs: `python -m scraper ask`.

A request is one JSON file, data/requests/ask-<id>.json, written atomically
(the CLI writes a dot file and renames it). The running loop takes the
oldest (`pending`) and answers it in data/requests/done/ask-<id>.json:
`running` as soon as it is taken, with the items done so far, and `done`,
`refused`, `stopped` or `interrupted` at the end. One request runs at a
time (the runner knows: `pending`'s `busy`): one that arrives while another
runs is refused ("another request is running"). A request file is removed
only once a result for it is written; a result already there for an id is
never run again, so a request is never started twice. Anything else in the
folder that looks like a request (a directory, a file over MAX_BYTES, text
that will not parse) is refused when its name gives an id, and moved aside
to data/requests/rejected/ when it cannot be removed or has no id. Results
and the pages requests saved are kept PRUNE_DAYS (`prune`, once a day).

Two kinds:

  read   load Paddy Power match pages, one at a time, each in a tab of its
         own, run a parser over what arrived, close the tab. The pages are
         named by a Paddy matches job and a kick-off range ("job", "from",
         "to": the match links on that job's listings whose kick-off is in
         the range; a listing whose tab is not open, or is older than the
         job's relist time, is loaded by the request, and its tab closed
         once read: the result's "listings") or by address ("urls", at most
         MAX_URLS). "parser": one of READ_PARSERS, which click nothing, or
         null. "keep": true saves each page (HTML, screenshot, its captured
         payloads as payloads.json) to data/dumps/ask-<id>-<n>/; a page
         whose parser failed is saved whatever keep says. A page that ends
         on another host (a redirect) is failed: not parsed, not saved.
         Page urls are written without their query (`bare`): in results,
         saved pages and logs.

         A requested load is recorded with the guard as requested
         (guard.py): it does not count against the site's hourly and daily
         budget, and it skips quiet hours, but it keeps the spacing between
         loads (the pacer), the hosts rule, and stops at once on a cool-off
         or back-off (Callum, 26 Nov 2026: docs/decisions.md). A block on a
         requested load is a block like any other.

  price  read Bookie Bashing's fair (Total Odds) for named presets on named
         games, on the bet builder page a Bookie Bashing builder job keeps
         open ("job": a job whose parser is bb_builder, e.g. bb-look). Read
         only: nothing is saved and nothing that changes the account is
         pressed ("save": "no", the default; "dry" and "press" are refused,
         "save not built"). "items": at most MAX_ITEMS of {"key",
         "competition", "home", "away", "preset"}, each key once. Nothing is
         loaded: with the job's page not open, its site cooling off, backing
         off or signing in, or the builder not up, the request stops and
         says why. Each tick works through items for at most
         `bb.call_budget_s` (at least one), so a long request spans ticks
         and its result shows the items done so far. A result item: the
         request's item as given, "state" (priced, no_game, no_preset,
         unsettled), "fair" (Total Odds, or null: a fair is taken only with
         proof the preset was applied to that game), "saved" ("no"),
         "reason", "description" (the bet as the builder words it),
         "data_age_s" (the page's "Ns since data update"), "respaced" (the
         preset was already chosen, so a spacer preset was chosen first).
         Items may be taken out of order, so that an item whose preset is
         the one already chosen does not come next; each keeps its key. An
         unexpected failure on one item (a timeout) leaves it unsettled, its
         reason the exception's class; the request goes on. "list_presets": true
         (items may then be empty) adds "presets": [{"name", "count"}] (Use
         Preset's names, whitespace collapsed, and how many options carry
         each) and "presets_from" (the game the panel was drawn for; with
         none chosen, the first listed is chosen). The driver is
         parsers/bb_presets.py.

What happens in the browser is Runner._ask's; this module is the files.
"""
import json
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from . import browser
from . import config
from .parsers import PARSERS

KINDS = ("read", "price")
# The parsers a read may run: ones that only read what arrived and click
# nothing (on a Paddy page nothing is clicked but the cookie banner).
READ_PARSERS = ("pp_most",)
MAX_URLS = 60
MAX_ITEMS = 60
MAX_TEXT = 200                       # a price item's field, at most
SAVES = ("no", "dry", "press")
MAX_BYTES = 1024 * 1024             # a request file larger than this is refused unread
PRUNE_DAYS = 14
FIELDS = {"read": {"id", "kind", "job", "from", "to", "urls", "parser", "keep"},
          "price": {"id", "kind", "job", "items", "save", "list_presets"}}
ITEM_FIELDS = ("key", "competition", "home", "away", "preset")
BUILDER_SITE, BUILDER_PARSER = "bookiebashing", "bb_builder"
LONDON = ZoneInfo("Europe/London")
_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ANOTHER = "another request is running"
INTERRUPTED = "the Scraper restarted mid-request; the items listed were done"
# A request's saved page: data/dumps/ask-<id>-<n> (n at most 60). Every other
# dump (`dump`, `look`) ends in its time, -YYYYmmdd-HHMMSS, so a target
# called ask-... never matches.
_DUMP = re.compile(r"^ask-[A-Za-z0-9._-]{1,64}-[1-9][0-9]?$")
TMP_STALE_S = 3600                   # a result's temp file older than this was left by a failed write
_URL_QUERY = re.compile(r"((?:https?|wss?)://[^\s?#'\"<>]+)[?#][^\s'\"<>]*")


class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def bare(url: str | None) -> str | None:
    """A page url without its query or fragment (a site's key may ride in
    the query): what results, saved pages and logs carry."""
    if url is None:
        return None
    try:
        u = urlparse(url)
    except ValueError:
        return url.split("?", 1)[0].split("#", 1)[0]
    return urlunparse((u.scheme, u.netloc, u.path, "", "", ""))


def scrub(text: str | None) -> str | None:
    """Text (an error, a reason) with every url in it made `bare`."""
    return None if text is None else _URL_QUERY.sub(r"\1", text)


@dataclass(frozen=True)
class PriceItem:
    key: str
    competition: str
    home: str
    away: str
    preset: str


@dataclass(frozen=True)
class Request:
    id: str
    kind: str
    job: str | None             # read: a Paddy matches job, with start and end ...; price: a builder job
    start: float | None         # epoch seconds ("from")
    end: float | None           # epoch seconds ("to")
    urls: tuple[str, ...]       # ... or the pages by address
    parser: str | None
    keep: bool
    items: tuple[PriceItem, ...] = ()    # price
    save: str = "no"                     # price
    list_presets: bool = False           # price: the result also lists Use Preset's names


def iso(ts: float | None) -> str | None:
    """Epoch seconds as ISO 8601, UTC; None stays None."""
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _when(raw: dict, key: str) -> float:
    v = raw.get(key)
    try:
        t = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        raise Refused(f"`{key}` is not an ISO time: {v!r}") from None
    if t.tzinfo is None:                    # a time with no zone is London's
        t = t.replace(tzinfo=LONDON)
    return t.timestamp()


def _paddy_job(name, targets) -> bool:
    return any(t.name == name and t.site == "paddypower" and t.matches for t in targets)


def builder_job(name, targets):
    """The Bookie Bashing builder job (parser bb_builder) called `name`, or None."""
    return next((t for t in targets if t.name == name and t.site == BUILDER_SITE
                 and t.parser == BUILDER_PARSER), None)


def _price(raw: dict, rid: str, targets) -> Request:
    job = raw.get("job")
    if not isinstance(job, str) or not job:
        raise Refused("job must be a job's name")
    if targets is not None and builder_job(job, targets) is None:
        raise Refused(f"no Bookie Bashing builder job called {job!r}")
    save = raw.get("save", "no")
    if save not in SAVES:
        raise Refused(f"save must be one of {', '.join(SAVES)}")
    if save != "no":
        raise Refused("save not built")
    list_presets = raw.get("list_presets", False)
    if not isinstance(list_presets, bool):
        raise Refused("list_presets must be true or false")
    items = raw.get("items", [] if list_presets else None)
    if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
        raise Refused("items must be a list of objects")
    if not items and not list_presets:
        raise Refused("items is empty")
    if len(items) > MAX_ITEMS:
        raise Refused(f"at most {MAX_ITEMS} items in one request (got {len(items)})")
    out, keys = [], set()
    for n, x in enumerate(items, 1):
        extra = sorted(set(x) - set(ITEM_FIELDS))
        if extra:
            raise Refused(f"item {n}: unknown field(s): {', '.join(extra)}")
        for f in ITEM_FIELDS:
            v = x.get(f)
            if not isinstance(v, str) or not v.strip():
                raise Refused(f"item {n}: {f} must be text")
            if len(v) > MAX_TEXT:
                raise Refused(f"item {n}: {f} is over {MAX_TEXT} characters")
        if x["key"] in keys:
            raise Refused(f"item {n}: key {x['key']!r} is given twice")
        keys.add(x["key"])
        out.append(PriceItem(*(x[f] for f in ITEM_FIELDS)))
    return Request(rid, "price", job, None, None, (), None, False, tuple(out), save, list_presets)


def parse(raw: dict, targets=None) -> Request:
    """The request, checked for shape; Refused says why not. With `targets`
    (the CLI's and the runner's), a job must be one of them: a Paddy Power
    matches job for a read, a Bookie Bashing builder job for a price."""
    if not isinstance(raw, dict):
        raise Refused("the request is not a JSON object")
    kind = raw.get("kind")
    known = FIELDS[kind] if isinstance(kind, str) and kind in FIELDS else set().union(*FIELDS.values())
    extra = sorted(set(raw) - known)
    if extra:
        raise Refused(f"unknown field(s): {', '.join(extra)}")
    rid = raw.get("id")
    if not isinstance(rid, str) or not _ID.match(rid):
        raise Refused("id must be 1-64 letters, digits, '.', '_' or '-'")
    if kind not in KINDS:
        raise Refused(f"unknown kind {kind!r} (known: {', '.join(KINDS)})")
    if kind == "price":
        return _price(raw, rid, targets)
    parser = raw.get("parser")
    if parser is not None and (not isinstance(parser, str) or parser not in PARSERS):
        raise Refused(f"no parser called {parser!r}")
    if parser is not None and parser not in READ_PARSERS:
        raise Refused(f"parser {parser!r} may not be run by a read: only parsers that click "
                      f"nothing ({', '.join(READ_PARSERS)})")
    keep = raw.get("keep", False)
    if not isinstance(keep, bool):
        raise Refused("keep must be true or false")
    by_job = any(k in raw for k in ("job", "from", "to"))
    if by_job and "urls" in raw:
        raise Refused("name a job with from and to, or urls - not both")
    if not by_job and "urls" not in raw:
        raise Refused("name a job with from and to, or urls")
    job, start, end, urls = None, None, None, ()
    if by_job:
        job = raw.get("job")
        if not isinstance(job, str) or not job:
            raise Refused("job must be a job's name")
        if "from" not in raw or "to" not in raw:
            raise Refused("a job needs both from and to")
        start, end = _when(raw, "from"), _when(raw, "to")
        if start > end:
            raise Refused("`from` is after `to`")
        if targets is not None and not _paddy_job(job, targets):
            raise Refused(f"no Paddy Power matches job called {job!r}")
    else:
        urls = raw["urls"]
        if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
            raise Refused("urls must be a list of addresses")
        if not urls:
            raise Refused("urls is empty")
        if len(urls) > MAX_URLS:
            raise Refused(f"at most {MAX_URLS} urls in one request (got {len(urls)})")
        for u in urls:
            try:
                ok = config.paddy_page(u)
            except ValueError:
                ok = False
            if not ok:
                raise Refused(f"not a Paddy Power page: {bare(u)}")
        urls = tuple(urls)
    if parser is None and not keep:
        raise Refused("nothing to do: no parser, and keep is false")
    return Request(rid, kind, job, start, end, urls, parser, keep)


# --- the files ------------------------------------------------------------------
def result_path(data_dir, rid: str):
    return data_dir / "requests" / "done" / f"ask-{rid}.json"


def result(req_id: str, kind, state: str, reason=None, started=None, finished=None, items=(),
           listings=()) -> dict:
    return {"id": req_id, "kind": kind, "state": state, "reason": reason,
            "started": started, "finished": finished, "listings": list(listings),
            "items": list(items)}


def write_result(data_dir, body: dict) -> bool:
    """Write a result atomically (a temp file renamed, as sinks._drop). False
    when it could not be written (said on stdout)."""
    final = result_path(data_dir, body["id"])
    tmp = final.with_name(final.name + ".tmp")
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(body, indent=1, default=str))   # a parser's odd value as text
        tmp.rename(final)
        return True
    except (OSError, ValueError, RecursionError) as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"ask {body.get('id')}: RESULT NOT WRITTEN ({exc.__class__.__name__}: {exc})",
              flush=True)
        return False


def read_result(data_dir, rid: str) -> dict | None:
    try:
        return json.loads(result_path(data_dir, rid).read_text())
    except (OSError, ValueError, RecursionError):
        return None


def answered(data_dir, rid: str) -> bool:
    return result_path(data_dir, rid).exists()


def running(data_dir) -> list[str]:
    """The ids of the results on disk that say `running`."""
    out = []
    for f in sorted((data_dir / "requests" / "done").glob("ask-*.json")):
        try:
            if json.loads(f.read_text()).get("state") == "running":
                out.append(f.name[len("ask-"):-len(".json")])
        except Exception:
            continue
    return out


def interrupt_running(data_dir, now=None) -> list[str]:
    """At the service's start: every result still `running` was cut short by
    the last run ending. Rewritten `interrupted`, keeping the items it had
    done; nothing is run again. The ids rewritten."""
    now = now or time.time()
    ids = running(data_dir)
    for rid in ids:
        body = read_result(data_dir, rid)
        if body is None:
            continue
        body.update(state="interrupted", reason=INTERRUPTED, finished=iso(now))
        write_result(data_dir, body)
    return ids


def _aside(entry, folder) -> None:
    """Move an entry that cannot be answered or removed out of the way
    (data/requests/rejected/); if even that fails it stays, skipped."""
    try:
        out = folder / "rejected"
        out.mkdir(exist_ok=True)
        entry.rename(out / f"{entry.name}-{time.time_ns()}")
        print(f"ask: {entry.name} moved to {out}", flush=True)
    except OSError as exc:
        print(f"ask: {entry.name} left where it is ({exc.__class__.__name__}: {exc})", flush=True)


def _remove(entry, folder) -> None:
    try:
        entry.unlink()
    except OSError:                          # a directory, a file it may not remove
        _aside(entry, folder)


def _age(entry) -> tuple:
    try:
        return entry.lstat().st_mtime, entry.name
    except OSError:
        return 0.0, entry.name


def _read(entry, targets, busy) -> Request:
    """One entry as a request; Refused for anything that is not one."""
    if not entry.is_file():
        raise Refused("not a request file")
    if entry.stat().st_size > MAX_BYTES:
        raise Refused(f"the request is over {MAX_BYTES // (1024 * 1024)} MB")
    text = entry.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise Refused(f"the request is not JSON: {exc}") from None
    req = parse(raw, targets=targets)
    rid = entry.name[len("ask-"):-len(".json")]
    if req.id != rid:
        raise Refused(f"the id {req.id!r} is not the file's ({rid!r})")
    if busy:
        raise Refused(ANOTHER)
    return req


def pending(data_dir, targets=None, now=None, busy=False, unwritten=None) -> Request | None:
    """The oldest request waiting, taken: its result written `running`, then
    its file removed. `busy`: the runner is answering one already, so every
    request waiting is refused. On the way, every entry that cannot run is
    answered `refused`, then removed: one that is not a readable request
    file (`_read`; any error at all), that does not parse (`targets` as in
    parse), whose id is not its file's. One already answered is removed
    unrun; one whose name gives no id is moved aside. A result that cannot
    be written leaves its request where it is, for the next look, and calls
    `unwritten(id)`."""
    now = now or time.time()
    folder = data_dir / "requests"
    try:
        entries = sorted(folder.glob("ask-*.json"), key=_age)
    except OSError:
        return None
    for entry in entries:
        rid = entry.name[len("ask-"):-len(".json")]
        if not _ID.match(rid):
            _aside(entry, folder)
            continue
        if answered(data_dir, rid):
            print(f"ask {rid}: already answered - not run again", flush=True)
            _remove(entry, folder)
            continue
        try:
            req = _read(entry, targets, busy)
        except Refused as exc:
            req, why = None, exc.reason
        except Exception as exc:                # deep nesting, bad bytes, a vanished file ...
            req, why = None, f"the request could not be read: {exc.__class__.__name__}"
        if req is None:
            print(f"ask {rid}: refused - {why}", flush=True)
            if write_result(data_dir, result(rid, None, "refused", why, iso(now), iso(now))):
                _remove(entry, folder)
            elif unwritten:
                unwritten(rid)
            continue
        if not write_result(data_dir, result(req.id, req.kind, "running", started=iso(now))):
            if unwritten:
                unwritten(rid)
            return None                          # its file stays: tried again next tick
        _remove(entry, folder)
        # Only the oldest is taken; the rest wait for the next look (and are
        # refused then, while this one runs).
        return req
    return None


def prune(data_dir, now=None, days=PRUNE_DAYS) -> int:
    """Results (data/requests/done/), entries moved aside and the pages
    requests saved (data/dumps/ask-<id>-<n>, _DUMP: never another dump)
    older than `days`, and result temp files older than TMP_STALE_S:
    removed. How many."""
    now = now or time.time()
    cutoff = now - days * 86400
    old = [(p, cutoff) for p in [*(data_dir / "requests" / "done").glob("ask-*.json"),
                                 *(data_dir / "requests" / "rejected").glob("*"),
                                 *(p for p in (data_dir / "dumps").glob("ask-*") if _DUMP.match(p.name))]]
    old += [(p, now - TMP_STALE_S) for p in (data_dir / "requests" / "done").glob("*.tmp")]
    gone = 0
    for path, before in old:
        try:
            if path.lstat().st_mtime >= before:
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            gone += 1
        except OSError as exc:
            print(f"ask: could not prune {path}: {exc}", flush=True)
    return gone


# --- what a read keeps ----------------------------------------------------------
def _path(url: str) -> str:
    """A captured url's path only: its query carries the site's app key."""
    try:
        return urlparse(url or "").path
    except ValueError:
        return ""


def payloads(capture) -> dict:
    """What the tab's capture holds (capture.py: the ring and the pins), as
    payloads.json has it: each body with its kind and its url's path."""
    return {"recent": [{"ts": ts, "kind": kind, "path": _path(url), "text": text}
                       for ts, kind, url, text in list(capture.recent)],
            "pinned": [{"ts": ts, "kind": kind, "path": _path(url), "text": text, "label": label}
                       for ts, kind, url, text, label in list(capture.pinned)]}


def save_page(page, capture, out) -> None:
    """The page as it is drawn (no request): page.url (bare), page.html, a
    full-page page.png, and payloads.json (payloads). Raises on the first
    that fails."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "page.url").write_text(bare(page.url))
    (out / "page.html").write_text(page.content(), encoding="utf-8")
    (out / "payloads.json").write_text(json.dumps(payloads(capture)))
    browser.screenshot(page, out / "page.png", full_page=True)
