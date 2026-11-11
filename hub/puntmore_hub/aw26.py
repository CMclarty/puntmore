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
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
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
