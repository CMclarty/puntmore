"""The Hub link's Render end: /2up/ws against hub/PROTOCOL.md, version 1.

The 2-Up engine is stood in for by `app.hub.engine.stub`. Each test gets its
own SQLite file (the socket, its dispatcher and the test touch the database
from different threads, which a single shared in-memory connection does not
survive).
"""
import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app import apikeys
from app.config import settings
from app.database import Base, get_db
from app.hub import engine as hub_engine
from app.hub import protocol, service
from app.main import app
from app.models import HubEscalation, HubInboxSeen, HubOutbox, Tip
from app.time_utils import utcnow

REPO = Path(__file__).resolve().parents[3]
HUB_SCHEMA = REPO / "hub" / "schemas" / "protocol.schema.json"
EXAMPLES = REPO / "hub" / "schemas" / "examples"

BET = {"bet_id": "b-100", "account_id": "acc1", "stake": 10.0, "expected_price": 2.1,
       "bet_type": "single", "source_scope": "shared",
       "legs": [{"event_id": "ev1", "market": "Match Result", "selection": "Home", "price": 2.1}]}


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'hub.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=eng)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(settings, "hub_poll_seconds", 0.05)
    monkeypatch.setattr(settings, "hub_owner_client_id", "owner")
    monkeypatch.setattr(service, "_last_sweep", 0.0)
    hub_engine.stub.reset()
    hub_engine.use(hub_engine.stub)
    service.registry.clear()
    yield Session
    app.dependency_overrides.pop(get_db, None)
    hub_engine.use(None)
    service.registry.clear()
    eng.dispose()


def make_client(Session, name="owner", tools="hub"):
    db = Session()
    try:
        _, key = apikeys.create(db, name, tools)
    finally:
        db.close()
    return key


def headers(key, client_id="owner"):
    return {"Authorization": f"Bearer {key}", "X-Client-Id": client_id}


def frame(type_, body, client_id="owner", msg_id=None):
    return protocol.envelope(type_, client_id, body, msg_id=msg_id)


def hello(client_id="owner", dry_run=True, kill_switch=False, version=1):
    return frame("hello", {"protocol_version": version, "dry_run": dry_run,
                           "kill_switch": kill_switch, "version": "0.1.0"}, client_id)


def sync(ws, client_id="owner"):
    """Round-trip a ping: everything the server did before it is done, and the
    pong must be the NEXT frame (so nothing unexpected was sent first)."""
    p = frame("ping", {}, client_id)
    ws.send_json(p)
    got = ws.receive_json()
    assert got["type"] == "pong", got
    assert got["body"]["msg_id"] == p["msg_id"]


def open_socket(api, key, client_id="owner"):
    ws_cm = api.websocket_connect("/2up/ws", headers=headers(key, client_id))
    ws = ws_cm.__enter__()
    ws.send_json(hello(client_id))
    ack = ws.receive_json()
    assert ack["type"] == "hello_ack"
    return ws_cm, ws


# --- the contract files ------------------------------------------------------

def test_the_vendored_schema_is_identical_to_the_hubs():
    assert protocol.SCHEMA_PATH.read_bytes() == HUB_SCHEMA.read_bytes(), (
        "render/api/app/hub/protocol.schema.json drifted from hub/schemas/protocol.schema.json: "
        "copy the Hub's file over it in the same PR")


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.stem)
def test_every_canonical_example_validates(path):
    assert protocol.schema_error(json.loads(path.read_text())) is None


# --- auth on the handshake -----------------------------------------------------

@pytest.mark.parametrize("hdrs,status", [
    ({}, 401),
    ({"Authorization": "Bearer pm_nope", "X-Client-Id": "owner"}, 401),
    ({"X-Client-Id": "owner"}, 401),
])
def test_bad_or_missing_key_is_refused_before_accept(db_session, hdrs, status):
    make_client(db_session)
    with pytest.raises(WebSocketDenialResponse) as exc:
        with TestClient(app).websocket_connect("/2up/ws", headers=hdrs):
            pass
    assert exc.value.status_code == status


def test_a_key_without_the_hub_grant_is_403(db_session):
    key = make_client(db_session, tools="shop")
    with pytest.raises(WebSocketDenialResponse) as exc:
        with TestClient(app).websocket_connect("/2up/ws", headers=headers(key)):
            pass
    assert exc.value.status_code == 403


def test_a_key_for_another_client_is_refused(db_session):
    key = make_client(db_session, name="owner")
    with pytest.raises(WebSocketDenialResponse) as exc:
        with TestClient(app).websocket_connect("/2up/ws", headers=headers(key, "sub-1")):
            pass
    assert exc.value.status_code == 401


def test_a_static_key_never_opens_the_socket(db_session, monkeypatch):
    monkeypatch.setattr(settings, "state_api_key", "static-state-key")
    with pytest.raises(WebSocketDenialResponse) as exc:
        with TestClient(app).websocket_connect("/2up/ws", headers=headers("static-state-key", "state-key")):
            pass
    assert exc.value.status_code == 403


def test_a_real_uvicorn_refuses_with_a_readable_401_and_accepts_a_good_key(db_session):
    """TestClient cannot see this: uvicorn's websockets-sansio protocol (what
    `--ws auto` runs in production) mangled a Starlette denial Response into
    "not a valid HTTP response". The Hub must see a plain 401."""
    import asyncio

    import uvicorn
    import websockets

    key = make_client(db_session)

    async def scenario():
        # log_level/log_config None: uvicorn must not reconfigure "uvicorn.error",
        # which app code logs through and other tests read with caplog.
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off",
                                               log_level=None, log_config=None,
                                               ws="websockets-sansio"))
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.02)
        url = f"ws://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}/2up/ws"
        try:
            with pytest.raises(websockets.InvalidStatus) as exc:
                async with websockets.connect(url, additional_headers=headers("pm_wrong")):
                    pass
            assert exc.value.response.status_code == 401
            async with websockets.connect(url, additional_headers=headers(key)) as ws:
                await ws.send(json.dumps(hello()))
                assert json.loads(await ws.recv())["type"] == "hello_ack"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)

    asyncio.run(scenario())


def test_hub_is_a_grant_the_cli_accepts():
    assert apikeys.parse_tools("hub,365") == "accabonus+goalscorers+hub"


# --- hello, ack, replay ------------------------------------------------------

def test_hello_gets_hello_ack_and_is_recorded(db_session):
    key = make_client(db_session)
    api = TestClient(app)
    with api.websocket_connect("/2up/ws", headers=headers(key)) as ws:
        ws.send_json(hello(dry_run=True, kill_switch=True))
        ack = ws.receive_json()
        assert ack["type"] == "hello_ack" and ack["client_id"] == "owner"
        assert ack["body"]["protocol_version"] == 1 and ack["body"]["server_time"]
        assert protocol.schema_error(ack) is None
        sync(ws)
        clients = api.get("/2up/hub/clients").json()
    assert clients[0]["client_id"] == "owner" and clients[0]["connected"] is True
    assert clients[0]["dry_run"] is True and clients[0]["kill_switch"] is True
    assert clients[0]["hub_version"] == "0.1.0" and clients[0]["last_frame_at"]
    assert api.get("/2up/hub/clients").json()[0]["connected"] is False


def test_an_unsupported_protocol_version_is_closed_1008(db_session):
    key = make_client(db_session)
    with TestClient(app).websocket_connect("/2up/ws", headers=headers(key)) as ws:
        ws.send_json(hello(version=2))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008


def test_a_report_is_acked_applied_once_and_a_replay_is_acked_as_replay(db_session):
    key = make_client(db_session)
    rep = json.loads((EXAMPLES / "report_placed.json").read_text())
    with TestClient(app).websocket_connect("/2up/ws", headers=headers(key)) as ws:
        ws.send_json(hello())
        ws.receive_json()
        ws.send_json(rep)
        ack = ws.receive_json()
        assert ack["type"] == "ack" and ack["body"] == {"msg_id": rep["msg_id"]}
        assert protocol.schema_error(ack) is None
        ws.send_json(rep)
        again = ws.receive_json()
        assert again["body"] == {"msg_id": rep["msg_id"], "replay": True}
    assert hub_engine.stub.reports == type(hub_engine.stub.reports)([("owner", rep["body"])],
                                                                      maxlen=hub_engine.stub.MAX_REPORTS)
    db = db_session()
    assert db.query(HubInboxSeen).filter_by(msg_id=rep["msg_id"]).count() == 1
    db.close()


def test_replay_survives_a_reconnect(db_session):
    key = make_client(db_session)
    rep = json.loads((EXAMPLES / "report_failed.json").read_text())
    api = TestClient(app)
    for expect_replay in (False, True):
        with api.websocket_connect("/2up/ws", headers=headers(key)) as ws:
            ws.send_json(rep)
            assert ws.receive_json()["body"].get("replay", False) is expect_replay
    assert len(hub_engine.stub.reports) == 1


def test_an_invalid_frame_is_acked_with_an_error_and_the_socket_lives(db_session, caplog):
    key = make_client(db_session)
    bad = frame("report", {"kind": "placed", "ts": protocol.utcnow_iso(), "bet_id": "b-1"})  # no actual_price
    with TestClient(app).websocket_connect("/2up/ws", headers=headers(key)) as ws:
        with caplog.at_level("WARNING", logger="app.hub.service"):
            for _ in range(3):
                ws.send_json(bad)
                ack = ws.receive_json()
                assert ack["type"] == "ack" and ack["body"]["msg_id"] == bad["msg_id"]
                assert "actual_price" in ack["body"]["error"]
            ws.send_text("{not json")
            assert ws.receive_json()["body"]["error"] == "frame is not JSON"
        sync(ws)
    assert hub_engine.stub.reports == type(hub_engine.stub.reports)([], maxlen=1000)
    invalid_lines = [r for r in caplog.records if "invalid frame" in r.getMessage()]
    assert len(invalid_lines) == 1     # one line per problem, not per frame


def test_a_frame_for_another_client_is_refused(db_session):
    key = make_client(db_session)
    rep = json.loads((EXAMPLES / "report_balance.json").read_text())
    rep["client_id"] = "someone-else"
    with TestClient(app).websocket_connect("/2up/ws", headers=headers(key)) as ws:
        ws.send_json(rep)
        assert "client_id" in ws.receive_json()["body"]["error"]
    assert len(hub_engine.stub.reports) == 0


def test_an_engine_failure_is_not_acked_so_the_hub_resends(db_session):
    key = make_client(db_session)

    class Broken(hub_engine.StubLedger):
        def apply_report(self, db, client_id, report):
            raise RuntimeError("ledger down")

    hub_engine.use(Broken())
    rep = json.loads((EXAMPLES / "report_settled.json").read_text())
    with TestClient(app).websocket_connect("/2up/ws", headers=headers(key)) as ws:
        ws.send_json(rep)
        sync(ws)          # the pong comes first: no ack was sent
    db = db_session()
    assert db.query(HubInboxSeen).count() == 0     # not marked seen either
    db.close()


# --- tips, escalations, scrape results ---------------------------------------

def test_a_tip_is_stored_once_and_listed(db_session):
    key = make_client(db_session)
    tip = json.loads((EXAMPLES / "tip.json").read_text())
    api = TestClient(app)
    with api.websocket_connect("/2up/ws", headers=headers(key)) as ws:
        ws.send_json(tip)
        assert ws.receive_json()["body"] == {"msg_id": tip["msg_id"]}
        again = dict(tip, msg_id="another-msg-0001")       # same tip, re-scraped
        ws.send_json(again)
        assert ws.receive_json()["body"] == {"msg_id": "another-msg-0001"}
    db = db_session()
    rows = db.query(Tip).all()
    assert len(rows) == 1 and rows[0].tip_id == tip["body"]["tip_id"] and rows[0].client_id == "owner"
    db.close()
    listed = api.get("/2up/hub/tips").json()
    assert listed[0]["tip_id"] == tip["body"]["tip_id"] and listed[0]["tip"] == tip["body"]


def test_escalation_and_outcome_land_on_one_row(db_session):
    key = make_client(db_session)
    esc = json.loads((EXAMPLES / "escalation.json").read_text())
    out = json.loads((EXAMPLES / "escalation_outcome.json").read_text())
    res = json.loads((EXAMPLES / "scrape_result.json").read_text())
    with TestClient(app).websocket_connect("/2up/ws", headers=headers(key)) as ws:
        for f in (esc, out, res):
            ws.send_json(f)
            assert ws.receive_json()["body"] == {"msg_id": f["msg_id"]}
    db = db_session()
    row = db.query(HubEscalation).one()
    assert row.escalation_id == 12 and row.kind == "failed_bet" and row.detail.startswith("bet ")
    assert row.outcome.startswith("Dropped.")
    db.close()


# --- outbound: bet lists -----------------------------------------------------

def test_a_bet_list_is_delivered_and_marked_sent_only_on_ack(db_session):
    key = make_client(db_session)
    hub_engine.stub.record_bet_list(None, "owner", [BET])
    ws_cm, ws = open_socket(TestClient(app), key)
    try:
        got = ws.receive_json()
        assert got["type"] == "bet_list" and got["client_id"] == "owner"
        assert got["body"]["bets"] == [BET]
        assert protocol.schema_error(got) is None
        time.sleep(0.2)                       # several polls go by
        sync(ws)                              # ...and nothing was sent twice
        assert hub_engine.stub.sent == {}     # not marked sent: no ack yet
        ws.send_json(frame("ack", {"msg_id": got["msg_id"]}))
        sync(ws)
        assert hub_engine.stub.sent == {"owner": {"b-100"}}
        db = db_session()
        assert db.query(HubOutbox).filter_by(msg_id=got["msg_id"]).one().acked_at is not None
        db.close()
    finally:
        ws_cm.__exit__(None, None, None)


def test_an_unacked_bet_list_is_resent_after_reconnect_with_the_same_msg_id(db_session):
    key = make_client(db_session)
    hub_engine.stub.record_bet_list(None, "owner", [BET])
    api = TestClient(app)
    ws_cm, ws = open_socket(api, key)
    first = ws.receive_json()
    ws_cm.__exit__(None, None, None)          # dropped before acking

    ws_cm, ws = open_socket(api, key)
    try:
        again = ws.receive_json()
        assert again["type"] == "bet_list" and again["msg_id"] == first["msg_id"]
        ws.send_json(frame("ack", {"msg_id": again["msg_id"], "replay": True}))
        time.sleep(0.2)
        sync(ws)                              # no third copy
        assert hub_engine.stub.sent == {"owner": {"b-100"}}
    finally:
        ws_cm.__exit__(None, None, None)


def test_a_dispatched_bet_is_never_listed_in_a_new_frame(db_session):
    """Even if the engine keeps listing it (a failed bet, say): one frame per bet_id."""
    key = make_client(db_session)

    class Sticky(hub_engine.StubLedger):
        def mark_sent(self, db, client_id, bet_ids):
            pass                              # the engine never forgets it

    sticky = Sticky()
    hub_engine.use(sticky)
    sticky.record_bet_list(None, "owner", [BET])
    ws_cm, ws = open_socket(TestClient(app), key)
    try:
        got = ws.receive_json()
        ws.send_json(frame("ack", {"msg_id": got["msg_id"]}))
        ws.send_json(frame("report", {"kind": "failed", "ts": protocol.utcnow_iso(),
                                      "bet_id": "b-100", "reason": "market suspended"}))
        assert ws.receive_json()["type"] == "ack"
        time.sleep(0.2)
        sync(ws)
    finally:
        ws_cm.__exit__(None, None, None)
    db = db_session()
    assert db.query(HubOutbox).filter_by(type="bet_list").count() == 1
    db.close()


def test_an_owner_bet_never_goes_to_another_client(db_session):
    key = make_client(db_session, name="sub-1")
    owner_bet = dict(BET, bet_id="b-owner", source_scope="owner")
    hub_engine.stub.record_bet_list(None, "sub-1", [owner_bet, BET])
    ws_cm, ws = open_socket(TestClient(app), key, "sub-1")
    try:
        got = ws.receive_json()
        assert [b["bet_id"] for b in got["body"]["bets"]] == ["b-100"]
        time.sleep(0.2)
        sync(ws, "sub-1")
    finally:
        ws_cm.__exit__(None, None, None)


def test_bets_wait_for_hello(db_session):
    key = make_client(db_session)
    hub_engine.stub.record_bet_list(None, "owner", [BET])
    with TestClient(app).websocket_connect("/2up/ws", headers=headers(key)) as ws:
        time.sleep(0.2)
        sync(ws)          # no bet_list before hello


# --- outbound: operator frames -----------------------------------------------

def test_kill_switch_and_scrape_job_reach_a_connected_hub(db_session):
    key = make_client(db_session)
    api = TestClient(app)
    ws_cm, ws = open_socket(api, key)
    try:
        r = api.post("/2up/hub/owner/kill_switch", json={"on": True, "reason": "manual pause"})
        assert r.status_code == 200 and r.json()["connected"] is True
        got = ws.receive_json()
        assert got["type"] == "kill_switch" and got["body"] == {"on": True, "reason": "manual pause"}
        assert got["msg_id"] == r.json()["msg_id"]

        r = api.post("/2up/hub/owner/scrape_job", json={"source": "example-tipster", "force": True})
        got = ws.receive_json()
        assert got["type"] == "scrape_job" and got["body"]["source"] == "example-tipster"
        assert got["body"]["params"] == {"force": True} and got["body"]["job_id"].startswith("job-")
        assert protocol.schema_error(got) is None
        ws.send_json(frame("ack", {"msg_id": got["msg_id"]}))
        sync(ws)
        assert api.get("/2up/hub/clients").json()[0]["unacked"] == 1   # the kill_switch
    finally:
        ws_cm.__exit__(None, None, None)


def test_a_frame_for_an_offline_hub_waits_for_it(db_session):
    key = make_client(db_session)
    api = TestClient(app)
    r = api.post("/2up/hub/owner/kill_switch", json={"on": True})
    assert r.status_code == 200 and r.json()["connected"] is False
    ws_cm, ws = open_socket(api, key)
    try:
        assert ws.receive_json()["msg_id"] == r.json()["msg_id"]
    finally:
        ws_cm.__exit__(None, None, None)


def test_operator_frames_need_a_known_hub_client(db_session):
    make_client(db_session, name="not-a-hub", tools="shop")
    api = TestClient(app)
    assert api.post("/2up/hub/nobody/kill_switch", json={"on": True}).status_code == 404
    assert api.post("/2up/hub/not-a-hub/kill_switch", json={"on": True}).status_code == 404


def test_operator_routes_need_the_365_grant(db_session):
    from app.security import user_permissions
    app.dependency_overrides[user_permissions] = lambda: "shop"
    try:
        api = TestClient(app)
        assert api.get("/2up/hub/clients").status_code == 403
        assert api.get("/2up/hub/tips").status_code == 403
        assert api.post("/2up/hub/owner/kill_switch", json={"on": True}).status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


# --- one socket per client, the sweep ---------------------------------------

def test_a_second_connection_replaces_the_first(db_session):
    key = make_client(db_session)
    api = TestClient(app)
    old_cm, old = open_socket(api, key)
    new_cm, new = open_socket(api, key)
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            old.receive_json()
        assert exc.value.code == 4000
        sync(new)
        assert service.registry.get("owner") is not None
        clients = api.get("/2up/hub/clients").json()
        assert len(clients) == 1 and clients[0]["connected"] is True
    finally:
        new_cm.__exit__(None, None, None)
        try:
            old_cm.__exit__(None, None, None)
        except Exception:
            pass


def test_the_sweep_drops_seen_ids_older_than_seven_days(db_session):
    db = db_session()
    now = utcnow()
    db.add(HubInboxSeen(client_id="owner", msg_id="old-msg-0001", type="report", seen_at=now - timedelta(days=8)))
    db.add(HubInboxSeen(client_id="owner", msg_id="new-msg-0001", type="report", seen_at=now - timedelta(days=6)))
    db.commit()
    assert service.sweep(db, now)["seen"] == 1
    assert [r.msg_id for r in db.query(HubInboxSeen)] == ["new-msg-0001"]
    db.close()


def test_the_engine_adapter_falls_back_to_stubs_only_when_absent():
    hub_engine.use(None)
    try:
        led = hub_engine._load()
        try:
            import app.twoup.ledger  # noqa: F401
            assert led is not hub_engine.stub
        except ModuleNotFoundError:
            assert led is hub_engine.stub
    finally:
        hub_engine.use(None)


# --- kill switches (PROTOCOL.md "Kill switches") -----------------------------

def _open_with(api, key, kill_switch):
    ws_cm = api.websocket_connect("/2up/ws", headers=headers(key))
    ws = ws_cm.__enter__()
    ws.send_json(hello(kill_switch=kill_switch))
    assert ws.receive_json()["type"] == "hello_ack"
    return ws_cm, ws


def test_a_hub_whose_hello_says_kill_switch_on_is_sent_no_bet_list(db_session):
    key = make_client(db_session)
    hub_engine.stub.record_bet_list(None, "owner", [BET])
    api = TestClient(app)
    ws_cm, ws = _open_with(api, key, kill_switch=True)
    try:
        time.sleep(0.2)                       # several dispatcher polls
        sync(ws)                              # nothing came before the pong
        # Its kill_switch / scrape_job frames still go.
        r = api.post("/2up/hub/owner/kill_switch", json={"on": True, "reason": "both"})
        got = ws.receive_json()
        assert got["type"] == "kill_switch" and got["msg_id"] == r.json()["msg_id"]
        ws.send_json(frame("ack", {"msg_id": got["msg_id"]}))
        sync(ws)
    finally:
        ws_cm.__exit__(None, None, None)
    db = db_session()
    assert db.query(HubOutbox).filter_by(type="bet_list").count() == 0
    db.close()
    assert hub_engine.stub.sent == {}         # the ledger's list is untouched
    # Once a hello says it is off again, the list goes.
    ws_cm, ws = _open_with(api, key, kill_switch=False)
    try:
        got = ws.receive_json()
        assert got["type"] == "bet_list" and got["body"]["bets"] == [BET]
    finally:
        ws_cm.__exit__(None, None, None)


def test_an_unacked_bet_list_is_not_redelivered_to_a_killed_hub(db_session):
    key = make_client(db_session)
    hub_engine.stub.record_bet_list(None, "owner", [BET])
    api = TestClient(app)
    ws_cm, ws = open_socket(api, key)
    first = ws.receive_json()
    assert first["type"] == "bet_list"
    ws_cm.__exit__(None, None, None)          # dropped before acking
    ws_cm, ws = _open_with(api, key, kill_switch=True)
    try:
        time.sleep(0.2)
        sync(ws)                              # the unacked frame waits
    finally:
        ws_cm.__exit__(None, None, None)
    ws_cm, ws = _open_with(api, key, kill_switch=False)
    try:
        assert ws.receive_json()["msg_id"] == first["msg_id"]
    finally:
        ws_cm.__exit__(None, None, None)


def test_render_turns_a_hubs_kill_switch_on_never_off(db_session):
    make_client(db_session)
    api = TestClient(app)
    r = api.post("/2up/hub/owner/kill_switch", json={"on": False, "reason": "resume"})
    assert r.status_code == 422 and "ON only" in r.json()["detail"]
    db = db_session()
    assert db.query(HubOutbox).filter_by(type="kill_switch").count() == 0
    db.close()
