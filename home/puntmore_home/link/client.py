"""WebSocket client to Render with reconnect, a durable outbox, and idempotent inbound handling."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ..config import Settings, settings as default_settings
from ..schemas import Bet, Envelope, ScrapeJob
from ..store import Store

log = logging.getLogger("puntmore.link")

PROTOCOL_VERSION = 1   # see home/PROTOCOL.md and schemas/protocol.schema.json
Handler = Callable[[Envelope], Awaitable[None] | None]


class RenderLink:
    def __init__(self, store: Store, cfg: Settings = default_settings, handlers: dict[str, Handler] | None = None):
        self.store = store
        self.cfg = cfg
        self.handlers: dict[str, Handler] = handlers or {}
        self._ws = None
        self._stop = asyncio.Event()

    # ---- outbound --------------------------------------------------------------------------------
    def enqueue(self, type_: str, body: dict) -> str:
        """Queue a message. It survives restarts and is sent when the socket is up."""
        return self.store.outbox_add(type_, body)

    async def _flush_outbox(self) -> None:
        while not self._stop.is_set():
            if self._ws is not None:
                for row in self.store.outbox_pending():
                    env = Envelope(type=row["type"], client_id=self.cfg.client_id, msg_id=row["msg_id"],
                                   body=__import__("json").loads(row["body"]))
                    try:
                        await self._ws.send(env.to_json())
                        self.store.outbox_mark_sent(row["msg_id"])
                    except Exception as exc:  # noqa: BLE001
                        log.warning("send failed, will retry: %s", exc)
                        break
            await asyncio.sleep(1.0)

    # ---- inbound ---------------------------------------------------------------------------------
    async def handle(self, env: Envelope) -> None:
        if env.type in ("ack", "hello_ack"):
            return
        if env.type == "ping":
            self.enqueue("pong", {"msg_id": env.msg_id})
            return
        if self.store.inbox_seen(env.msg_id):
            log.info("replayed message %s ignored", env.msg_id)
            self.enqueue("ack", {"msg_id": env.msg_id, "replay": True})
            return
        handler = self.handlers.get(env.type)
        if handler is None:
            log.warning("no handler for inbound type %r", env.type)
        else:
            result = handler(env)
            if asyncio.iscoroutine(result):
                await result
        self.enqueue("ack", {"msg_id": env.msg_id})

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                env = Envelope.from_json(raw)
            except Exception as exc:  # noqa: BLE001
                log.warning("bad frame ignored: %s", exc)
                continue
            try:
                await self.handle(env)
            except Exception as exc:  # noqa: BLE001
                log.exception("handler for %s failed: %s", env.type, exc)

    # ---- lifecycle -------------------------------------------------------------------------------
    async def run(self) -> None:
        import websockets

        if not self.cfg.link_configured:
            log.warning("Render link not configured (PUNTMORE_RENDER_WS / PUNTMORE_RENDER_API_KEY); running offline")
            await self._stop.wait()
            return
        backoff = 2.0
        headers = {"Authorization": f"Bearer {self.cfg.render_api_key}", "X-Client-Id": self.cfg.client_id}
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.cfg.render_ws_url, additional_headers=headers,
                                              ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024) as ws:
                    self._ws = ws
                    backoff = 2.0
                    hello = Envelope(type="hello", client_id=self.cfg.client_id, body=self.hello_body())
                    await ws.send(hello.to_json())
                    log.info("connected to %s", self.cfg.render_ws_url)
                    flusher = asyncio.create_task(self._flush_outbox())
                    try:
                        await self._recv_loop()
                    finally:
                        flusher.cancel()
                        self._ws = None
            except Exception as exc:  # noqa: BLE001
                log.warning("link down (%s); reconnecting in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def hello_body(self) -> dict:
        from .. import __version__
        return {"protocol_version": PROTOCOL_VERSION, "dry_run": self.cfg.dry_run,
                "kill_switch": self.cfg.kill_switch_on(), "version": __version__}

    def stop(self) -> None:
        self._stop.set()


async def check(url: str, api_key: str, client_id: str = "owner", timeout: float = 10.0) -> tuple[bool, str]:
    """Connect to a Render, send hello, wait for hello_ack. For testing a new Render build."""
    import websockets

    headers = {"Authorization": f"Bearer {api_key}", "X-Client-Id": client_id}
    try:
        async with websockets.connect(url, additional_headers=headers, open_timeout=timeout) as ws:
            await ws.send(Envelope(type="hello", client_id=client_id,
                                   body={"protocol_version": PROTOCOL_VERSION, "dry_run": True, "kill_switch": False}).to_json())
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            env = Envelope.from_json(raw)
            if env.type != "hello_ack":
                return False, f"expected hello_ack, got {env.type}"
            pv = env.body.get("protocol_version")
            if pv != PROTOCOL_VERSION:
                return False, f"protocol_version {pv} != {PROTOCOL_VERSION}"
            return True, f"hello_ack ok (server_time {env.body.get('server_time')})"
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"


# ---- helpers for handlers -----------------------------------------------------------------------

def bets_from(env: Envelope) -> list[Bet]:
    return [Bet.from_dict(b) for b in env.body.get("bets", [])]


def job_from(env: Envelope) -> ScrapeJob:
    return ScrapeJob.from_dict(env.body)
