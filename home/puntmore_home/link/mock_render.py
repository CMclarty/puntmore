"""A minimal Render for tests and for anyone building the real one.

Implements exactly what PROTOCOL.md requires of the server side: bearer-key auth on the
handshake, `hello` → `hello_ack`, an `ack` for every non-control message, and a way to push
`bet_list` / `scrape_job` / `kill_switch` frames to the connected client. Nothing else.
Run it standalone to poke at the home box by hand:

    python -m puntmore_home.link.mock_render --port 8765 --key test-key
"""
from __future__ import annotations

import argparse
import asyncio
import http
import logging
from typing import Callable

from ..schemas import Envelope, utcnow_iso

log = logging.getLogger("puntmore.mock_render")


class MockRender:
    def __init__(self, api_key: str = "test-key", protocol_version: int = 1):
        self.api_key = api_key
        self.protocol_version = protocol_version
        self.received: list[Envelope] = []
        self.clients: set = set()
        self.server = None
        self.port: int | None = None

    # ---- lifecycle -------------------------------------------------------------------------------
    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        from websockets.asyncio.server import serve

        self.server = await serve(self._handler, host, port, process_request=self._auth)
        self.port = self.server.sockets[0].getsockname()[1]
        log.info("mock render listening on ws://%s:%s", host, self.port)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    # ---- protocol --------------------------------------------------------------------------------
    def _auth(self, connection, request):
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {self.api_key}":
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "bad client key\n")
        return None

    async def _handler(self, ws) -> None:
        self.clients.add(ws)
        try:
            async for raw in ws:
                try:
                    env = Envelope.from_json(raw)
                except Exception as exc:  # noqa: BLE001
                    log.warning("bad frame: %s", exc)
                    continue
                self.received.append(env)
                if env.type == "hello":
                    await ws.send(Envelope(type="hello_ack", client_id=env.client_id,
                                           body={"protocol_version": self.protocol_version,
                                                 "server_time": utcnow_iso()}).to_json())
                elif env.type not in ("ack", "pong"):
                    await ws.send(Envelope(type="ack", client_id=env.client_id, body={"msg_id": env.msg_id}).to_json())
        finally:
            self.clients.discard(ws)

    async def send(self, env: Envelope) -> None:
        for ws in list(self.clients):
            await ws.send(env.to_json())

    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while not predicate():
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("mock render: condition not met in time")
            await asyncio.sleep(0.05)

    def of_type(self, type_: str) -> list[Envelope]:
        return [e for e in self.received if e.type == type_]


async def _main(port: int, key: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = MockRender(api_key=key)
    await server.start(port=port)
    try:
        while True:
            await asyncio.sleep(5)
            for env in server.received[-5:]:
                log.info("got %s %s %s", env.type, env.msg_id, env.body)
            server.received.clear()
    finally:
        await server.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--key", default="test-key")
    a = ap.parse_args()
    asyncio.run(_main(a.port, a.key))
