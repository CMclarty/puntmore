"""Which site a request is for: the main domain (Puntmore) or Me.

Puntmore is the product: the main domain serves every public tool, the login
and the API. Me (`me.<the main domain>`) serves Callum's personal tools.
Both are the same service, the same API and the same accounts; only the
shell's identity, the nav and the landing page differ. Machine callers keep
calling whichever host they use: every API path answers on both
(`docs/decisions.md`, "Names and structure").

The rule is the host name and nothing else, no env var: a host whose first
label is "me" is Me (me.puntmore.com in production, me.localhost in
development). A host whose first label is "shalgo" (the retired site) is sent
to the same path on the main domain before anything else sees it
(`ShalgoRedirect`, mounted in asgi.py). MIRRORED in frontend/src/site.js.
"""
PUNTMORE, ME = "puntmore", "me"

# The tools (grant names) that live on Me; every other tool is the main site's.
ME_TOOLS = ("finder", "pnl", "kelly", "boost", "shop")

RETIRED = "shalgo"


def site_for(host: str | None) -> str:
    name = (host or "").split(":", 1)[0].strip().lower()
    label = name.split(".", 1)[0] if "." in name else ""
    return ME if label == ME else PUNTMORE


def tool_site(tool: str) -> str:
    """The site a tool (grant name) lives on."""
    return ME if tool in ME_TOOLS else PUNTMORE


def main_domain_url(host: str | None, scheme: str, path: str, query: str = "") -> str | None:
    """Where a request to the retired shalgo host goes: the same path and query
    on the main domain, the port kept. None for any other host."""
    host = (host or "").strip().lower()
    name, _, port = host.partition(":")
    if "." not in name or name.split(".", 1)[0] != RETIRED:
        return None
    base = name.split(".", 1)[1] + (f":{port}" if port else "")
    return f"{scheme}://{base}{path}" + (f"?{query}" if query else "")


class ShalgoRedirect:
    """ASGI middleware: a request to shalgo.<domain> is answered with a
    redirect to the same path on <domain>, so old links and home-screen icons
    keep working. GET and HEAD get a 301; anything else a 308, which keeps the
    method and body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        scheme = headers.get("x-forwarded-proto", scope.get("scheme", "http")).split(",")[0].strip()
        to = main_domain_url(headers.get("host"), scheme, scope.get("root_path", "") + scope["path"],
                             scope.get("query_string", b"").decode("latin-1"))
        if to is None:
            return await self.app(scope, receive, send)
        status = 301 if scope["method"] in ("GET", "HEAD") else 308
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"location", to.encode("latin-1")), (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})
