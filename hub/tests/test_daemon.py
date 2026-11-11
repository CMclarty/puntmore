"""The Hub daemon (`puntmore daemon`, also `puntmore link`): the inbox pass over the Scraper's drop
files and the tipster mailbox poll, with no socket anywhere, and what it leaves for the Console."""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from puntmore_hub import aw26_loop, cli, daemon, finder, inbox, mail


class _Time:
    """A clock that moves only when the daemon sleeps, so no test waits in real time."""
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


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
    t = _Time()
    daemon.run(live, sleep=t.sleep, clock=t.clock)    # returns only when the loop is stopped
    assert len(calls) == 3 and all(c is live for c in calls)
    status = json.loads(daemon.status_file(live).read_text())
    counts = status["inbox"]["counts"]        # a price pass may have laid its counts over the full pass's
    assert {k: counts[k] for k in ("tip_files", "events")} == {"tip_files": 1, "events": 0} and status["inbox"]["at"]
    assert "socket" not in caplog.text and "Render" not in caplog.text


def _stop_after(n):
    calls = []

    def ingest(store, c):
        calls.append(c)
        if len(calls) == n:
            raise KeyboardInterrupt
        return {"tip_files": 0, "events": 0}
    return ingest


def test_the_photo_reader_is_off_unless_switched_on(tmp_path):
    """PUNTMORE_AW26_READ: off by default, on at 1 (a fresh interpreter reads the environment)."""
    env = {k: v for k, v in os.environ.items() if k != "PUNTMORE_AW26_READ"}
    env["PUNTMORE_HUB_ROOT"] = str(tmp_path)          # no .env read
    hub = Path(__file__).resolve().parents[1]          # this checkout's package, not an installed one
    read = [sys.executable, "-c", "from puntmore_hub.config import Settings; print(Settings().aw26_read)"]
    assert subprocess.run(read, env=env, cwd=hub, capture_output=True, text=True, check=True).stdout == "False\n"
    on = {**env, "PUNTMORE_AW26_READ": "1"}
    assert subprocess.run(read, env=on, cwd=hub, capture_output=True, text=True, check=True).stdout == "True\n"


def _no_aw26_thread(cfg, monkeypatch, caplog):
    """Run the daemon for two passes; the names of the threads it started."""
    started = []
    monkeypatch.setattr(inbox, "ingest", _stop_after(2))
    monkeypatch.setattr(aw26_loop, "read_once", lambda c, **kw: started.append(c))
    monkeypatch.setattr(threading.Thread, "start", lambda t: started.append(t.name))
    caplog.set_level(logging.INFO, logger="puntmore.daemon")
    t = _Time()
    daemon.run(cfg, sleep=t.sleep, clock=t.clock)
    return started


def test_switched_off_the_daemon_starts_no_aw26_thread(cfg, monkeypatch, caplog):
    live = cfg.with_overrides(render_http="https://puntmore.example", render_api_key="k")
    assert live.aw26_read is False
    assert _no_aw26_thread(live, monkeypatch, caplog) == []
    assert "aw26" not in daemon.last_status(live)
    assert caplog.text.count("AW 26 photo loop not started") == 1
    assert "PUNTMORE_AW26_READ is not 1" in caplog.text and "PUNTMORE_RENDER_HTTP" not in caplog.text


def test_without_render_the_daemon_starts_no_aw26_thread(cfg, monkeypatch, caplog):
    on = cfg.with_overrides(aw26_read=True)
    assert _no_aw26_thread(on, monkeypatch, caplog) == []
    assert "aw26" not in daemon.last_status(on)
    assert caplog.text.count("AW 26 photo loop not started") == 1
    assert "PUNTMORE_RENDER_HTTP" in caplog.text and "PUNTMORE_AW26_READ" not in caplog.text


def test_with_render_the_daemon_reads_photos_on_its_own_thread(cfg, monkeypatch, caplog):
    live = cfg.with_overrides(render_http="https://puntmore.example", render_api_key="k", aw26_read=True)
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
    caplog.set_level(logging.INFO, logger="puntmore.daemon")
    daemon.run(live, sleep=lambda s: None)
    assert names[0] == "aw26"
    assert "AW 26 photo loop not started" not in caplog.text
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


def test_a_pass_that_posted_prices_is_logged(cfg, monkeypatch, caplog):
    calls = []

    def ingest(store, c):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {"tip_files": 0, "events": 0, "price_files": 2, "price_rows": 6, "price_dropped": 0, "price_waiting": 0}
    monkeypatch.setattr(inbox, "ingest", ingest)
    caplog.set_level(logging.INFO, logger="puntmore.daemon")
    t = _Time()
    daemon.run(cfg, sleep=t.sleep, clock=t.clock)
    assert "price_files" in caplog.text


# The price pass every PRICES_EVERY_S (10 s) between the full passes (INBOX_EVERY_S, 30 s).

PRICE_COUNTS = {"price_files": 0, "price_rows": 0, "price_dropped": 0, "price_waiting": 0}


def _stop_at(t, when):
    """The daemon's sleep, stopping the loop (as SIGTERM would) once fake time reaches `when`."""
    def sleep(s):
        t.sleep(s)
        if t.now >= when:
            raise KeyboardInterrupt
    return sleep


def test_one_full_pass_then_two_price_passes_every_thirty_seconds(cfg, monkeypatch):
    assert (daemon.PRICES_EVERY_S, daemon.INBOX_EVERY_S) == (10, 30)
    t, order = _Time(), []

    def ingest(store, c):
        order.append(("full", t.now))
        return {"tip_files": 0, "events": 0, **PRICE_COUNTS}

    def post_prices(c):
        order.append(("prices", t.now))
        return dict(PRICE_COUNTS)
    monkeypatch.setattr(inbox, "ingest", ingest)
    monkeypatch.setattr(inbox, "post_prices", post_prices)
    daemon.run(cfg, sleep=_stop_at(t, 40), clock=t.clock)
    assert order == [("full", 0), ("prices", 10), ("prices", 20), ("full", 30)]


def test_a_price_drop_is_posted_at_the_next_ten_second_wake(cfg, monkeypatch):
    t, sent = _Time(), []
    c = cfg.with_overrides(render_http="https://puntmore.example/", render_api_key="k-ingest")

    class _Resp:
        status = 200

        def read(self):
            return b'{"stored": 1}'

    def render(req, timeout):
        sent.append((t.now, req.full_url, json.loads(req.data)))
        return _Resp()
    monkeypatch.setattr(finder, "_open", render)
    row = {"home": "A", "away": "B", "kickoff": "2026-11-08T16:30:00Z", "market": "most_corners",
           "home_price": 1.5, "tie_price": 9.0, "away_price": 2.4}

    def sleep(s):
        if t.now == 0:                            # the Scraper drops a file just after the full pass
            d = c.inbox_dir / "prices"
            d.mkdir(parents=True)
            (d / "1-pp-epl-most-abcd1234.json").write_text(json.dumps(
                {"source": "pp-epl-most", "at": time.time(), "url": "u", "route": "/api/paddy/most-prices", "rows": [row]}))
        _stop_at(t, 20)(s)
    daemon.run(c, sleep=sleep, clock=t.clock)
    assert sent == [(10, "https://puntmore.example/api/paddy/most-prices", {"prices": [row]})]
    assert daemon.last_status(c)["inbox"]["counts"]["price_files"] == 1


def test_a_price_pass_keeps_the_full_passs_tip_counts_and_updates_the_prices(cfg, monkeypatch):
    t = _Time()
    monkeypatch.setattr(inbox, "ingest", lambda store, c: {"tip_files": 1, "tips": 2, "new_tips": 2, "rejected": 0,
                                                           "events": 1, **PRICE_COUNTS, "price_waiting": 4})
    monkeypatch.setattr(inbox, "post_prices", lambda c: {**PRICE_COUNTS, "price_files": 3, "price_rows": 9})
    daemon.run(cfg, sleep=_stop_at(t, 20), clock=t.clock)
    rec = daemon.last_status(cfg)["inbox"]
    assert rec["counts"] == {"tip_files": 1, "tips": 2, "new_tips": 2, "rejected": 0, "events": 1,
                             "price_files": 3, "price_rows": 9, "price_dropped": 0, "price_waiting": 0}
    assert rec["error"] is None


def test_a_price_pass_over_an_empty_folder_logs_nothing(cfg, monkeypatch, caplog):
    t = _Time()
    (cfg.inbox_dir / "prices").mkdir(parents=True)
    monkeypatch.setattr(inbox, "ingest", lambda store, c: {"tip_files": 0, "events": 0, **PRICE_COUNTS})
    passes = []
    real = inbox.post_prices
    monkeypatch.setattr(inbox, "post_prices", lambda c: passes.append(1) or real(c))
    caplog.set_level(logging.DEBUG)
    daemon.run(cfg, sleep=_stop_at(t, 30), clock=t.clock)
    assert len(passes) == 2
    logged = [r.getMessage() for r in caplog.records if r.name in ("puntmore.daemon", "puntmore.inbox")]
    assert [m for m in logged if not m.startswith("AW 26 photo loop not started")] == []   # the start-up line only:
    # the reader is off by default and says so once at start-up, whatever the passes do


def test_a_price_pass_that_only_left_files_waiting_writes_no_prices_line(cfg, monkeypatch, caplog):
    """While Render is down the inbox warns once per pass; the daemon adds no `prices:` line of
    its own unless a file was posted or dropped."""
    t, passes = _Time(), []
    monkeypatch.setattr(inbox, "ingest", lambda store, c: {"tip_files": 0, "events": 0, **PRICE_COUNTS})

    def post_prices(c):
        passes.append(t.now)
        return {**PRICE_COUNTS, "price_waiting": 3}
    monkeypatch.setattr(inbox, "post_prices", post_prices)
    caplog.set_level(logging.INFO, logger="puntmore.daemon")
    daemon.run(cfg, sleep=_stop_at(t, 30), clock=t.clock)
    assert passes == [10, 20]
    assert [r for r in caplog.records if r.getMessage().startswith("prices:")] == []


@pytest.mark.parametrize("counts", [{"price_files": 1, "price_rows": 2}, {"price_dropped": 1}])
def test_a_price_pass_that_posted_or_dropped_a_file_logs_one_prices_line(cfg, monkeypatch, caplog, counts):
    t = _Time()
    monkeypatch.setattr(inbox, "ingest", lambda store, c: {"tip_files": 0, "events": 0, **PRICE_COUNTS})
    monkeypatch.setattr(inbox, "post_prices", lambda c: {**PRICE_COUNTS, "price_waiting": 2, **counts})
    caplog.set_level(logging.INFO, logger="puntmore.daemon")
    daemon.run(cfg, sleep=_stop_at(t, 20), clock=t.clock)
    assert len([r for r in caplog.records if r.getMessage().startswith("prices:")]) == 1


def test_a_price_pass_that_raises_is_recorded_and_the_loop_goes_on(cfg, monkeypatch, caplog):
    t, calls = _Time(), []
    monkeypatch.setattr(inbox, "ingest", lambda store, c: {"tip_files": 1, "events": 0, **PRICE_COUNTS})

    def post_prices(c):
        calls.append(t.now)
        if len(calls) == 1:
            raise RuntimeError("boom")
        assert "RuntimeError: boom" in daemon.last_status(cfg)["inbox"]["error"]
        return dict(PRICE_COUNTS)
    monkeypatch.setattr(inbox, "post_prices", post_prices)
    daemon.run(cfg, sleep=_stop_at(t, 40), clock=t.clock)
    assert calls == [10, 20]
    rec = daemon.last_status(cfg)["inbox"]
    assert rec["error"] is None and rec["counts"]["tip_files"] == 1       # the full pass at 30 s cleared it
    assert "price pass failed" in caplog.text


def test_files_moved_aside_stay_on_record_until_a_pass_handles_another_file(cfg):
    """The Scraper drops a price file about once a minute and the price pass runs every 10 s, so
    most passes find nothing. Such a pass says nothing new about prices: the last files moved
    aside, and why, stay on the record (what the Console's Daemon card warns on) until a pass
    handles a file, whether a full pass or a price pass."""
    nothing = {**PRICE_COUNTS, "price_dropped_reason": None, "price_waiting_reason": None}
    daemon.record_inbox(cfg, {"tip_files": 0, "events": 0, **nothing})
    daemon.record_inbox(cfg, {**nothing, "price_dropped": 1, "price_dropped_reason": "Render refused HTTP 403"},
                        prices_only=True)
    daemon.record_inbox(cfg, dict(nothing), prices_only=True)
    daemon.record_inbox(cfg, {"tip_files": 1, "events": 0, **nothing})
    counts = daemon.last_status(cfg)["inbox"]["counts"]
    assert counts["tip_files"] == 1
    assert (counts["price_dropped"], counts["price_dropped_reason"]) == (1, "Render refused HTTP 403")
    daemon.record_inbox(cfg, {**nothing, "price_files": 1, "price_rows": 3}, prices_only=True)
    counts = daemon.last_status(cfg)["inbox"]["counts"]
    assert (counts["price_files"], counts["price_dropped"], counts["price_dropped_reason"]) == (1, 0, None)


def test_files_waiting_are_never_carried_over(cfg):
    """Waiting files are still on disk, so the next pass counts them again; a pass that finds
    none means they are gone and the record says so."""
    nothing = {**PRICE_COUNTS, "price_dropped_reason": None, "price_waiting_reason": None}
    daemon.record_inbox(cfg, {"tip_files": 0, "events": 0, **nothing, "price_waiting": 2,
                              "price_waiting_reason": "HTTP 503"})
    daemon.record_inbox(cfg, dict(nothing), prices_only=True)
    counts = daemon.last_status(cfg)["inbox"]["counts"]
    assert (counts["price_waiting"], counts["price_waiting_reason"]) == (0, None)
