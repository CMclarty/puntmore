"""Passive capture of what a kept-open page receives.

The page's own scripts fetch and push prices; this listens. Every JSON/text
response and websocket frame goes to:

  recent   a short in-memory ring, always on, whatever the disk does. This is
           what parsers read.
  pinned   the payloads that arrived just after `mark()` (a market-tab
           click), kept apart so the market list a click brought in is still
           there after the ring has rolled over.
  disk     data/samples/<run>/<tab>/, so parsers can be written from real
           payloads. Each TAB has its own budget (bytes and files) inside the
           run's ceiling, so the first busy tab cannot starve the rest: every
           tab keeps its first distinct payloads. A repeat of the same URL with
           the same size is not written again (a polled price feed repeats
           every few seconds), and third-party hosts (consent banners, stats
           widgets) are not written at all. The main document's HTML is kept
           once per tab, so how the page draws its tabs can be read afterwards.

`last_activity` is what decides whether a tab has gone stale: a kept-open
page that has stopped receiving anything is reloaded - once, and only then.
A refusal or an error is not activity.

`refusals` records every 403/429 the page's OWN requests get (on the site's
hosts - a third-party widget's 403 says nothing about us). A kept-open page
makes no page loads, so a block shows up here first, not as a load failing.
"""
import collections
import itertools
import json
import pathlib
import re
import time

from .config import host_allowed

_TEXTUAL = re.compile(r"json|text/plain|javascript|graphql", re.I)
_MIN_BYTES = 200          # skip beacons and empty acks
_MAX_FILE = 2 * 1024 * 1024
PIN_WINDOW_S = 20         # how long after mark() a payload is pinned


class DiskBudget:
    """The run's ceiling on sample bytes, shared by every tab, plus what each
    tab may use of it."""

    def __init__(self, mb: float, tab_mb: float = 8.0, tab_files: int = 400):
        self.left = int(mb * 1024 * 1024)
        self.tab_bytes = int(tab_mb * 1024 * 1024)
        self.tab_files = tab_files

    def take(self, n: int) -> bool:
        if n > self.left:
            return False
        self.left -= n
        return True


class Capture:
    def __init__(self, page, outdir: pathlib.Path, budget: DiskBudget, ring: int = 200):
        self.outdir = outdir
        self.budget = budget
        self.recent = collections.deque(maxlen=ring)    # (ts, kind, url, text)
        self.pinned = collections.deque(maxlen=80)      # (ts, kind, url, text, label)
        self.last_activity = time.time()
        self.status = None                              # last main-document status
        self.documents = 0                              # main-document responses seen
        self.refusals = collections.deque(maxlen=50)    # (ts, status, url)
        self._n = itertools.count()
        self._seen: set = set()                         # (url, bytes) already on disk
        self._tab_bytes = 0
        self._tab_files = 0
        self._mark = (0.0, None)                        # (ts, label)
        self._kept_html = False
        outdir.mkdir(parents=True, exist_ok=True)
        page.on("response", self._on_response)
        page.on("websocket", self._on_ws)

    # --- disk ---------------------------------------------------------------
    def _write(self, kind, url, data: bytes, ts, suffix="txt") -> bool:
        if not host_allowed(url):
            return False
        key = (url, len(data))
        if key in self._seen:
            return False
        if (self._tab_files >= self.budget.tab_files
                or self._tab_bytes + len(data) > self.budget.tab_bytes
                or not self.budget.take(len(data))):
            return False
        if len(self._seen) < 20_000:
            self._seen.add(key)
        self._tab_bytes += len(data)
        self._tab_files += 1
        n = next(self._n)
        (self.outdir / f"{n:05d}-{kind}.{suffix}").write_bytes(data)
        row = {"n": n, "kind": kind, "url": url, "ts": ts, "bytes": len(data)}
        if self._mark[1] and ts - self._mark[0] <= PIN_WINDOW_S:
            row["after"] = self._mark[1]
        with (self.outdir / "index.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        return True

    def mark(self, label: str):
        """Something is about to happen on the page (a market-tab click):
        what arrives in the next PIN_WINDOW_S is pinned and labelled, in
        memory and in index.jsonl."""
        self._mark = (time.time(), label)
        with (self.outdir / "index.jsonl").open("a") as f:
            f.write(json.dumps({"mark": label, "ts": self._mark[0]}) + "\n")

    def since(self, ts: float) -> list[tuple]:
        """What arrived at or after `ts` (from the ring)."""
        return [r for r in self.recent if r[0] >= ts]

    def _keep(self, kind, url, text):
        now = time.time()
        self.last_activity = now
        self.recent.append((now, kind, url, text))
        if self._mark[1] and now - self._mark[0] <= PIN_WINDOW_S:
            self.pinned.append((now, kind, url, text, self._mark[1]))
        self._write(kind, url, text.encode("utf-8", "replace")[:_MAX_FILE], now)

    def _on_response(self, resp):
        try:
            if resp.status in (403, 429) and host_allowed(resp.url):
                self.refusals.append((time.time(), resp.status, resp.url))
            main_doc = (resp.request.resource_type == "document"
                        and resp.frame == resp.frame.page.main_frame)
            if main_doc:
                self.status = resp.status
                self.documents += 1
            if resp.status >= 300:
                return                               # refusals, errors, redirects: not activity
            if main_doc and not self._kept_html:
                self._kept_html = True
                self._write("doc", resp.url, resp.body()[:_MAX_FILE], time.time(),
                            suffix="html")
            if resp.request.resource_type not in ("xhr", "fetch", "document"):
                return
            if not _TEXTUAL.search(resp.headers.get("content-type", "")):
                return
            text = resp.text()
        except Exception:
            return                                   # aborted requests, closed pages
        if len(text) >= _MIN_BYTES:
            self._keep("http", resp.url, text)

    def _on_ws(self, ws):
        def frame(payload):
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", "replace")
            if len(payload) >= _MIN_BYTES:
                self._keep("ws", ws.url, payload)
        ws.on("framereceived", frame)

    def refused_within(self, seconds: float) -> int:
        cutoff = time.time() - seconds
        return sum(1 for ts, _, _ in self.refusals if ts >= cutoff)

    def quiet_for(self, now=None) -> float:
        return (now or time.time()) - self.last_activity
