"""The Hub link's server side: auth, inbound handling, the outbound dispatcher.

Invariants (render/api/AGENTS.md, "Hub endpoint"):
- The handshake is refused (HTTP 401/403, before the socket is accepted) unless
  the Bearer key resolves through `security.resolve_api_key` to an API client
  granted `hub` whose NAME is the `X-Client-Id`.
- One live socket per client id; a newer one replaces the older.
- Every non-control inbound frame is acked; a msg_id already in
  `hub_inbox_seen` is acked `replay: true` and not processed again. The seen
  row is written in the same transaction as the frame's effect.
- A frame whose processing FAILS (an engine error) is not acked, so the Hub
  re-sends it; an INVALID frame is acked with an `error` and dropped.
- A bet_id goes out in exactly one frame (`hub_sent_bets`). That frame is
  re-delivered under the same msg_id until acked, and the engine's
  `mark_sent` runs only on the ack. A bet is never listed again in a new frame,
  so a failed bet is never resent.
- An `owner`-scope bet goes only to `settings.hub_owner_client_id`.
- One log line per problem per connection, never one per frame.
- Every table is swept (`sweep`), every in-memory map is bounded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..database import get_db
from ..models import HubEscalation, HubInboxSeen, HubOutbox, HubSentBet, HubTip
from ..security import has_permission, resolve_api_key
from ..time_utils import utcnow
from . import engine
from .protocol import (
    CONTROL_TYPES, INBOUND_TYPES, MAX_FRAME_BYTES, PROTOCOL_VERSION, envelope,
    schema_error, utcnow_iso,
)

log = logging.getLogger(__name__)

GRANT = "hub"

# Retention, by table. Seen ids only need to outlive the Hub's outbox replay
# window; the rest are records someone may want to look back at.
SEEN_RETENTION = timedelta(days=7)
OUTBOX_RETENTION = timedelta(days=7)
SENT_BET_RETENTION = timedelta(days=30)
TIP_RETENTION = timedelta(days=30)
ESCALATION_RETENTION = timedelta(days=90)
SWEEP_EVERY_S = 3600.0


# --- logging, once per problem -------------------------------------------------

class LogOnce:
    """Logs the first occurrence of each problem key and counts the rest.
    Bounded: the oldest keys are forgotten past MAX_KEYS."""

    MAX_KEYS = 512

    def __init__(self) -> None:
        self._seen: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.Lock()

    def __call__(self, key: str, level: int, msg: str, *args) -> None:
        with self._lock:
            if key in self._seen:
                self._seen[key] += 1
                return
            self._seen[key] = 1
            while len(self._seen) > self.MAX_KEYS:
                self._seen.popitem(last=False)
        log.log(level, msg, *args)

    def repeats(self) -> int:
        with self._lock:
            return sum(n - 1 for n in self._seen.values())


_process_log = LogOnce()   # for problems not tied to one connection


# --- auth -------------------------------------------------------------------

def presented_key(headers) -> str | None:
    auth = headers.get("authorization", "")
    scheme, _, cred = auth.partition(" ")
    if scheme.lower() == "bearer" and cred.strip():
        return cred.strip()
    key = headers.get("x-api-key", "").strip()
    return key or None


def authorise(db, key: str | None, client_id: str) -> tuple[int, str] | None:
    """None when the handshake may proceed, else (HTTP status, reason).

    401 = no key, a bad key, or a key that is not that client's (PROTOCOL.md);
    403 = a valid client key without the `hub` grant."""
    if not key:
        return 401, "missing API key"
    if not client_id:
        return 401, "missing X-Client-Id"
    principal = resolve_api_key(key, db)
    if principal is None:
        return 401, "invalid API key"
    if principal.kind != "client" or not has_permission(principal.permissions, GRANT):
        return 403, "key lacks the hub grant"
    if principal.name != client_id:
        return 401, "key does not belong to that client"
    return None


# --- connections ------------------------------------------------------------

@dataclass
class ClientState:
    client_id: str
    connected: bool = False
    connected_at: datetime | None = None
    last_hello_at: datetime | None = None
    dry_run: bool | None = None
    kill_switch: bool | None = None
    hub_version: str | None = None
    last_frame_at: datetime | None = None


class Registry:
    """The live socket per client id, and what each client last said.
    Thread-safe: REST routes run in the threadpool. Bounded by the number of
    API clients holding the `hub` grant (only an authorised id gets in)."""

    MAX_STATES = 256

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: dict[str, "Connection"] = {}
        self._states: OrderedDict[str, ClientState] = OrderedDict()

    def state(self, client_id: str) -> ClientState:
        with self._lock:
            st = self._states.get(client_id)
            if st is None:
                st = self._states[client_id] = ClientState(client_id)
                while len(self._states) > self.MAX_STATES:
                    old_id, _ = next(iter(self._states.items()))
                    if old_id in self._conns:
                        break
                    self._states.popitem(last=False)
            return st

    def register(self, conn: "Connection") -> "Connection | None":
        st = self.state(conn.client_id)
        with self._lock:
            old = self._conns.get(conn.client_id)
            self._conns[conn.client_id] = conn
            st.connected = True
            st.connected_at = utcnow()
        return old

    def unregister(self, conn: "Connection") -> None:
        with self._lock:
            if self._conns.get(conn.client_id) is conn:
                del self._conns[conn.client_id]
                st = self._states.get(conn.client_id)
                if st is not None:
                    st.connected = False

    def get(self, client_id: str) -> "Connection | None":
        with self._lock:
            return self._conns.get(client_id)

    def states(self) -> list[ClientState]:
        with self._lock:
            return list(self._states.values())

    def clear(self) -> None:
        with self._lock:
            self._conns.clear()
            self._states.clear()


registry = Registry()


@contextmanager
def session_for(app):
    """A DB session from `get_db`, honouring the app's dependency override (the
    tests' in-memory database) exactly as an HTTP route would."""
    factory = app.dependency_overrides.get(get_db, get_db)
    gen = factory()
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


class Connection:
    """One authorised Hub socket. `run` owns the receive loop; a dispatcher
    task beside it sends bet lists and queued frames."""

    def __init__(self, websocket, client_id: str, app) -> None:
        self.ws = websocket
        self.client_id = client_id
        self.app = app
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self.hello = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.closed = False
        self.problems = LogOnce()

    # ---- plumbing ---------------------------------------------------------
    def _db_call(self, fn, *args):
        with session_for(self.app) as db:
            return fn(db, *args)

    async def db(self, fn, *args):
        return await run_in_threadpool(self._db_call, fn, *args)

    async def send(self, frame: dict | str) -> None:
        text = frame if isinstance(frame, str) else json.dumps(frame)
        async with self.send_lock:
            await self.ws.send_text(text)

    def nudge(self) -> None:
        """Wake the dispatcher now (callable from any thread or event loop)."""
        try:
            self.loop.call_soon_threadsafe(self.wake.set)
        except RuntimeError:       # its loop has gone: the socket is gone too
            pass

    async def close(self, code: int, reason: str) -> None:
        self.closed = True
        try:
            await self.ws.close(code=code, reason=reason)
        except Exception:  # noqa: BLE001 - already closed
            pass

    def close_soon(self, code: int, reason: str) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self.close(code, reason), self.loop)
        except RuntimeError:
            pass

    async def ack(self, msg_id: str, replay: bool = False, error: str | None = None) -> None:
        body: dict = {"msg_id": msg_id}
        if replay:
            body["replay"] = True
        if error:
            body["error"] = error
        await self.send(envelope("ack", self.client_id, body))

    # ---- lifecycle --------------------------------------------------------
    async def run(self) -> None:
        old = registry.register(self)
        if old is not None and old is not self:
            log.info("hub %s: a new connection replaces the previous one", self.client_id)
            old.close_soon(4000, "replaced by a newer connection")
        dispatcher = asyncio.create_task(self.dispatch_loop())
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                text = msg.get("text")
                if text is None:
                    self.problems("binary", logging.WARNING,
                                  "hub %s: binary frame ignored (text only)", self.client_id)
                    await self.ack("", error="binary frames are not accepted")
                    continue
                await self.handle_text(text)
        except Exception as exc:  # noqa: BLE001 - a dead socket ends the loop
            if not self.closed:
                log.info("hub %s: socket ended (%s)", self.client_id, exc.__class__.__name__)
        finally:
            self.closed = True
            dispatcher.cancel()
            registry.unregister(self)
            if self.problems.repeats():
                log.info("hub %s: %d repeated problem frame(s) not logged individually",
                         self.client_id, self.problems.repeats())

    # ---- inbound ----------------------------------------------------------
    async def handle_text(self, text: str) -> None:
        if len(text) > MAX_FRAME_BYTES or len(text.encode()) > MAX_FRAME_BYTES:
            self.problems("too-big", logging.WARNING, "hub %s: frame over 8 MiB dropped", self.client_id)
            await self.ack("", error="frame over 8 MiB")
            return
        try:
            frame = json.loads(text)
        except ValueError:
            self.problems("not-json", logging.WARNING, "hub %s: frame is not JSON", self.client_id)
            await self.ack("", error="frame is not JSON")
            return
        msg_id = frame.get("msg_id") if isinstance(frame, dict) else None
        msg_id = msg_id if isinstance(msg_id, str) else ""
        ftype = frame.get("type") if isinstance(frame, dict) else None
        body = frame.get("body") if isinstance(frame, dict) else None

        # Version first: a v2 hello is a valid hello of a version we refuse,
        # not a malformed frame (PROTOCOL.md, Versioning).
        if ftype == "hello" and isinstance(body, dict) and body.get("protocol_version") != PROTOCOL_VERSION:
            log.warning("hub %s: protocol_version %r refused", self.client_id, body.get("protocol_version"))
            await self.close(1008, f"unsupported protocol_version {body.get('protocol_version')!r}; "
                                   f"this server speaks {PROTOCOL_VERSION}")
            return

        bad = schema_error(frame)
        if bad is not None:
            sig, message = bad
            self.problems(sig, logging.WARNING, "hub %s: invalid frame (%s): %s",
                          self.client_id, sig, message)
            await self.ack(msg_id, error=f"invalid frame: {message}")
            return
        if frame["client_id"] != self.client_id:
            self.problems("client-id", logging.WARNING,
                          "hub %s: frame names client_id %r; dropped", self.client_id, frame["client_id"])
            await self.ack(msg_id, error="client_id does not match the connection")
            return

        st = registry.state(self.client_id)
        st.last_frame_at = utcnow()

        if ftype not in INBOUND_TYPES:
            self.problems(f"unexpected:{ftype}", logging.WARNING,
                          "hub %s: %s is a Render-to-Hub message; ignored", self.client_id, ftype)
            await self.ack(msg_id, error=f"{ftype} is not accepted from a Hub")
            return
        if ftype == "hello":
            st.last_hello_at = utcnow()
            st.dry_run = body["dry_run"]
            st.kill_switch = body["kill_switch"]
            st.hub_version = body.get("version")
            await self.send(envelope("hello_ack", self.client_id,
                                     {"protocol_version": PROTOCOL_VERSION, "server_time": utcnow_iso()}))
            self.hello.set()
            return
        if ftype == "ping":
            await self.send(envelope("pong", self.client_id, {"msg_id": msg_id}))
            return
        if ftype == "pong":
            return
        if ftype == "ack":
            try:
                await self.db(on_ack, self.client_id, body["msg_id"])
            except Exception as exc:  # noqa: BLE001
                self.problems(f"ack-fail:{exc.__class__.__name__}", logging.ERROR,
                              "hub %s: recording an ack failed: %s (the frame is re-sent on reconnect)",
                              self.client_id, exc)
            return

        try:
            replay = await self.db(process_inbound, self.client_id, frame)
        except Exception as exc:  # noqa: BLE001 - never crash the socket
            self.problems(f"process-fail:{ftype}:{exc.__class__.__name__}", logging.ERROR,
                          "hub %s: handling %s failed, not acked (Hub will re-send): %s",
                          self.client_id, ftype, exc)
            return
        await self.ack(msg_id, replay=replay)

    # ---- outbound ---------------------------------------------------------
    async def dispatch_loop(self) -> None:
        """Poll the engine for this client's bet lists every few seconds and
        send them, plus any queued kill_switch / scrape_job frames. Starts after
        the Hub's hello. The first pass re-sends every frame still unacked."""
        global _last_sweep
        await self.hello.wait()
        first = True
        while not self.closed:
            try:
                if time.monotonic() - _last_sweep >= SWEEP_EVERY_S:
                    _last_sweep = time.monotonic()
                    await self.db(sweep)
                frames = await self.db(build_outgoing, self.client_id, first,
                                       settings.hub_owner_client_id, self.problems)
                sent = []
                try:
                    for msg_id, text in frames:
                        await self.send(text)
                        sent.append(msg_id)
                finally:
                    if sent:
                        await self.db(mark_frames_sent, sent)
                first = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if self.closed:
                    return
                self.problems(f"dispatch:{exc.__class__.__name__}", logging.ERROR,
                              "hub %s: dispatch pass failed: %s", self.client_id, exc)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=settings.hub_poll_seconds)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()


_last_sweep = 0.0


# --- database work (sync; run in the threadpool) ------------------------------

def process_inbound(db, client_id: str, frame: dict) -> bool:
    """Apply one valid, non-control frame. Returns True if it was a replay.
    The seen row and the frame's effect commit together."""
    msg_id = frame["msg_id"]
    seen = (db.query(HubInboxSeen.id)
            .filter(HubInboxSeen.client_id == client_id, HubInboxSeen.msg_id == msg_id).first())
    if seen is not None:
        return True
    try:
        db.add(HubInboxSeen(client_id=client_id, msg_id=msg_id, type=frame["type"], seen_at=utcnow()))
        db.flush()
    except IntegrityError:
        db.rollback()
        return True
    try:
        _HANDLERS[frame["type"]](db, client_id, frame["body"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return False


def _on_report(db, client_id: str, body: dict) -> None:
    engine.apply_report(db, client_id, body)


def _on_tip(db, client_id: str, body: dict) -> None:
    if db.query(HubTip.id).filter(HubTip.tip_id == body["tip_id"]).first() is not None:
        return       # the same tip re-scraped: tip_id is stable, keep the first
    db.add(HubTip(
        tip_id=body["tip_id"], client_id=client_id, source_id=body["source_id"],
        event=body["event"], market=body["market"], selection=body["selection"],
        advised_price=body.get("advised_price"), tip_time=body["tip_time"],
        body=json.dumps(body), received_at=utcnow(),
    ))


def _escalation_row(db, client_id: str, body: dict) -> HubEscalation:
    row = (db.query(HubEscalation)
           .filter(HubEscalation.client_id == client_id,
                   HubEscalation.escalation_id == body["escalation_id"]).first())
    if row is None:
        row = HubEscalation(client_id=client_id, escalation_id=body["escalation_id"],
                            kind=body["kind"], ref=body["ref"], received_at=utcnow())
        db.add(row)
    return row


def _on_escalation(db, client_id: str, body: dict) -> None:
    row = _escalation_row(db, client_id, body)
    row.kind, row.ref = body["kind"], body["ref"]
    row.detail, row.created = body["detail"], body["created"]


def _on_escalation_outcome(db, client_id: str, body: dict) -> None:
    row = _escalation_row(db, client_id, body)
    row.outcome = body["outcome"]
    row.outcome_at = utcnow()


def _on_scrape_result(db, client_id: str, body: dict) -> None:
    log.info("hub %s: scrape_result job %s: %s (%s items)%s", client_id, body["job_id"],
             body["status"], body.get("items"), f" — {body['error']}" if body.get("error") else "")


_HANDLERS = {
    "report": _on_report,
    "tip": _on_tip,
    "escalation": _on_escalation,
    "escalation_outcome": _on_escalation_outcome,
    "scrape_result": _on_scrape_result,
}


def on_ack(db, client_id: str, msg_id: str) -> bool:
    """The Hub acked one of our frames. A bet_list's bets are marked sent in
    the engine now, and not before. Returns False for an unknown msg_id."""
    row = (db.query(HubOutbox)
           .filter(HubOutbox.client_id == client_id, HubOutbox.msg_id == msg_id).first())
    if row is None:
        return False
    if row.acked_at is not None:
        return True
    row.acked_at = utcnow()
    if row.bet_ids:
        try:
            engine.mark_sent(db, client_id, json.loads(row.bet_ids))
        except Exception:
            db.rollback()
            raise
    db.commit()
    return True


def _bet_lists(raw: list) -> list[list]:
    """The engine's pending lists, normalised (see engine.py's docstring)."""
    lists, loose = [], []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("bets"), list):
            lists.append(item["bets"])
        elif isinstance(item, dict) and "bet_id" in item:
            loose.append(item)
    if loose:
        lists.append(loose)
    return lists


def build_outgoing(db, client_id: str, resend_sent: bool, owner_id: str,
                   problems: LogOnce = _process_log) -> list[tuple[str, str]]:
    """Frames to send now, as (msg_id, json): every unacked frame not yet sent
    (or, on a fresh connection, every unacked frame at all), then a new
    bet_list frame per pending list from the engine. New frames are written to
    the outbox and their bet_ids to hub_sent_bets before they are returned."""
    q = db.query(HubOutbox).filter(HubOutbox.client_id == client_id, HubOutbox.acked_at.is_(None))
    if not resend_sent:
        q = q.filter(HubOutbox.sent_at.is_(None))
    frames = [(r.msg_id, r.frame) for r in q.order_by(HubOutbox.id)]

    lists = _bet_lists(engine.pending_bet_lists(db, client_id))
    if not lists:
        return frames
    ids = {b.get("bet_id") for bets in lists for b in bets if isinstance(b, dict)}
    dispatched = {bid for (bid,) in db.query(HubSentBet.bet_id).filter(
        HubSentBet.client_id == client_id, HubSentBet.bet_id.in_([str(i) for i in ids if i]))}
    for bets in lists:
        keep = []
        for bet in bets:
            bet_id = bet.get("bet_id") if isinstance(bet, dict) else None
            if not bet_id or str(bet_id) in dispatched:
                continue      # already in a frame (awaiting its ack, or done)
            if bet.get("source_scope") == "owner" and client_id != owner_id:
                problems(f"owner-scope:{bet_id}", logging.ERROR,
                         "hub %s: owner-scope bet %s withheld: only %r may receive owner bets",
                         client_id, bet_id, owner_id)
                continue
            bad = schema_error(envelope("bet_list", client_id, {"bets": [bet]}))
            if bad is not None:
                problems(f"bad-bet:{bet_id}", logging.ERROR,
                         "hub %s: bet %s not sent, it breaks the protocol: %s", client_id, bet_id, bad[1])
                continue
            keep.append(bet)
            dispatched.add(str(bet_id))
        if not keep:
            continue
        env = envelope("bet_list", client_id, {"bets": keep})
        text = json.dumps(env)
        bet_ids = [str(b["bet_id"]) for b in keep]
        db.add(HubOutbox(msg_id=env["msg_id"], client_id=client_id, type="bet_list", frame=text,
                         bet_ids=json.dumps(bet_ids), created_at=utcnow()))
        for bid in bet_ids:
            db.add(HubSentBet(client_id=client_id, bet_id=bid, msg_id=env["msg_id"], created_at=utcnow()))
        frames.append((env["msg_id"], text))
    db.commit()
    return frames


def mark_frames_sent(db, msg_ids: list[str]) -> None:
    now = utcnow()
    (db.query(HubOutbox)
     .filter(HubOutbox.msg_id.in_(msg_ids), HubOutbox.sent_at.is_(None))
     .update({HubOutbox.sent_at: now}, synchronize_session=False))
    db.commit()


def queue_frame(db, client_id: str, type_: str, body: dict) -> dict:
    """Queue a kill_switch / scrape_job for a client; the dispatcher sends it
    (now if the client is connected, else when it next connects). Raises
    ValueError when the frame would break the protocol."""
    env = envelope(type_, client_id, body)
    bad = schema_error(env)
    if bad is not None:
        raise ValueError(bad[1])
    db.add(HubOutbox(msg_id=env["msg_id"], client_id=client_id, type=type_,
                     frame=json.dumps(env), created_at=utcnow()))
    db.commit()
    conn = registry.get(client_id)
    if conn is not None:
        conn.nudge()
    return env


def unacked_count(db, client_id: str) -> int:
    return (db.query(HubOutbox)
            .filter(HubOutbox.client_id == client_id, HubOutbox.acked_at.is_(None)).count())


def sweep(db, now: datetime | None = None) -> dict[str, int]:
    """Drop rows past their retention. Run hourly by the dispatcher."""
    now = now or utcnow()
    counts = {
        "seen": db.query(HubInboxSeen).filter(HubInboxSeen.seen_at < now - SEEN_RETENTION)
        .delete(synchronize_session=False),
        "outbox": db.query(HubOutbox).filter(HubOutbox.created_at < now - OUTBOX_RETENTION)
        .delete(synchronize_session=False),
        "sent_bets": db.query(HubSentBet).filter(HubSentBet.created_at < now - SENT_BET_RETENTION)
        .delete(synchronize_session=False),
        "tips": db.query(HubTip).filter(HubTip.received_at < now - TIP_RETENTION)
        .delete(synchronize_session=False),
        "escalations": db.query(HubEscalation)
        .filter(HubEscalation.received_at < now - ESCALATION_RETENTION)
        .delete(synchronize_session=False),
    }
    db.commit()
    if any(counts.values()):
        log.info("hub sweep: %s", ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return counts
