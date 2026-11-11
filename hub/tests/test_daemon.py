"""The Hub daemon (`puntmore daemon`, also `puntmore link`): the inbox pass over the Scraper's drop
files and the tipster mailbox poll, with no socket anywhere, and what it leaves for the Console."""
import json
import logging
import threading

from puntmore_hub import aw26_loop, cli, daemon, inbox, mail


def test_the_daemon_runs_inbox_and_mail_without_a_socket(cfg, monkeypatch, caplog):
    live = cfg.with_overrides(imap_host="imap.example")
    calls, polled = [], threading.Event()

    def ingest(store, c):
        calls.append(c)
        if len(calls) == 3:
            assert polled.wait(5), "the mail thread never polled"
            raise KeyboardInterrupt          # stands in for the service being stopped
        return {"tip_files": 1, "events": 0}

    def poll(store, c):
        polled.set()
        return {k: 0 for k in mail.COUNT_KEYS} | {"error": None}

    monkeypatch.setattr(inbox, "ingest", ingest)
    monkeypatch.setattr(mail, "poll", poll)
    caplog.set_level(logging.INFO, logger="puntmore.daemon")
    daemon.run(live, sleep=lambda s: None)    # returns only when the loop is stopped
    assert len(calls) == 3 and all(c is live for c in calls)
    status = json.loads(daemon.status_file(live).read_text())
    assert status["inbox"]["counts"] == {"tip_files": 1, "events": 0} and status["inbox"]["at"]
    assert "socket" not in caplog.text and "Render" not in caplog.text


def _stop_after(n):
    calls = []

    def ingest(store, c):
        calls.append(c)
        if len(calls) == n:
            raise KeyboardInterrupt
        return {"tip_files": 0, "events": 0}
    return ingest


def test_without_render_the_daemon_starts_no_aw26_thread(cfg, monkeypatch):
    started = []
    monkeypatch.setattr(inbox, "ingest", _stop_after(2))
    monkeypatch.setattr(aw26_loop, "read_once", lambda c, **kw: started.append(c))
    monkeypatch.setattr(threading.Thread, "start", lambda t: started.append(t.name))
    daemon.run(cfg, sleep=lambda s: None)
    assert started == []
    assert "aw26" not in daemon.last_status(cfg)


def test_with_render_the_daemon_reads_photos_on_its_own_thread(cfg, monkeypatch):
    live = cfg.with_overrides(render_http="https://puntmore.example", render_api_key="k")
    passed, names = threading.Event(), []

    def read_once(c):
        names.append(threading.current_thread().name)
        passed.set()
        return {"photo": 5, "outcome": "recorded"}

    def ingest(store, c):
        assert passed.wait(5), "the aw26 thread never ran a pass"
        raise KeyboardInterrupt
    monkeypatch.setattr(inbox, "ingest", ingest)
    monkeypatch.setattr(aw26_loop, "read_once", read_once)
    daemon.run(live, sleep=lambda s: None)
    assert names[0] == "aw26"
    status = daemon.last_status(live)["aw26"]
    assert status["counts"] == {"photo": 5, "outcome": "recorded"} and status["at"] and status["error"] is None


def test_puntmore_link_and_puntmore_daemon_are_the_same_command(cfg, monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "settings", cfg)
    monkeypatch.setattr(daemon, "run", lambda c, **kw: ran.append(c))
    cli.main(["daemon"])
    cli.main(["link"])
    assert ran == [cfg, cfg]
    assert "link-check" not in cli.build_parser().format_help()


def test_the_wire_is_gone():
    from pathlib import Path

    import puntmore_hub
    from puntmore_hub import schemas, store
    # (not importlib's find_spec: an editable install of another checkout would answer it)
    assert not (Path(puntmore_hub.__file__).parent / "link").exists()
    for name in ("Envelope", "CandidateTip", "ScrapeJob", "INBOUND_TYPES", "OUTBOUND_TYPES"):
        assert not hasattr(schemas, name), name
    assert not any(n.startswith("outbox") for n in dir(store.Store))
    from puntmore_hub.config import Settings
    fields = {f.name for f in __import__("dataclasses").fields(Settings)}
    assert not fields & {"render_ws_url", "client_id"}


def test_resolving_an_escalation_queues_nothing(store, cfg):
    from puntmore_hub import escalation
    esc = escalation.raise_escalation(store, "failed_bet", "b-1", "x", cfg=cfg, notify=False)
    escalation.resolve(store, esc, "dropped it")
    assert store.escalation(esc)["outcome"] == "dropped it"
    tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "outbox" not in tables and "inbox_seen" not in tables
