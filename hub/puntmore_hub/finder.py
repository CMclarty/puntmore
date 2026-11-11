"""Finder on the Hub: tips go to HubAI, HubAI posts its verdicts to Render.

Render's Finder (docs/finder-spec.md) keeps, serves and expires the rows; the judging is
HubAI's, here. The intake (the Scraper's drop files, inbox.py; tipster emails, mail.py) stores
each tip once; HubAI reads the ones it has not posted yet (`puntmore finder new`), judges
each (offer / ask / reject, the spec's step 2) and posts the rows (`puntmore finder post`); a tip
it cannot post at all it skips (`puntmore finder skip`), so the list moves on. The Bet Placer reports
`placed` or `gone` (`puntmore finder status`). What the Hub records for a posted tip is the status
Render says it stored, never the one HubAI sent.

`post_json` is the Hub's one Render client, used by Finder and by the inbox's price pass
(inbox.py posts the Scraper's Paddy price drops through it). Every post goes over HTTPS to
PUNTMORE_RENDER_HTTP with PUNTMORE_RENDER_API_KEY as a Bearer key: a Render API client key
carrying `ingest` (`python -m app.apikeys create <name> --tools ingest`, in Render's shell), or
SAVES_INGEST_KEY. Stdlib only; nothing here retries.
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
    """Stored tips with no Finder post yet, oldest first, as the dicts HubAI reads."""
    return [dict(r) for r in store.finder_unposted(limit)]


def skip(store: Store, tip_id: str, reason: str | None = None) -> dict:
    """Mark a tip HubAI cannot post (another sport, nothing readable) as `skipped`, so it leaves
    `new_tips` and never reaches Render. ValueError: an unknown tip, or one already posted (it is
    on Render; withdraw it there by re-posting it `rejected`). Skipping a skipped tip again
    replaces its reason."""
    if not store.tip_exists(tip_id):
        raise ValueError(f"unknown tip {tip_id}")
    status = store.finder_status(tip_id)
    if status not in (None, "skipped"):
        raise ValueError(f"tip {tip_id} is already posted as {status}")
    store.finder_mark(tip_id, "skipped", reason)
    return {"tip_id": tip_id, "status": "skipped"}


def _open(req: urllib.request.Request, timeout: float):
    """urllib's urlopen, with an HTTP error answered as a response, not raised."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def post_json(cfg: Settings, path: str, body: dict, opener: Opener | None = None) -> tuple[int, str]:
    """POST `body` as JSON to PUNTMORE_RENDER_HTTP + `path` and return (HTTP code, answer text).
    RuntimeError when PUNTMORE_RENDER_HTTP or PUNTMORE_RENDER_API_KEY is blank; a network error
    or timeout propagates. It never retries."""
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
    """POST the rows to /api/finder/rows and return Render's answer: {stored, updated, refused,
    duplicates, rows}. On 200 each tip in the answer's `rows` is marked with the status Render
    stored for it (an offer stored as a duplicate is `rejected`, a withdrawal of a placed row is
    `placed`); a tip the answer leaves out or gives no status (no row on Render) stays new. Any
    other answer raises RuntimeError and marks nothing, so the tips stay new."""
    code, text = post_json(cfg, "/api/finder/rows", {"rows": rows}, opener)
    if code != 200:
        raise RuntimeError(f"finder post: HTTP {code} {text[:200]}")
    answer = json.loads(text)
    if store is not None:
        for row in answer.get("rows") or []:
            if row.get("status"):
                store.finder_mark(row["tip_id"], row["status"])
    return answer


def post_status(cfg: Settings, tip_id: str, status: str, at: str, *, opener: Opener | None = None,
                store: Store | None = None) -> dict:
    """The Bet Placer's report on an offered row: `placed` or `gone` at `at` (ISO). A row that is
    already final answers 409, returned as {"refused": "final"}; anything else but 200 raises."""
    path = f"/api/finder/rows/{urllib.parse.quote(tip_id, safe='')}/status"
    code, text = post_json(cfg, path, {"status": status, "at": at}, opener)
    if code == 409:
        return {"refused": "final"}
    if code != 200:
        raise RuntimeError(f"finder status: HTTP {code} {text[:200]}")
    if store is not None:
        store.finder_mark(tip_id, status)
    return json.loads(text)
