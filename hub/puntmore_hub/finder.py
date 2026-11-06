"""Finder on the Hub: tips go to Hermes, Hermes posts its verdicts to Render.

Render's Finder (docs/finder-spec.md) keeps, serves and expires the bets; the judging is
Hermes's, here. The intake (the Scraper's drops, inbox.py; tipster emails, mail.py) stores
each tip once; Hermes reads the ones it has not posted yet (`puntmore finder new`), judges
each (offer / ask / reject, the spec's step 2) and posts the rows (`puntmore finder post`).
The placer reports `placed` or `gone` (`puntmore finder status`).

Both posts go over HTTPS to PUNTMORE_RENDER_HTTP with PUNTMORE_RENDER_API_KEY as a Bearer key:
a Render API client key carrying `ingest` (`python -m app.apikeys create <name> --tools ingest`,
in Render's shell), or SAVES_INGEST_KEY. Stdlib only; nothing here retries.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .config import Settings
from .store import Store

TIMEOUT = 30          # seconds; a post is one small JSON body
Opener = Callable[..., Any]


def new_tips(store: Store, limit: int = 50) -> list[dict]:
    """Stored tips with no Finder post yet, oldest first, as the dicts Hermes reads."""
    return [dict(r) for r in store.finder_unposted(limit)]


def _open(req: urllib.request.Request, timeout: float):
    """urllib's urlopen, with an HTTP error answered as a response, not raised."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def _post(cfg: Settings, path: str, body: dict, opener: Opener | None) -> tuple[int, str]:
    base = (cfg.render_http or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("PUNTMORE_RENDER_HTTP is not set: no Render to post to")
    if not cfg.render_api_key:
        raise RuntimeError("PUNTMORE_RENDER_API_KEY is not set: Finder needs a key with the ingest grant")
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {cfg.render_api_key}", "Content-Type": "application/json"})
    resp = (opener or _open)(req, timeout=TIMEOUT)
    code = getattr(resp, "status", None) or getattr(resp, "code", None)
    raw = resp.read()
    return int(code), raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)


def post_rows(cfg: Settings, rows: list[dict], *, opener: Opener | None = None,
              store: Store | None = None) -> dict:
    """POST the rows to /api/finder/rows. On 200 each row is marked posted (with the status it
    carried) and Render's answer is returned: {stored, updated, refused, duplicates}. Any other
    answer raises RuntimeError and marks nothing, so the tips stay new."""
    code, text = _post(cfg, "/api/finder/rows", {"rows": rows}, opener)
    if code != 200:
        raise RuntimeError(f"finder post: HTTP {code} {text[:200]}")
    if store is not None:
        for row in rows:
            store.finder_mark(row["tip_id"], row.get("status") or "")
    return json.loads(text)


def post_status(cfg: Settings, tip_id: str, status: str, at: str, *, opener: Opener | None = None,
                store: Store | None = None) -> dict:
    """The placer's report on an offered row: `placed` or `gone` at `at` (ISO). A row that is
    already final answers 409, returned as {"refused": "final"}; anything else but 200 raises."""
    path = f"/api/finder/rows/{urllib.parse.quote(tip_id, safe='')}/status"
    code, text = _post(cfg, path, {"status": status, "at": at}, opener)
    if code == 409:
        return {"refused": "final"}
    if code != 200:
        raise RuntimeError(f"finder status: HTTP {code} {text[:200]}")
    if store is not None:
        store.finder_mark(tip_id, status)
    return json.loads(text)
