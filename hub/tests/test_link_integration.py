"""The real link against the mock Render over a real localhost socket."""
import asyncio

from puntmore_hub.link.client import RenderLink, bets_from
from puntmore_hub.link.mock_render import MockRender
from puntmore_hub.placement.engine import ClientEngine
from puntmore_hub.schemas import Envelope

ACCOUNTS = {"acc1": {"account_id": "acc1", "device": "phone-1", "bookmaker": "bet365"}}
BET = {"bet_id": "b-100", "account_id": "acc1", "stake": 10.0, "expected_price": 2.1, "bet_type": "single",
       "legs": [{"event_id": "ev1", "market": "Match Result", "selection": "Home", "price": 2.1}]}


def test_hello_bet_list_report_ack(cfg, store):
    async def scenario():
        server = MockRender(api_key="test-key")
        await server.start()
        live = cfg.with_overrides(render_ws_url=f"ws://127.0.0.1:{server.port}", render_api_key="test-key")
        engine = ClientEngine(store, cfg=live, sub_clients=ACCOUNTS, notify=False)
        link = RenderLink(store, cfg=live, handlers={"bet_list": lambda env: engine.handle_bet_list(bets_from(env))})
        task = asyncio.create_task(link.run())
        try:
            await server.wait_for(lambda: server.of_type("hello"))
            hello = server.of_type("hello")[0]
            assert hello.body["protocol_version"] == 1 and hello.body["dry_run"] is True

            env = Envelope(type="bet_list", client_id="test", body={"bets": [BET]})
            await server.send(env)
            await server.wait_for(lambda: server.of_type("report"))
            rep = server.of_type("report")[0]
            assert rep.body["kind"] == "placed" and rep.body["bet_id"] == "b-100" and rep.body["dry_run"] is True

            await server.wait_for(lambda: any(a.body.get("msg_id") == env.msg_id for a in server.of_type("ack")))
            # replay the same bet_list: acked as replay, not placed twice
            await server.send(env)
            await server.wait_for(lambda: any(a.body.get("replay") for a in server.of_type("ack")))
            assert len(server.of_type("report")) == 1
            assert store.outbox_pending() == []   # everything we sent was acked and marked sent
        finally:
            link.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            await server.stop()

    asyncio.run(scenario())


def test_bad_key_is_refused_and_link_keeps_retrying(cfg, store):
    async def scenario():
        server = MockRender(api_key="right-key")
        await server.start()
        live = cfg.with_overrides(render_ws_url=f"ws://127.0.0.1:{server.port}", render_api_key="wrong-key")
        link = RenderLink(store, cfg=live)
        task = asyncio.create_task(link.run())
        await asyncio.sleep(1.0)
        assert server.received == []
        assert not task.done()      # still alive, backing off
        link.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await server.stop()

    asyncio.run(scenario())
