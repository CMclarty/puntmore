"""The Hub Console: one page showing what every Hub component is doing, live, plus the kill switch.

    puntmore web                 # or: python -m puntmore_hub.web
    open http://127.0.0.1:8090

Reads need nothing. Actions are POSTs that need the header ``X-Puntmore-Token`` equal to
``PUNTMORE_WEB_TOKEN``; a blank token disables them. The page is plain HTML/CSS/JS shipped in this
package (no build, no CDN), so it loads with the box offline. Every string that came from a scraped
page, a tip, an escalation or a log is put on the page with ``textContent``, and the CSP forbids
inline script, so none of it can run.
"""
from __future__ import annotations

import hmac
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..config import Settings, settings as default_settings
from ..store import Store, iso, utcnow
from . import actions, collect, scraper_behaviour, scraper_targets

STATIC = Path(__file__).resolve().parent / "static"
CACHE_S = {"render": 15.0, "hermes": 4.0, "model": 4.0}   # outside calls: overview and tab share one answer
DEFAULT_CACHE_S = 2.0
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _allowed_hosts(cfg: Settings) -> list[str]:
    """Host headers the console answers to: localhost names, the bound address and this box's name.
    Stops a web page in a browser on this box from reading the console through DNS rebinding."""
    host, _ = cfg.web_host_port
    if host in ("0.0.0.0", "::", ""):
        return ["*"]
    names = {"localhost", "127.0.0.1", "::1", "[::1]", host}
    try:
        hn = socket.gethostname()
        names |= {hn, f"{hn}.local", f"{hn}.lan"}
    except OSError:
        pass
    return sorted(names)


def create_app(cfg: Settings | None = None, *, allowed_hosts: list[str] | None = None) -> FastAPI:
    cfg = cfg or default_settings
    app = FastAPI(title="Puntmore Hub Console", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or _allowed_hosts(cfg))
    cache: dict[str, tuple[float, dict]] = {}
    lock = threading.Lock()

    def store() -> Store:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        return Store(cfg.db_path)

    def get(name: str) -> dict[str, Any]:
        ttl = CACHE_S.get(name, DEFAULT_CACHE_S)
        with lock:
            hit = cache.get(name)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
        out = collect.collect(name, cfg, store)
        with lock:
            cache[name] = (time.monotonic(), out)
        return out

    def forget(*names: str) -> None:
        with lock:
            for n in names or tuple(cache):
                cache.pop(n, None)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ---- reads ------------------------------------------------------------------------------------
    @app.get("/api/overview")
    def overview():
        with ThreadPoolExecutor(max_workers=len(collect.COMPONENTS)) as pool:
            results = dict(zip(collect.COMPONENTS, pool.map(get, collect.COMPONENTS)))
        return {"now": iso(utcnow()), "version": __version__,
                "dry_run": cfg.dry_run, "kill_switch": cfg.kill_switch_on(), "actions_enabled": bool(cfg.web_token),
                "components": {n: {k: r.get(k) for k in ("state", "checked_at", "detail")} for n, r in results.items()}}

    def tab(name: str):
        def route():
            return get(name)
        route.__name__ = f"tab_{name}"
        return route

    for name in collect.COMPONENTS:
        app.get(f"/api/{name}")(tab(name))

    @app.get("/api/scraper/latest/{target}")
    def scraper_latest(target: str):
        try:
            out = collect.latest_rows(cfg, target)
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"latest/{target}.json unreadable: {exc.__class__.__name__}") from None
        if out is None:
            raise HTTPException(404, f"no latest rows for {target}")
        return out

    @app.get("/api/scraper/log/{target}")
    def scraper_log(target: str, n: int = 50):
        try:
            return scraper_targets.log(cfg, target, n)
        except scraper_targets.LibraryMissing as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/api/scraper/behaviour")
    def scraper_behaviour_view():
        return scraper_behaviour.view(cfg)

    # ---- actions ----------------------------------------------------------------------------------
    def authorise(request: Request, action: str) -> None:
        given = request.headers.get("x-puntmore-token", "")
        client = request.client.host if request.client else None
        if not cfg.web_token:
            actions.log_action(cfg, action, "refused: actions disabled (PUNTMORE_WEB_TOKEN blank)", client)
            raise HTTPException(403, "actions are disabled: PUNTMORE_WEB_TOKEN is not set")
        if not hmac.compare_digest(given.encode(), cfg.web_token.encode()):
            actions.log_action(cfg, action, "refused: bad token", client)
            raise HTTPException(403, "wrong or missing X-Puntmore-Token")

    async def body_of(request: Request) -> dict:
        try:
            b = await request.json()
        except Exception:  # noqa: BLE001 - an empty or non-JSON body is an empty body
            return {}
        return b if isinstance(b, dict) else {}

    def run(request: Request, action: str, fn, *invalidate: str) -> JSONResponse:
        client = request.client.host if request.client else None
        try:
            out = fn()
        except actions.ActionError as exc:
            actions.log_action(cfg, action, f"refused: {exc.message}", client)
            return JSONResponse({"ok": False, "message": exc.message, **exc.extra}, status_code=exc.status)
        except Exception as exc:  # noqa: BLE001
            actions.log_action(cfg, action, f"error: {exc.__class__.__name__}: {exc}", client)
            return JSONResponse({"ok": False, "message": f"{exc.__class__.__name__}: {exc}"}, status_code=500)
        actions.log_action(cfg, action, ("ok: " if out.get("ok") else "failed: ") + str(out.get("message", "")), client)
        forget(*invalidate)
        return JSONResponse(out)

    def with_store(fn, *a):
        def go():
            st = store()
            try:
                return fn(cfg, st, *a)
            finally:
                st.close()
        return go

    @app.post("/api/actions/kill-on")
    def kill_on(request: Request):
        authorise(request, "kill on")
        return run(request, "kill on", with_store(actions.kill_on), "placement", "escalations")

    @app.post("/api/actions/kill-off")
    async def kill_off(request: Request):
        authorise(request, "kill off")
        body = await body_of(request)
        return run(request, "kill off", lambda: actions.kill_off(cfg, body), "placement")

    @app.post("/api/actions/escalation-resolve")
    async def escalation_resolve(request: Request):
        authorise(request, "escalation resolve")
        body = await body_of(request)
        return run(request, f"escalation resolve #{body.get('id')}",
                   with_store(actions.escalation_resolve, body), "escalations")

    @app.post("/api/actions/inbox-now")
    def inbox_now(request: Request):
        authorise(request, "inbox now")
        return run(request, "inbox now", with_store(actions.inbox_now), "tips", "daemon", "escalations")

    @app.post("/api/actions/heartbeat-now")
    def heartbeat_now(request: Request):
        authorise(request, "heartbeat now")
        return run(request, "heartbeat now", with_store(actions.heartbeat_now))

    # The Scraper's targets: scraper/targets.py's own functions (scraper_targets.py). A refusal is
    # the library's message (400, with the field it names); its lock held elsewhere is a 409.
    @app.post("/api/actions/scraper/targets/add")
    async def targets_add(request: Request):
        body = await body_of(request)
        label = f"scraper targets add {str(body.get('name') or '?')[:80]}"
        authorise(request, label)
        return run(request, label, lambda: scraper_targets.add(cfg, body), "scraper")

    @app.post("/api/actions/scraper/targets/{name}/set")
    async def targets_set(request: Request, name: str):
        body = await body_of(request)
        label = f"scraper targets set {name} " + " ".join(f"{k}={v}" for k, v in body.items())
        authorise(request, label)
        return run(request, label, lambda: scraper_targets.set_(cfg, name, body), "scraper")

    @app.post("/api/actions/scraper/targets/{name}/enable")
    def targets_enable(request: Request, name: str):
        authorise(request, f"scraper targets enable {name}")
        return run(request, f"scraper targets enable {name}", lambda: scraper_targets.enable(cfg, name), "scraper")

    @app.post("/api/actions/scraper/targets/{name}/disable")
    def targets_disable(request: Request, name: str):
        authorise(request, f"scraper targets disable {name}")
        return run(request, f"scraper targets disable {name}", lambda: scraper_targets.disable(cfg, name), "scraper")

    @app.post("/api/actions/scraper/targets/{name}/remove")
    async def targets_remove(request: Request, name: str):
        body = await body_of(request)
        authorise(request, f"scraper targets remove {name}")
        return run(request, f"scraper targets remove {name}", lambda: scraper_targets.remove(cfg, name, body), "scraper")

    # The Scraper's behaviour: scraper/behaviour.py's own set_ and reset (scraper_behaviour.py). A
    # refusal is the library's message (400, its field the setting's key); its lock held is a 409.
    @app.post("/api/actions/scraper/behaviour/set")
    async def behaviour_set(request: Request):
        body = await body_of(request)
        label = f"scraper behaviour set {scraper_behaviour.said(body.get('key'))[:80]} {scraper_behaviour.said(body.get('value'))[:80]}"
        authorise(request, label)
        return run(request, label, lambda: scraper_behaviour.set_(cfg, body), "scraper")

    @app.post("/api/actions/scraper/behaviour/reset")
    async def behaviour_reset(request: Request):
        body = await body_of(request)
        label = ("scraper behaviour reset --all" if body.get("all") is True
                 else f"scraper behaviour reset {scraper_behaviour.said(body.get('key'))[:80]}")
        authorise(request, label)
        return run(request, label, lambda: scraper_behaviour.reset(cfg, body), "scraper")

    return app


def serve(cfg: Settings | None = None, bind: str | None = None) -> None:
    import uvicorn
    cfg = cfg or default_settings
    if bind:
        cfg = cfg.with_overrides(web_bind=bind)
    host, port = cfg.web_host_port
    if host not in ("127.0.0.1", "localhost", "::1") and not cfg.web_token:
        print(f"WARNING: bound to {host}, which is not localhost, and PUNTMORE_WEB_TOKEN is blank: "
              "anyone who can reach this address can read the console (actions stay disabled).", flush=True)
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning", access_log=False)
