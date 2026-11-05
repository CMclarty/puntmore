"""The Hub link: `/api/2up/ws` (hub/PROTOCOL.md, version 1) and its operator routes.

The socket authenticates on the HANDSHAKE (Bearer key + X-Client-Id, the key
an API client granted `hub` and named as the client id) and is refused with an
HTTP status before it is accepted. Everything after that is `app/hub/service.py`.

The REST routes are for operators, under the 365 tool's grant (`accabonus`):
who is connected, recent tips, and queuing a kill_switch / scrape_job. Nothing
here places a bet or decides what to bet.
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..database import get_db
from ..hub import service
from ..models import ApiClient, Tip
from ..schemas import HubClientOut, HubKillSwitchIn, HubQueuedOut, HubScrapeJobIn, HubTipOut
from ..security import has_permission, require_tool

router = APIRouter(prefix="/2up", tags=["2up"])
operator = [Depends(require_tool("accabonus"))]   # the 365 tool's grant


@router.websocket("/ws")
async def hub_socket(websocket: WebSocket):
    client_id = websocket.headers.get("x-client-id", "").strip()
    key = service.presented_key(websocket.headers)
    conn_app = websocket.app

    def check():
        with service.session_for(conn_app) as db:
            return service.authorise(db, key, client_id)

    refused = await run_in_threadpool(check)
    if refused is not None:
        status, reason = refused
        await _deny(websocket, status, reason)
        return
    await websocket.accept()
    await service.Connection(websocket, client_id, conn_app).run()


async def _deny(websocket: WebSocket, status: int, reason: str) -> None:
    """Refuse the handshake with an HTTP status (PROTOCOL.md: 401 / 403).

    Sent as raw ASGI messages with NO headers, not as a Starlette Response:
    uvicorn's websockets-sansio protocol (what `--ws auto` picks) adds its own
    Content-Type / Content-Length and then appends the response's, and the
    duplicated Content-Length makes the client see "not a valid HTTP
    response" instead of a 401. A server without the denial extension gets a
    close before accept, which every server turns into a 403."""
    if "websocket.http.response" in websocket.scope.get("extensions", {}):
        await websocket.send({"type": "websocket.http.response.start", "status": status, "headers": []})
        await websocket.send({"type": "websocket.http.response.body", "body": f"{reason}\n".encode()})
    else:
        await websocket.close(code=1008, reason=reason)


@router.get("/hub/clients", response_model=list[HubClientOut], dependencies=operator)
def hub_clients(db: Session = Depends(get_db)):
    """Every Hub client seen since this process started: connected or not,
    what its last hello said, when it last sent a frame, frames awaiting ack."""
    return [
        HubClientOut(**{k: getattr(st, k) for k in HubClientOut.model_fields if hasattr(st, k)},
                     unacked=service.unacked_count(db, st.client_id))
        for st in sorted(service.registry.states(), key=lambda s: s.client_id)
    ]


@router.get("/hub/tips", response_model=list[HubTipOut], dependencies=operator)
def hub_tips(
    limit: int = Query(50, ge=1, le=500),
    client_id: str | None = None,
    db: Session = Depends(get_db),
):
    """The most recent candidate tips from the Hubs' Bet Finder sources, as
    they arrived: the wire columns of the one `tips` table (the Bet Finder's
    view of the same rows is /2up/betfinder/tips)."""
    q = db.query(Tip)
    if client_id:
        q = q.filter(Tip.client_id == client_id)
    rows = q.order_by(Tip.received_at.desc(), Tip.id.desc()).limit(limit).all()
    return [
        HubTipOut(tip_id=r.tip_id, client_id=r.client_id, source_id=r.source_id,
                  event=r.event_raw, market=r.market_raw, selection=r.selection_raw,
                  advised_price=r.advised_price, tip_time=r.tip_time_raw,
                  received_at=r.received_at, tip=_body(r))
        for r in rows
    ]


def _body(r: Tip) -> dict:
    try:
        v = json.loads(r.body or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _known_hub(db: Session, client_id: str) -> None:
    row = db.query(ApiClient).filter(ApiClient.name == client_id).first()
    if row is None or not row.enabled or not has_permission(row.permissions, service.GRANT):
        raise HTTPException(status_code=404, detail="no enabled Hub client by that id")


def _queue(db: Session, client_id: str, type_: str, body: dict) -> HubQueuedOut:
    _known_hub(db, client_id)
    try:
        env = service.queue_frame(db, client_id, type_, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return HubQueuedOut(client_id=client_id, type=type_, msg_id=env["msg_id"],
                        connected=service.registry.get(client_id) is not None, body=body)


@router.post("/hub/{client_id}/kill_switch", response_model=HubQueuedOut, dependencies=operator)
def hub_kill_switch(client_id: str, req: HubKillSwitchIn, db: Session = Depends(get_db)):
    """Turn a Hub's kill switch (its file `hub/data/KILL`) ON (PROTOCOL.md
    `kill_switch`, "Kill switches"). Render can turn it on, never off: only
    Cal turns it off, on the box. `on: false` is refused (422), and a Hub
    ignores one anyway. Render's own switches are `/2up/clients/{id}/kill`."""
    if not req.on:
        raise HTTPException(status_code=422, detail="Render turns a Hub's kill switch ON only; "
                                                    "it is turned off on the Hub, by Cal")
    return _queue(db, client_id, "kill_switch", {"on": True, "reason": req.reason})


@router.post("/hub/{client_id}/scrape_job", response_model=HubQueuedOut, dependencies=operator)
def hub_scrape_job(client_id: str, req: HubScrapeJobIn, db: Session = Depends(get_db)):
    """Ask a Hub to run one of its sources now; it answers with scrape_result."""
    body = {"job_id": req.job_id or f"job-{uuid.uuid4().hex[:12]}", "source": req.source,
            "params": {"force": req.force}}
    return _queue(db, client_id, "scrape_job", body)
