"""Which site a request is for: the main domain (Puntmore) or Me.

Puntmore is the product: the main domain serves every public tool, the login
and the API. Me serves Callum's personal tools (`ME_TOOLS`) on its own host.
Both are the same service, the same API and the same accounts; only the
shell's identity, the nav, the landing page and the login differ. Machine
callers keep calling whichever host they use: every API path answers on both
(`docs/decisions.md`, "Names and structure").

Me exists only when the `ME_HOST` setting names its host (me.puntmore.com in
production, me.localhost in development): a request to exactly that host is
Me. Blank (the default, until the host's DNS points at Render) there is one
site, and every tool, the personal ones included, is the main domain's. The
frontend is told the setting (`GET /api/auth/site`) and MIRRORS this in
frontend/src/site.js.

A host whose first label is "shalgo" (the retired site) is sent to the same
path on the main domain before anything else sees it (`ShalgoRedirect`,
mounted in asgi.py), except its `/sw.js`, which retires the service worker
installed from there.
"""
import json
from pathlib import Path
from urllib.parse import quote

from .config import settings

PUNTMORE, ME = "puntmore", "me"

# The tools (grant names) that live on Me when it exists; every other tool is
# the main site's.
ME_TOOLS = ("finder", "pnl", "kelly", "boost", "shop", "aw26")

RETIRED = "shalgo"


def _host_name(host: str | None) -> str:
    return (host or "").split(":", 1)[0].strip().lower()


def me_host() -> str:
    """Me's host name, or "" when Me is off (`ME_HOST` blank)."""
    return _host_name(settings.me_host)


def site_for(host: str | None) -> str:
    me = me_host()
    return ME if me and _host_name(host) == me else PUNTMORE


def tool_site(tool: str) -> str:
    """The site a tool (grant name) lives on: Me's tools are the main site's
    while Me is off."""
    return ME if me_host() and tool in ME_TOOLS else PUNTMORE


def main_domain_base(host: str | None, scheme: str) -> str | None:
    """The main domain's origin for a request to the retired shalgo host
    (scheme, host, the port kept). None for any other host."""
    host = (host or "").strip().lower()
    name, _, port = host.partition(":")
    if "." not in name or name.split(".", 1)[0] != RETIRED:
        return None
    return f"{scheme}://" + name.split(".", 1)[1] + (f":{port}" if port else "")


def main_domain_url(host: str | None, scheme: str, path: str, query: str = "") -> str | None:
    """Where a request to the retired shalgo host goes: the same path and query
    on the main domain. `path` is the path as sent (still percent-encoded).
    None for any other host."""
    base = main_domain_base(host, scheme)
    if base is None:
        return None
    return base + path + (f"?{query}" if query else "")


# Served as /sw.js on the shalgo host in place of a redirect (shalgo_sw.js,
# its MAIN filled in with the main domain's origin). A browser fetches a service
# worker's script itself to update it and refuses a redirected one, so an app
# installed from shalgo can only be released by a worker served from shalgo.
# render/frontend/src/serviceWorkers.test.js runs it against a fake worker scope.
RETIRING_WORKER = (Path(__file__).with_name("shalgo_sw.js")).read_text(encoding="utf-8")


def _scheme(headers: dict, scope: dict) -> str:
    """The scheme the browser used: Render's proxy says so in X-Forwarded-Proto
    (uvicorn is not told to trust it), and only http or https is believed."""
    forwarded = headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if forwarded in ("http", "https"):
        return forwarded
    return scope.get("scheme", "http") if scope.get("scheme") in ("http", "https") else "http"


class ShalgoRedirect:
    """ASGI middleware: a request to shalgo.<domain> is answered with a
    redirect to the same path on <domain>, so old links and home-screen icons
    keep working. GET and HEAD get a 301; anything else a 308, which keeps the
    method and body. `GET /sw.js` there is answered with `RETIRING_WORKER`."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        base = main_domain_base(headers.get("host"), _scheme(headers, scope))
        if base is None:
            return await self.app(scope, receive, send)
        if scope["path"] == "/sw.js" and scope["method"] in ("GET", "HEAD"):
            body = RETIRING_WORKER.replace("__MAIN__", json.dumps(base), 1).encode("utf-8")
            await send({"type": "http.response.start", "status": 200, "headers": [
                (b"content-type", b"application/javascript; charset=utf-8"),
                (b"cache-control", b"no-cache"),
                (b"content-length", str(len(body)).encode("latin-1"))]})
            await send({"type": "http.response.body",
                        "body": b"" if scope["method"] == "HEAD" else body})
            return
        # The path as sent, still percent-encoded: the decoded `path` would turn
        # %3F and %23 into a query and a fragment and cannot hold every character
        # in a header. No root_path is set on this service.
        raw = scope.get("raw_path")
        path = raw.decode("latin-1") if raw else quote(scope["path"])
        to = main_domain_url(headers.get("host"), _scheme(headers, scope), path,
                             scope.get("query_string", b"").decode("latin-1"))
        status = 301 if scope["method"] in ("GET", "HEAD") else 308
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"location", to.encode("latin-1")), (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})

