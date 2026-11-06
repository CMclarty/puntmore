"""Finder on the Hub (puntmore_hub/finder.py): the new tips Hermes reads, the rows it posts to
Render's /api/finder/rows on the ingest key, the placer's status, and the link without a socket."""
import asyncio
import json
import logging
import urllib.request

import pytest

from puntmore_hub import cli, finder
from puntmore_hub.link.client import RenderLink

NOW_ISO = "2026-11-07T10:00:00+00:00"
TIP_A = {"source_id": "tipster-a", "event": "A v B", "market": "Match Result", "selection": "A",
         "advised_price": 3.0, "tip_time": NOW_ISO, "origin": "tipster"}
TIP_B = {**TIP_A, "event": "C v D", "selection": "C"}
ROW_A = {"tip_id": "a", "origin": "tipster-a", "sport": "football", "bookmaker": "bet365",
         "event": "A v B", "kickoff": "2026-11-07T15:00:00+00:00", "market": "Match Result",
         "selection": "A", "odds": 3.0, "fair": 2.5, "status": "offered", "reason": "3.0 beats 2.5"}


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body if isinstance(body, str) else json.dumps(body)

    def read(self):
        return self._body.encode()


@pytest.fixture
def cfg(cfg):
    return cfg.with_overrides(render_http="https://puntmore.example/", render_api_key="k-ingest")


@pytest.fixture
def cfg_without_ws(cfg):
    return cfg.with_overrides(render_ws_url="")


def test_new_tips_are_those_not_yet_posted(store):
    store.add_tip(TIP_A, tip_id="a"); store.add_tip(TIP_B, tip_id="b")
    assert [t["tip_id"] for t in finder.new_tips(store)] == ["a", "b"]
    store.finder_mark("a", "offered")
    assert [t["tip_id"] for t in finder.new_tips(store)] == ["b"]


def test_a_new_tip_is_the_dict_hermes_reads(store):
    store.add_tip({**TIP_A, "advised_stake_units": 2, "expiry": "2026-11-07T14:00:00+00:00"}, tip_id="a")
    [t] = finder.new_tips(store)
    assert set(t) == {"tip_id", "source_id", "event", "market", "selection", "advised_price",
                      "advised_stake_units", "tip_time", "expiry", "origin", "first_seen"}
    assert (t["source_id"], t["advised_price"], t["advised_stake_units"], t["expiry"]) == \
        ("tipster-a", 3.0, 2, "2026-11-07T14:00:00+00:00")
    store.add_tip(TIP_B, tip_id="b")
    assert [t["tip_id"] for t in finder.new_tips(store, limit=1)] == ["a"]


def test_post_rows_sends_a_bearer_key_and_marks_on_200(store, cfg):
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["auth"], seen["body"] = req.full_url, req.get_header("Authorization"), json.loads(req.data)
        return _Resp(200, {"stored": 1, "updated": 0, "refused": [], "duplicates": []})
    out = finder.post_rows(cfg, [ROW_A], opener=opener, store=store)
    assert seen["url"].endswith("/api/finder/rows") and seen["auth"] == f"Bearer {cfg.render_api_key}"
    assert seen["body"] == {"rows": [ROW_A]} and out["stored"] == 1
    assert finder.new_tips(store) == []


def test_a_posted_tip_leaves_the_new_list(store, cfg):
    store.add_tip(TIP_A, tip_id="a"); store.add_tip(TIP_B, tip_id="b")
    finder.post_rows(cfg, [ROW_A], opener=lambda req, timeout: _Resp(200, {"stored": 1}), store=store)
    assert [t["tip_id"] for t in finder.new_tips(store)] == ["b"]
    assert store.conn.execute("SELECT status FROM finder_posted WHERE tip_id='a'").fetchone()[0] == "offered"


def test_a_failed_post_marks_nothing(store, cfg):
    store.add_tip(TIP_A, tip_id="a")
    with pytest.raises(RuntimeError, match="HTTP 503"):
        finder.post_rows(cfg, [ROW_A], opener=lambda req, timeout: _Resp(503, "down"), store=store)
    assert [t["tip_id"] for t in finder.new_tips(store)] == ["a"]


def test_a_real_http_error_is_a_status_not_an_exception_from_urllib(store, cfg, monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        finder.post_rows(cfg, [ROW_A], store=store)


def test_no_render_base_or_key_is_said_before_anything_is_sent(store, cfg):
    for bad in (cfg.with_overrides(render_http=""), cfg.with_overrides(render_api_key="")):
        with pytest.raises(RuntimeError, match="PUNTMORE_RENDER_"):
            finder.post_rows(bad, [ROW_A], opener=lambda req, timeout: pytest.fail("sent"), store=store)


def test_a_final_row_status_is_reported_not_raised(cfg):
    assert finder.post_status(cfg, "a", "gone", NOW_ISO, opener=lambda req, timeout: _Resp(409, {"detail": "final"})) == {"refused": "final"}


def test_post_status_sends_the_placers_word(cfg):
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return _Resp(200, {"tip_id": "a b", "status": "placed"})
    assert finder.post_status(cfg, "a b", "placed", NOW_ISO, opener=opener)["status"] == "placed"
    assert seen["url"] == "https://puntmore.example/api/finder/rows/a%20b/status"
    assert seen["body"] == {"status": "placed", "at": NOW_ISO}
    with pytest.raises(RuntimeError, match="HTTP 404"):
        finder.post_status(cfg, "nope", "gone", NOW_ISO, opener=lambda req, timeout: _Resp(404, {"detail": "Unknown"}))


def test_the_link_runs_offline_without_a_socket(caplog, cfg_without_ws, store):
    caplog.set_level(logging.INFO, logger="puntmore.link")
    asyncio.run(RenderLink(store, cfg_without_ws).run())
    assert "no socket configured" in caplog.text
    assert caplog.text.count("no socket configured") == 1


def test_the_cli_reads_new_tips_and_posts_a_file(store, cfg, monkeypatch, tmp_path, capsys):
    store.add_tip(TIP_A, tip_id="a")
    monkeypatch.setattr(cli, "_store", lambda: store)
    monkeypatch.setattr(cli, "settings", cfg)
    cli.main(["finder", "new"])
    [line] = capsys.readouterr().out.strip().splitlines()
    assert json.loads(line)["tip_id"] == "a"
    sent = []
    monkeypatch.setattr(finder, "_open", lambda req, timeout: sent.append(json.loads(req.data)) or _Resp(200, {"stored": 1}))
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([ROW_A]))
    cli.main(["finder", "post", str(rows)])
    assert sent == [{"rows": [ROW_A]}] and json.loads(capsys.readouterr().out)["stored"] == 1
    assert finder.new_tips(store) == []
    monkeypatch.setattr(finder, "_open", lambda req, timeout: _Resp(409, {"detail": "final"}))
    cli.main(["finder", "status", "a", "gone"])
    assert json.loads(capsys.readouterr().out) == {"refused": "final"}


def test_the_link_daemon_without_a_socket_polls_the_inbox_in_the_foreground(cfg_without_ws, monkeypatch, caplog):
    import time
    from puntmore_hub import inbox
    calls = []

    def ingest(store, cfg):
        calls.append(cfg.render_ws_url)
        if len(calls) == 3:
            raise KeyboardInterrupt          # stands in for the service being stopped
        return {"tip_files": 0, "events": 0}
    monkeypatch.setattr(cli, "settings", cfg_without_ws)
    monkeypatch.setattr(inbox, "ingest", ingest)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    caplog.set_level(logging.INFO, logger="puntmore.link")
    cli.cmd_link(None)                       # returns only when the loop is stopped
    assert calls == ["", "", ""]             # polled in the foreground, the same process
    assert caplog.text.count("no socket configured") == 1 and "reconnecting" not in caplog.text
