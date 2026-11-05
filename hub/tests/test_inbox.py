"""The Worker's drop folders -> stored tips queued for Render, and escalations."""
import json
import time

from puntmore_hub import inbox


def _drop(cfg, sub, name, body):
    d = cfg.inbox_dir / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{int(time.time() * 1000)}-{name}.json").write_text(json.dumps(body))


def test_tips_are_stored_once_and_queued(cfg, store):
    body = {"source": "tips-a", "at": time.time(), "url": "https://a.example.com", "channel": "tips",
            "trust_weight": 0.5, "rows": [
                {"event": "A v B", "market": "Match Result", "selection": "A", "advised_price": 2.1, "origin": "tipster"},
                {"event": "C v D", "market": "Over/Under 2.5", "selection": "Over 2.5", "advised_price": 1.9},
                {"event": "", "market": "x", "selection": "y"},                       # rejected: no event
                {"event": "E v F", "market": "m", "selection": "s", "advised_price": 5000},  # rejected: price
            ]}
    _drop(cfg, "tips", "tips-a", body)
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts == {"tip_files": 1, "tips": 2, "new_tips": 2, "rejected": 2, "events": 0}
    assert [r["type"] for r in store.outbox_pending()] == ["tip", "tip"]
    queued = json.loads(store.outbox_pending()[0]["body"])
    assert queued["source_id"] == "tips-a" and queued["extra"]["trust_weight"] == 0.5 and queued["source_scope"] == "owner"
    assert not list((cfg.inbox_dir / "tips").glob("*.json")) and len(list((cfg.inbox_dir / "done").glob("*.json"))) == 1
    # the same rows dropped again (a second sweep) add nothing
    _drop(cfg, "tips", "tips-a", body)
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["new_tips"] == 0 and store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 2


def test_events_become_escalations(cfg, store):
    _drop(cfg, "events", "tips-a", {"kind": "unhealthy_source", "ref": "tips-a", "detail": "only 0 row(s) - layout change?", "at": time.time()})
    _drop(cfg, "events", "generic", {"kind": "blocked_source", "ref": "generic", "detail": "HTTP 403", "at": time.time()})
    _drop(cfg, "events", "odd", {"kind": "something_new", "ref": "x", "detail": "?", "at": time.time()})
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


def test_worker_status_reads_heartbeat_and_status_file(cfg):
    cfg.worker_data_dir.mkdir(parents=True)
    assert inbox.worker_status(cfg)["running"] is False
    (cfg.worker_data_dir / "heartbeat").write_text(str(int(time.time())))
    (cfg.worker_data_dir / "status.json").write_text(json.dumps({"targets": [
        {"name": "pp-epl", "site": "paddypower", "mode": "keep", "state": "running", "detail": "3 tabs open", "rows_posted": 10},
        {"name": "tips-a", "site": "generic", "mode": "fetch", "state": "running", "detail": "only 0 row(s), expected at least 2 - layout change?", "rows_posted": 0},
        {"name": "bb-saves", "site": "bookiebashing", "mode": "keep", "state": "blocked", "detail": "backing off", "rows_posted": 0},
    ], "sites": []}))
    w = inbox.worker_status(cfg)
    assert w["running"] and w["unhealthy"] == ["tips-a"] and w["blocked"] == ["bb-saves"]
    (cfg.worker_data_dir / "heartbeat").write_text(str(int(time.time()) - 3600))
    assert inbox.worker_status(cfg)["running"] is False
