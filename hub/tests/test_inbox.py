"""The Scraper's drop folders -> stored tips for Hermes (never queued for Render), and escalations.

Every file the Scraper writes is one protocol envelope (schemas/protocol.schema.json, types
scraper_tips / scraper_event); `_drop` here writes exactly that and checks it against the schema,
so a drop the Hub's tests ingest is one the Scraper's sinks would write."""
import json
import time
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator

from puntmore_hub import finder, inbox
from puntmore_hub.schemas import utcnow_iso

SCHEMA = Draft202012Validator(json.loads((Path(__file__).resolve().parent.parent / "schemas" / "protocol.schema.json").read_text()))
TYPE_OF = {"tips": "scraper_tips", "events": "scraper_event"}


def _drop(cfg, sub, name, body, *, bare=False, check=True):
    d = cfg.inbox_dir / sub
    d.mkdir(parents=True, exist_ok=True)
    env = body if bare else {"type": TYPE_OF[sub], "client_id": "scraper", "msg_id": uuid.uuid4().hex,
                             "ts": utcnow_iso(), "body": body}
    if check and not bare:
        SCHEMA.validate(env)
    (d / f"{int(time.time() * 1000)}-{name}.json").write_text(json.dumps(env))


def test_tips_are_stored_once_and_not_queued(cfg, store):
    body = {"source": "tips-a", "at": time.time(), "url": "https://a.example.com", "channel": "tips",
            "trust_weight": 0.5, "rows": [
                {"event": "A v B", "market": "Match Result", "selection": "A", "advised_price": 2.1, "origin": "tipster"},
                {"event": "C v D", "market": "Over/Under 2.5", "selection": "Over 2.5", "advised_price": 1.9},
                {"event": "", "market": "x", "selection": "y"},                       # rejected: no event
                {"event": "E v F", "market": "m", "selection": "s", "advised_price": 5000},  # rejected: price
            ]}
    _drop(cfg, "tips", "tips-a", body, check=False)   # the Scraper's sink keeps such rows out; the Hub still checks
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts == {"tip_files": 1, "tips": 2, "new_tips": 2, "rejected": 2, "events": 0}
    # Tips go to Hermes (`puntmore finder new`), never to Render's retired `tip` message.
    assert store.outbox_pending() == []
    new = finder.new_tips(store)
    assert [t["source_id"] for t in new] == ["tips-a", "tips-a"] and new[0]["origin"] == "tipster"
    extra = json.loads(store.conn.execute("SELECT extra FROM tips WHERE tip_id=?", (new[0]["tip_id"],)).fetchone()[0])
    assert extra["trust_weight"] == 0.5
    assert not list((cfg.inbox_dir / "tips").glob("*.json")) and len(list((cfg.inbox_dir / "done").glob("*.json"))) == 1
    # the same rows dropped again (a second sweep) add nothing
    _drop(cfg, "tips", "tips-a", body, check=False)
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["new_tips"] == 0 and store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 2


def test_events_become_escalations(cfg, store):
    _drop(cfg, "events", "tips-a", {"kind": "unhealthy_source", "ref": "tips-a", "detail": "only 0 row(s) - layout change?", "at": time.time()})
    _drop(cfg, "events", "generic", {"kind": "blocked_source", "ref": "generic", "detail": "HTTP 403", "at": time.time()})
    _drop(cfg, "events", "odd", {"kind": "something_new", "ref": "x", "detail": "?", "at": time.time()}, bare=True)
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["events"] == 3
    kinds = sorted(e["kind"] for e in store.open_escalations())
    assert kinds == ["blocked_source", "other", "unhealthy_source"]
    assert not list((cfg.inbox_dir / "events").glob("*.json"))


def test_unreadable_files_are_moved_aside_not_retried_forever(cfg, store):
    d = cfg.inbox_dir / "tips"
    d.mkdir(parents=True)
    (d / "1-bad.json").write_text("{not json")
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["tip_files"] == 0 and not list(d.glob("*.json"))


def test_scraper_status_reads_heartbeat_and_status_file(cfg):
    cfg.scraper_data_dir.mkdir(parents=True)
    assert inbox.scraper_status(cfg)["running"] is False
    (cfg.scraper_data_dir / "heartbeat").write_text(str(int(time.time())))
    (cfg.scraper_data_dir / "status.json").write_text(json.dumps({"targets": [
        {"name": "pp-epl", "site": "paddypower", "mode": "keep", "state": "running", "detail": "3 tabs open", "rows_posted": 10},
        {"name": "tips-a", "site": "generic", "mode": "fetch", "state": "running", "detail": "only 0 row(s), expected at least 2 - layout change?", "rows_posted": 0},
        {"name": "bb-saves", "site": "bookiebashing", "mode": "keep", "state": "blocked", "detail": "backing off", "rows_posted": 0},
    ], "sites": []}))
    w = inbox.scraper_status(cfg)
    assert w["running"] and w["unhealthy"] == ["tips-a"] and w["blocked"] == ["bb-saves"]
    (cfg.scraper_data_dir / "heartbeat").write_text(str(int(time.time()) - 3600))
    assert inbox.scraper_status(cfg)["running"] is False


def test_scraper_data_env_prefers_the_new_name_and_falls_back_to_the_old(monkeypatch):
    from puntmore_hub.config import _env_first
    monkeypatch.delenv("PUNTMORE_SCRAPER_DATA", raising=False)
    monkeypatch.delenv("PUNTMORE_WORKER_DATA", raising=False)
    assert _env_first("PUNTMORE_SCRAPER_DATA", "PUNTMORE_WORKER_DATA", default="/d") == "/d"
    monkeypatch.setenv("PUNTMORE_WORKER_DATA", "/old")
    assert _env_first("PUNTMORE_SCRAPER_DATA", "PUNTMORE_WORKER_DATA", default="/d") == "/old"
    monkeypatch.setenv("PUNTMORE_SCRAPER_DATA", "/new")
    assert _env_first("PUNTMORE_SCRAPER_DATA", "PUNTMORE_WORKER_DATA", default="/d") == "/new"


def test_a_drop_from_before_the_envelope_still_reads(cfg, store):
    """Files already on disk when the Hub updates are bare bodies; they are not stranded."""
    _drop(cfg, "tips", "tips-a", {"source": "tips-a", "at": time.time(), "url": "https://a.example.com",
                                  "channel": "tips", "trust_weight": 0.5,
                                  "rows": [{"event": "A v B", "market": "Match Result", "selection": "A"}]}, bare=True)
    _drop(cfg, "events", "tips-a", {"kind": "blocked_source", "ref": "tips-a", "detail": "HTTP 403", "at": time.time()},
          bare=True)
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["new_tips"] == 1 and counts["events"] == 1
