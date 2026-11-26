"""Requests for the running service, answered while it runs: `python -m scraper ask`.

A request is one JSON file, data/requests/ask-<id>.json, written atomically
(the CLI writes a dot file and renames it). The running loop takes the
oldest (`pending`), and answers it in data/requests/done/ask-<id>.json:
`running` as soon as it is taken, with the items done so far, and `done`,
`refused`, `stopped` or `interrupted` at the end. One request runs at a
time: one that arrives while another runs is refused ("another request is
running"). A result already there for an id is never run again, so a
request is never started twice.

One kind so far:

  read   load Paddy Power match pages, one at a time, each in a tab of its
         own, run a parser over what arrived, close the tab. The pages are
         named by a Paddy matches job and a kick-off range ("job", "from",
         "to": the match links on that job's open listings whose kick-off is
         in the range; no listing is loaded for it) or by address ("urls",
         at most MAX_URLS). "parser": a name in parsers.PARSERS, or null.
         "keep": true saves each page (HTML, screenshot, its captured
         payloads as payloads.json) to data/dumps/ask-<id>-<n>/; a page
         whose parser failed is saved whatever keep says.

         A requested load is recorded with the guard as requested
         (guard.py): it does not count against the site's hourly and daily
         budget, and it skips quiet hours, but it keeps the spacing between
         loads (the pacer), the hosts rule, and stops at once on a cool-off
         or back-off (Callum, 26 Nov 2026: docs/decisions.md). A block on a
         requested load is a block like any other.

What happens in the browser is Runner._ask's; this module is the files.
"""
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import browser
from . import config
from . import login
from .parsers import PARSERS

KINDS = ("read",)
MAX_URLS = 60
FIELDS = {"id", "kind", "job", "from", "to", "urls", "parser", "keep"}
LONDON = ZoneInfo("Europe/London")
_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ANOTHER = "another request is running"
INTERRUPTED = "the Scraper restarted mid-request; the items listed were done"


class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Request:
    id: str
    kind: str
    job: str | None             # a Paddy matches job, with start and end ...
    start: float | None         # epoch seconds ("from")
    end: float | None           # epoch seconds ("to")
    urls: tuple[str, ...]       # ... or the pages by address
    parser: str | None
    keep: bool


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


def parse(raw: dict, targets=None) -> Request:
    """The request, checked for shape; Refused says why not. With `targets`
    (the CLI's and the runner's), a job must be one of them: a Paddy Power
    matches job."""
    if not isinstance(raw, dict):
        raise Refused("the request is not a JSON object")
    extra = sorted(set(raw) - FIELDS)
    if extra:
        raise Refused(f"unknown field(s): {', '.join(extra)}")
    rid = raw.get("id")
    if not isinstance(rid, str) or not _ID.match(rid):
        raise Refused("id must be 1-64 letters, digits, '.', '_' or '-'")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise Refused(f"unknown kind {kind!r} (known: {', '.join(KINDS)})")
    parser = raw.get("parser")
    if parser is not None and (not isinstance(parser, str) or parser not in PARSERS):
        raise Refused(f"no parser called {parser!r}")
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
                raise Refused(f"not a Paddy Power page: {u}")
        urls = tuple(urls)
    if parser is None and not keep:
        raise Refused("nothing to do: no parser, and keep is false")
    return Request(rid, kind, job, start, end, urls, parser, keep)


# --- the files ------------------------------------------------------------------
def requests_dir(data_dir):
    return data_dir / "requests"


def result_path(data_dir, rid: str):
    return data_dir / "requests" / "done" / f"ask-{rid}.json"


def result(req_id: str, kind, state: str, reason=None, started=None, finished=None, items=()) -> dict:
    return {"id": req_id, "kind": kind, "state": state, "reason": reason,
            "started": started, "finished": finished, "items": list(items)}


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
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"ask {body.get('id')}: result not written: {exc}", flush=True)
        return False


def read_result(data_dir, rid: str) -> dict | None:
    try:
        return json.loads(result_path(data_dir, rid).read_text())
    except (OSError, ValueError):
        return None


def running(data_dir) -> list[str]:
    """The ids of the results that say `running`."""
    out = []
    for f in sorted((data_dir / "requests" / "done").glob("ask-*.json")):
        try:
            if json.loads(f.read_text()).get("state") == "running":
                out.append(f.name[len("ask-"):-len(".json")])
        except (OSError, ValueError, AttributeError):
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


def _refuse(data_dir, rid, kind, reason, now):
    print(f"ask {rid}: refused - {reason}", flush=True)
    write_result(data_dir, result(rid, kind, "refused", reason, iso(now), iso(now)))


def pending(data_dir, targets=None, now=None) -> Request | None:
    """The oldest request waiting, taken: its result written `running` and its
    file removed. Every request that cannot run is answered `refused` and
    removed on the way: one that does not parse (parse; `targets` as there),
    whose id is not its file's, that was answered before, or that arrived
    while another request runs (then None)."""
    now = now or time.time()
    folder = data_dir / "requests"
    try:
        files = sorted(folder.glob("ask-*.json"), key=lambda f: (f.stat().st_mtime, f.name))
    except OSError:
        return None
    if not files:                           # the common tick: no result read
        return None
    busy = bool(running(data_dir))
    for f in files:
        rid = f.name[len("ask-"):-len(".json")]
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            text, why = None, f"the request could not be read: {exc}"
        f.unlink(missing_ok=True)
        if not _ID.match(rid):
            print(f"ask: {f.name} dropped - not a request id", flush=True)
            continue
        if read_result(data_dir, rid) is not None:
            print(f"ask {rid}: already answered - not run again", flush=True)
            continue
        kind = None
        try:
            if text is None:
                raise Refused(why)
            try:
                raw = json.loads(text)
            except ValueError as exc:
                raise Refused(f"the request is not JSON: {exc}") from None
            kind = raw.get("kind") if isinstance(raw, dict) else None
            req = parse(raw, targets=targets)
            if req.id != rid:
                raise Refused(f"the id {req.id!r} is not the file's ({rid!r})")
            if busy:
                raise Refused(ANOTHER)
        except Refused as exc:
            _refuse(data_dir, rid, kind, exc.reason, now)
            continue
        if not write_result(data_dir, result(req.id, req.kind, "running", started=iso(now))):
            continue                         # no result to say it ran: not run
        # Only the oldest is taken; the rest wait for the next look (and are
        # refused then, while this one runs).
        return req
    return None


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
    """The page as it is drawn (no request): page.url, page.html, a full-page
    page.png, and payloads.json (payloads). Raises on the first that fails."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "page.url").write_text(login.safe_url(page.url))
    (out / "page.html").write_text(page.content(), encoding="utf-8")
    (out / "payloads.json").write_text(json.dumps(payloads(capture)))
    browser.screenshot(page, out / "page.png", full_page=True)
