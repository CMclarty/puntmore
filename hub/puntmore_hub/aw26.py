"""AW 26 on the Hub: HubAI reads Callum's slip photos and records the slips on Render.

Render keeps the photo queue and the slips (render/api/app/routers/aw26.py, all under
`/api/aw26/`); it reads no photo. HubAI takes a waiting photo, reads it here on the box, and
records the slip it read, or fails the photo with a reason. Every call goes over HTTPS to
PUNTMORE_RENDER_HTTP with PUNTMORE_RENDER_API_KEY as a Bearer key: a Render API client key
carrying `aw26` (and `cards` for Paddy's board). Stdlib only; nothing here retries.

The Paddy terminal prints times month first, in London time ("10/17/2026, 12:06:07 PM");
`parse_printed` turns one into ISO UTC, and anything it cannot read is refused, never guessed.
"""
from __future__ import annotations

import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .config import Settings

TIMEOUT = 60          # seconds; the largest call is one photo up or down
LONDON = ZoneInfo("Europe/London")
Opener = Callable[..., Any]

_PRINTED = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AP]M)",
                      re.IGNORECASE)
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


class RenderError(RuntimeError):
    """Render answered something other than what the call takes as success."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status} {detail}")
        self.status = status
        self.detail = detail


def parse_printed(text: str) -> str:
    """A time as the terminal prints it, month first in London time ("10/17/2026, 12:06:07 PM";
    the comma and the seconds may be missing), or already ISO 8601 (UTC when it names no
    offset), as ISO UTC "2026-10-17T11:06:07Z". Anything else raises ValueError."""
    s = (text or "").strip()
    m = _PRINTED.fullmatch(s)
    try:
        if m:
            month, day, year, hour, minute, second, half = m.groups()
            hour = int(hour)
            if not 1 <= hour <= 12:
                raise ValueError
            hour = hour % 12 + (12 if half.upper() == "PM" else 0)
            when = datetime(int(year), int(month), int(day), hour, int(minute), int(second or 0),
                            tzinfo=LONDON)
        elif _ISO.match(s):
            when = datetime.fromisoformat(s)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        else:
            raise ValueError
    except ValueError:
        raise ValueError(f"unreadable time: {text!r}") from None
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def configured(cfg: Settings) -> bool:
    """Both PUNTMORE_RENDER_HTTP and PUNTMORE_RENDER_API_KEY are set."""
    return bool((cfg.render_http or "").strip() and (cfg.render_api_key or "").strip())


def _open(req: urllib.request.Request, timeout: float):
    """urllib's urlopen, with an HTTP error answered as a response, not raised."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def _multipart(files: list[tuple[str, bytes, str]], form: dict | None) -> tuple[bytes, str]:
    """A multipart/form-data body: the form's fields, then each file under the name `files`."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in (form or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f"{value}\r\n".encode())
    for filename, data, ctype in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
                     f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def request(cfg: Settings, method: str, path: str, *, body: dict | None = None,
            files: list[tuple[str, bytes, str]] | None = None, form: dict | None = None,
            opener: Opener | None = None) -> tuple[int, bytes, str]:
    """One call to Render: (status, body bytes, content type). A JSON `body`, or multipart when
    `files` are given (with `form`'s fields), or `form` urlencoded. An HTTP error is a status,
    not an exception; a blank PUNTMORE_RENDER_HTTP or PUNTMORE_RENDER_API_KEY is a RuntimeError
    before anything is sent."""
    base = (cfg.render_http or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("PUNTMORE_RENDER_HTTP is not set: no Render to call")
    key = (cfg.render_api_key or "").strip()
    if not key:
        raise RuntimeError("PUNTMORE_RENDER_API_KEY is not set: AW 26 needs a key with the aw26 grant")
    headers = {"Authorization": f"Bearer {key}"}
    data = None
    if files:
        data, headers["Content-Type"] = _multipart(files, form)
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif form:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    resp = (opener or _open)(req, timeout=TIMEOUT)
    code = getattr(resp, "status", None) or getattr(resp, "code", None)
    raw = resp.read()
    resp_headers = getattr(resp, "headers", None)
    ctype = (resp_headers.get("Content-Type") if resp_headers is not None else None) or ""
    return int(code), raw if isinstance(raw, bytes) else str(raw).encode(), ctype


# ---- the commands HubAI calls (`puntmore aw26 ...`) -------------------------------------------

# What Render's AwSlipRecordIn and AwSlipLegIn take (render/api/app/schemas.py); anything else
# HubAI read off a slip (a printed total, max return, bonus) is dropped, never sent.
SLIP_KEYS = ("receipt", "placed_at", "offer", "stake", "legs", "photo_id")
LEG_KEYS = ("event", "market", "selection", "odds", "kickoff", "fair_odds", "kind", "line", "side",
            "fixture_id", "fair_assumed")
EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
BET_KEYS = ("market", "line", "side", "pick", "odds", "fair")


def _detail(raw: bytes) -> str:
    """Render's `detail`, or the body's first 200 characters."""
    text = raw.decode(errors="replace")
    try:
        detail = json.loads(text).get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) else (json.dumps(detail) if detail else text[:200])


def _json(cfg: Settings, method: str, path: str, opener: Opener | None, **kw) -> Any:
    """A call that succeeds on 200 only, answered as its JSON; anything else is a RenderError."""
    code, raw, _ = request(cfg, method, path, opener=opener, **kw)
    if code != 200:
        raise RenderError(code, _detail(raw))
    return json.loads(raw)


def photos(cfg: Settings, opener: Opener | None = None) -> list[dict]:
    """Every photo on the queue not done yet, oldest first."""
    return _json(cfg, "GET", "/api/aw26/photos", opener)


def take(cfg: Settings, photo_id: int, dest_dir: Path, opener: Opener | None = None) -> Path:
    """Take a waiting photo (once; Render refuses a second take with 409), then save its image
    to `dest_dir/<id><ext>`."""
    _json(cfg, "POST", f"/api/aw26/photos/{int(photo_id)}/take", opener)
    code, raw, ctype = request(cfg, "GET", f"/api/aw26/photos/{int(photo_id)}/image", opener=opener)
    if code != 200:
        raise RenderError(code, _detail(raw))
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{int(photo_id)}{EXTS.get(ctype.split(';')[0].strip().lower(), '.img')}"
    path.write_bytes(raw)
    return path


def fail(cfg: Settings, photo_id: int, reason: str, opener: Opener | None = None) -> dict:
    """A photo HubAI could not make a slip of: back on the queue while tries are left."""
    return _json(cfg, "POST", f"/api/aw26/photos/{int(photo_id)}/fail", opener, body={"reason": reason})


def upload(cfg: Settings, path: Path, source: str = "telegram", opener: Opener | None = None) -> dict:
    """Put one photo on the queue; Render's row for it."""
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    rows = _json(cfg, "POST", "/api/aw26/photos", opener,
                 files=[(path.name, path.read_bytes(), ctype)], form={"source": source})
    return rows[0]


def board(cfg: Settings, at: str, match: str | None = None, opener: Opener | None = None) -> list[dict]:
    """Paddy's board as it stood at `at` (printed or ISO), each game compact. With `match`, only
    the games whose home, away or match name holds a word of four letters or more from it."""
    query = urllib.parse.urlencode({"at": parse_printed(at)})
    games = _json(cfg, "GET", f"/api/paddy/board-at?{query}", opener)
    if match is not None:
        words = [w for w in re.findall(r"\w+", match.lower()) if len(w) >= 4]
        games = [g for g in games
                 if any(w in (g.get(k) or "").lower() for w in words for k in ("home", "away", "match"))]
    return [{"event_id": g["event_id"], "match": g["match"], "commence_time": g["commence_time"],
             "bets": [{k: b.get(k) for k in BET_KEYS} for b in g.get("bets") or []]} for g in games]


def _normalise(slip: dict) -> dict:
    """The body Render takes: its keys only, every time in ISO UTC. ValueError names the field."""
    body = {k: slip[k] for k in SLIP_KEYS if k in slip}
    try:
        body["placed_at"] = parse_printed(slip.get("placed_at") or "")
    except ValueError as exc:
        raise ValueError(f"placed_at: {exc}") from None
    legs = []
    for i, leg in enumerate(slip.get("legs") or []):
        out = {k: leg[k] for k in LEG_KEYS if k in leg}
        if (leg.get("kickoff") or "").strip():
            try:
                out["kickoff"] = parse_printed(leg["kickoff"])
            except ValueError as exc:
                raise ValueError(f"legs[{i}].kickoff: {exc}") from None
        legs.append(out)
    body["legs"] = legs
    return body


def record(cfg: Settings, slip: dict, *, dry_run: bool = False, opener: Opener | None = None) -> dict:
    """Record the slip HubAI read: {"recorded": <Render's slip>}, or {"duplicate": <detail>} when
    the receipt is already in or the photo is already a slip (both success). An unreadable time
    is a ValueError before anything is sent; any other refusal a RenderError. `dry_run` answers
    {"dry_run": <the body>} and sends nothing."""
    body = _normalise(slip)
    if dry_run:
        return {"dry_run": body}
    code, raw, _ = request(cfg, "POST", "/api/aw26/slips/record", body=body, opener=opener)
    if code == 200:
        return {"recorded": json.loads(raw)}
    if code == 409:
        return {"duplicate": _detail(raw)}
    raise RenderError(code, _detail(raw))
