import asyncio

import pytest

from puntmore_home.link.client import RenderLink, bets_from
from puntmore_home.schemas import Bet, Envelope, Report


def test_envelope_roundtrip():
    env = Envelope(type="report", client_id="owner", body={"kind": "placed", "bet_id": "b1"})
    back = Envelope.from_json(env.to_json())
    assert back.msg_id == env.msg_id and back.body == env.body and back.type == "report"


def test_report_kind_validated():
    with pytest.raises(ValueError):
        Report("unknown", {})


def test_bet_from_dict():
    b = Bet.from_dict({"bet_id": 1, "account_id": "a", "legs": [{"event_id": 9, "market": "m", "selection": "s", "price": "2.0"}],
                       "stake": "5", "expected_price": 2.0})
    assert b.bet_id == "1" and b.legs[0].event_id == "9" and b.stake == 5.0 and b.bet_type == "single"


def test_link_dispatch_and_replay(cfg, store):
    seen = []
    link = RenderLink(store, cfg=cfg, handlers={"bet_list": lambda env: seen.append(len(bets_from(env)))})
    env = Envelope(type="bet_list", client_id="render", body={"bets": [
        {"bet_id": "b1", "account_id": "a", "legs": [], "stake": 1, "expected_price": 2}]})
    asyncio.run(link.handle(env))
    asyncio.run(link.handle(env))   # replay
    assert seen == [1]
    acks = [r for r in store.outbox_pending() if r["type"] == "ack"]
    assert len(acks) == 2
