"""Passive capture of what a kept-open page receives.

The page's own scripts fetch and push prices; this listens. Every JSON/text
response and websocket frame is (a) kept in a short in-memory ring for the
parser and (b) written to data/samples/<run>/<tab>/ until the run's disk cap,
so the parser can be written from real payloads.

`last_activity` is what decides whether a tab has gone stale: a kept-open
page that has stopped receiving anything is reloaded — once, and only then.

`refusals` records every 403/429 the page's OWN requests get. A kept-open page
makes no page loads, so a block shows up here first, not as a load failing.
"""
import collections
import itertools
import json
import pathlib
import re
import time

_TEXTUAL = re.compile(r"json|text/plain|javascript|graphql", re.I)
_MIN_BYTES = 200          # skip beacons and empty acks
_MAX_FILE = 2 * 1024 * 1024


class DiskBudget:
    def __init__(self, mb: float):
        self.left = int(mb * 1024 * 1024)

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
        self.last_activity = time.time()
        self.status = None                              # last main-document status
        self.refusals = collections.deque(maxlen=50)    # (ts, status, url)
        self._n = itertools.count()
        outdir.mkdir(parents=True, exist_ok=True)
        page.on("response", self._on_response)
        page.on("websocket", self._on_ws)

    def _keep(self, kind, url, text):
        self.last_activity = time.time()
        self.recent.append((self.last_activity, kind, url, text))
        data = text.encode("utf-8", "replace")[:_MAX_FILE]
        if self.budget.take(len(data)):
            n = next(self._n)
            (self.outdir / f"{n:05d}-{kind}.txt").write_bytes(data)
            with (self.outdir / "index.jsonl").open("a") as f:
                f.write(json.dumps({"n": n, "kind": kind, "url": url,
                                    "ts": self.last_activity, "bytes": len(data)}) + "\n")

    def _on_response(self, resp):
        try:
            if resp.status in (403, 429):
                self.refusals.append((time.time(), resp.status, resp.url))
            if resp.request.resource_type == "document" and resp.frame == resp.frame.page.main_frame:
                self.status = resp.status
            if resp.request.resource_type not in ("xhr", "fetch", "document"):
                return
            if not _TEXTUAL.search(resp.headers.get("content-type", "")):
                return
            text = resp.text()
        except Exception:
            return                                   # redirects, aborted requests
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
