"""ask.py: a request for the running service (a JSON file in data/requests/),
and its result (data/requests/done/). No browser, no page."""
import json
import re
import time
from datetime import datetime, timezone

import pytest

from scraper import ask, config

PP = "https://www.paddypower.com/football/arsenal-v-chelsea-35912345"


def _job(name="pp-epl-most", site="paddypower", matches=True):
    return config.Target(name=name, site=site, url="https://www.paddypower.com/football/english-premier-league",
                         matches=config.Matches() if matches else None)


TARGETS = [_job(), _job("bb-saves", "bookiebashing", matches=False), _job("pp-page", matches=False)]


def _refused(raw, targets=TARGETS) -> str:
    with pytest.raises(ask.Refused) as exc:
        ask.parse(raw, targets=targets)
    return exc.value.reason


# --- parse ------------------------------------------------------------------
def test_a_read_by_job_and_range():
    req = ask.parse({"id": "r1", "kind": "read", "job": "pp-epl-most",
                     "from": "2026-11-28T12:00:00+00:00", "to": "2026-11-29T23:59:00+00:00",
                     "parser": "pp_most", "keep": True}, targets=TARGETS)
    assert (req.id, req.kind, req.job, req.urls, req.parser, req.keep) == \
        ("r1", "read", "pp-epl-most", (), "pp_most", True)
    assert req.start == datetime(2026, 11, 28, 12, tzinfo=timezone.utc).timestamp()
    assert req.end == datetime(2026, 11, 29, 23, 59, tzinfo=timezone.utc).timestamp()


def test_a_time_with_no_zone_is_london():
    req = ask.parse({"id": "r1", "kind": "read", "job": "pp-epl-most", "from": "2026-07-01T12:00",
                     "to": "2026-07-01T13:00", "parser": "pp_most"}, targets=TARGETS)
    assert req.start == datetime(2026, 7, 1, 11, tzinfo=timezone.utc).timestamp()   # BST
    assert req.keep is False                                                        # the default


def test_a_read_by_urls():
    req = ask.parse({"id": "r2", "kind": "read", "urls": [PP, PP.replace("arsenal", "spurs")],
                     "parser": None, "keep": True})
    assert req.urls == (PP, PP.replace("arsenal", "spurs"))
    assert (req.job, req.start, req.end, req.parser) == (None, None, None, None)


def test_localhost_is_a_paddy_page_only_under_the_tests_flag(monkeypatch):
    local = {"id": "r", "kind": "read", "urls": ["http://127.0.0.1:8123/football/a-v-b"], "keep": True}
    assert config.LOCAL_PADDY is False                       # the service's default
    assert _refused(local) == "not a Paddy Power page: http://127.0.0.1:8123/football/a-v-b"
    assert not config.paddy_page("http://localhost/football/a-v-b")
    monkeypatch.setattr(config, "LOCAL_PADDY", True)         # what the tests' fake site sets
    assert ask.parse(local).urls


def test_a_read_runs_only_parsers_that_click_nothing():
    assert _refused({"id": "r", "kind": "read", "urls": [PP], "parser": "bb_saves"}) == \
        "parser 'bb_saves' may not be run by a read: only parsers that click nothing (pp_most)"
    assert ask.parse({"id": "r", "kind": "read", "urls": [PP], "parser": "pp_most"}).parser == "pp_most"


def test_a_refusal_never_carries_a_query():
    url = "https://example.com/x?_ak=SECRET"
    assert _refused({"id": "r", "kind": "read", "urls": [url], "keep": True}) == \
        "not a Paddy Power page: https://example.com/x"
    assert ask.bare("https://www.paddypower.com/a/b?_ak=S#t") == "https://www.paddypower.com/a/b"
    assert ask.scrub("Timeout navigating to https://x.paddypower.com/p?_ak=S then wss://y/s?t=S") == \
        "Timeout navigating to https://x.paddypower.com/p then wss://y/s"


def test_unknown_kind():
    assert _refused({"id": "r", "kind": "place", "urls": [PP]}) == "unknown kind 'place' (known: read)"


def test_unknown_parser():
    assert _refused({"id": "r", "kind": "read", "urls": [PP], "parser": "pp_nope"}) == \
        "no parser called 'pp_nope'"


@pytest.mark.parametrize("url", ["https://www.bookiebashing.net/tools/daily/",
                                 "https://example.com/football/a-v-b",
                                 "https://paddypower.com.example.com/x",
                                 "ftp://www.paddypower.com/x", "not a url"])
def test_a_url_off_paddy_power(url):
    assert _refused({"id": "r", "kind": "read", "urls": [PP, url], "keep": True}) == \
        f"not a Paddy Power page: {url}"


def test_more_than_60_urls():
    urls = [f"{PP}-{i}" for i in range(61)]
    assert _refused({"id": "r", "kind": "read", "urls": urls, "keep": True}) == \
        "at most 60 urls in one request (got 61)"
    assert len(ask.parse({"id": "r", "kind": "read", "urls": urls[:60], "keep": True}).urls) == 60


def test_from_after_to():
    assert _refused({"id": "r", "kind": "read", "job": "pp-epl-most", "parser": "pp_most",
                     "from": "2026-11-29T12:00:00+00:00", "to": "2026-11-28T12:00:00+00:00"}) == \
        "`from` is after `to`"


@pytest.mark.parametrize("raw, why", [
    ([], "the request is not a JSON object"),
    ({"kind": "read", "urls": [PP]}, "id must be 1-64 letters, digits, '.', '_' or '-'"),
    ({"id": "../x", "kind": "read", "urls": [PP]}, "id must be 1-64 letters, digits, '.', '_' or '-'"),
    ({"id": "r", "kind": "read"}, "name a job with from and to, or urls"),
    ({"id": "r", "kind": "read", "urls": [PP], "job": "pp-epl-most"},
     "name a job with from and to, or urls - not both"),
    ({"id": "r", "kind": "read", "urls": []}, "urls is empty"),
    ({"id": "r", "kind": "read", "urls": PP}, "urls must be a list of addresses"),
    ({"id": "r", "kind": "read", "job": "pp-epl-most", "from": "2026-11-28T12:00:00+00:00",
      "parser": "pp_most"}, "a job needs both from and to"),
    ({"id": "r", "kind": "read", "job": "pp-epl-most", "from": "Saturday", "to": "2026-11-28T12:00",
      "parser": "pp_most"}, "`from` is not an ISO time: 'Saturday'"),
    ({"id": "r", "kind": "read", "job": "nope", "from": "2026-11-28T12:00", "to": "2026-11-28T13:00",
      "parser": "pp_most"}, "no Paddy Power matches job called 'nope'"),
    ({"id": "r", "kind": "read", "job": "bb-saves", "from": "2026-11-28T12:00", "to": "2026-11-28T13:00",
      "parser": "pp_most"}, "no Paddy Power matches job called 'bb-saves'"),
    ({"id": "r", "kind": "read", "job": "pp-page", "from": "2026-11-28T12:00", "to": "2026-11-28T13:00",
      "parser": "pp_most"}, "no Paddy Power matches job called 'pp-page'"),
    ({"id": "r", "kind": "read", "urls": [PP], "keep": "yes"}, "keep must be true or false"),
    ({"id": "r", "kind": "read", "urls": [PP]}, "nothing to do: no parser, and keep is false"),
    ({"id": "r", "kind": "read", "urls": [PP], "keep": True, "click": "Bet Builder"},
     "unknown field(s): click"),
])
def test_other_refusals(raw, why):
    assert _refused(raw) == why


def test_a_job_is_checked_only_against_the_targets_given():
    """The CLI and the runner pass their targets; without them a job name is
    taken as it is (the runner checks it again when it starts the request)."""
    assert ask.parse({"id": "r", "kind": "read", "job": "anything", "from": "2026-11-28T12:00",
                      "to": "2026-11-28T13:00", "parser": "pp_most"}).job == "anything"


# --- pending, write_result ------------------------------------------------------
def _drop(tmp_path, rid, body, age=0.0):
    req = tmp_path / "requests"
    req.mkdir(parents=True, exist_ok=True)
    f = req / f"ask-{rid}.json"
    f.write_text(body if isinstance(body, str) else json.dumps(body))
    t = time.time() - 100 + age
    import os
    os.utime(f, (t, t))
    return f


def _result(tmp_path, rid):
    return json.loads((tmp_path / "requests" / "done" / f"ask-{rid}.json").read_text())


def test_pending_takes_the_oldest_and_marks_it_running(tmp_path):
    _drop(tmp_path, "b", {"id": "b", "kind": "read", "urls": [PP], "keep": True}, age=2)
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "urls": [PP], "keep": True}, age=1)
    req = ask.pending(tmp_path, now=1_000.0)
    assert req.id == "a"
    assert not (tmp_path / "requests" / "ask-a.json").exists()      # taken
    body = _result(tmp_path, "a")
    assert body == {"id": "a", "kind": "read", "state": "running", "reason": None,
                    "started": ask.iso(1_000.0), "finished": None, "listings": [], "items": []}


def test_a_second_request_while_one_runs_is_refused(tmp_path):
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "urls": [PP], "keep": True})
    assert ask.pending(tmp_path).id == "a"
    _drop(tmp_path, "b", {"id": "b", "kind": "read", "urls": [PP], "keep": True})
    assert ask.pending(tmp_path, busy=True) is None
    assert not (tmp_path / "requests" / "ask-b.json").exists()
    b = _result(tmp_path, "b")
    assert (b["state"], b["reason"], b["items"]) == ("refused", "another request is running", [])
    assert _result(tmp_path, "a")["state"] == "running"               # untouched


def test_busy_is_the_runners_own_request_not_a_file(tmp_path):
    """A result left `running` on disk (written by hand, or one whose end was
    never written) does not refuse the next request: only the runner knows
    whether it is answering one."""
    ask.write_result(tmp_path, ask.result("old", "read", "running"))
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "urls": [PP], "keep": True})
    assert ask.pending(tmp_path).id == "a"


def test_a_directory_and_an_oversized_file_are_refused_and_the_next_taken(tmp_path):
    folder = tmp_path / "requests"
    (folder / "ask-dir.json").mkdir(parents=True)
    (folder / "ask-dir.json" / "x").write_text("x")
    _drop(tmp_path, "big", '{"id": "big", "pad": "' + "x" * (ask.MAX_BYTES + 10) + '"}', age=1)
    _drop(tmp_path, "deep", "[" * 200_000 + "]" * 200_000, age=2)
    _drop(tmp_path, "ok", {"id": "ok", "kind": "read", "urls": [PP], "keep": True}, age=3)
    import os
    os.utime(folder / "ask-dir.json", (time.time() - 200, time.time() - 200))
    assert ask.pending(tmp_path).id == "ok"
    assert _result(tmp_path, "dir")["reason"] == "not a request file"
    assert _result(tmp_path, "big")["reason"] == "the request is over 1 MB"
    assert _result(tmp_path, "deep")["reason"] == "the request could not be read: RecursionError"
    assert list(folder.glob("ask-*")) == []                       # the directory moved aside
    assert [p.name.startswith("ask-dir.json-") for p in (folder / "rejected").iterdir()] == [True]


def test_a_file_with_no_id_in_its_name_is_moved_aside(tmp_path):
    _drop(tmp_path, "bad name", {"id": "x"})
    _drop(tmp_path, "ok", {"id": "ok", "kind": "read", "urls": [PP], "keep": True}, age=1)
    assert ask.pending(tmp_path).id == "ok"
    assert [p.name.startswith("ask-bad name.json-") for p in (tmp_path / "requests" / "rejected").iterdir()] \
        == [True]


def test_a_request_is_never_removed_before_its_result_is_written(tmp_path, monkeypatch):
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "urls": [PP], "keep": True})
    _drop(tmp_path, "b", {"id": "b", "kind": "place"}, age=1)
    said = []
    monkeypatch.setattr(ask, "write_result", lambda data_dir, body: False)    # a full disk
    assert ask.pending(tmp_path, unwritten=said.append) is None
    assert (tmp_path / "requests" / "ask-a.json").exists()
    assert (tmp_path / "requests" / "ask-b.json").exists()
    assert said == ["a"]                                      # the oldest; tried again next tick
    monkeypatch.undo()
    assert ask.pending(tmp_path).id == "a"


def test_a_malformed_request_is_refused_with_a_reason_and_the_next_taken(tmp_path):
    _drop(tmp_path, "bad", "{not json", age=1)
    _drop(tmp_path, "worse", {"id": "worse", "kind": "read", "urls": ["https://example.com/"],
                              "keep": True}, age=2)
    _drop(tmp_path, "good", {"id": "good", "kind": "read", "urls": [PP], "keep": True}, age=3)
    assert ask.pending(tmp_path).id == "good"
    bad = _result(tmp_path, "bad")
    assert bad["state"] == "refused" and bad["reason"].startswith("the request is not JSON: ")
    assert _result(tmp_path, "worse")["reason"] == "not a Paddy Power page: https://example.com/"
    assert list((tmp_path / "requests").glob("ask-*")) == []


def test_an_id_that_differs_from_its_file_is_refused(tmp_path):
    _drop(tmp_path, "a", {"id": "b", "kind": "read", "urls": [PP], "keep": True})
    assert ask.pending(tmp_path) is None
    assert _result(tmp_path, "a")["reason"] == "the id 'b' is not the file's ('a')"


def test_a_request_already_answered_is_not_run_again(tmp_path):
    """The service stopped after it took a request and before the file went:
    the request is not started a second time."""
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "urls": [PP], "keep": True})
    ask.write_result(tmp_path, {"id": "a", "kind": "read", "state": "interrupted", "reason": "x",
                                "started": ask.iso(1.0), "finished": ask.iso(2.0), "items": []})
    assert ask.pending(tmp_path) is None
    assert not (tmp_path / "requests" / "ask-a.json").exists()
    assert _result(tmp_path, "a")["state"] == "interrupted"            # the first answer stands


def test_pending_checks_a_job_against_the_targets(tmp_path):
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "job": "nope", "from": "2026-11-28T12:00",
                          "to": "2026-11-28T13:00", "parser": "pp_most"})
    assert ask.pending(tmp_path, targets=TARGETS) is None
    assert _result(tmp_path, "a")["reason"] == "no Paddy Power matches job called 'nope'"


def test_write_result_is_atomic_and_leaves_no_temp_file(tmp_path):
    ask.write_result(tmp_path, {"id": "x", "kind": "read", "state": "done", "reason": None,
                                "started": None, "finished": None, "items": []})
    assert [p.name for p in (tmp_path / "requests" / "done").iterdir()] == ["ask-x.json"]


def test_interrupted_on_start(tmp_path):
    """A result left `running` by a service that stopped is rewritten
    `interrupted`, with what it had done; one that finished is left alone."""
    item = {"url": PP, "home": "Arsenal", "away": "Chelsea", "kickoff": None, "state": "read",
            "reason": None, "rows": [], "saved": None}
    ask.write_result(tmp_path, {"id": "a", "kind": "read", "state": "running", "reason": None,
                                "started": ask.iso(1.0), "finished": None, "items": [item]})
    ask.write_result(tmp_path, {"id": "b", "kind": "read", "state": "done", "reason": None,
                                "started": ask.iso(1.0), "finished": ask.iso(2.0), "items": []})
    assert ask.interrupt_running(tmp_path, now=5.0) == ["a"]
    a = _result(tmp_path, "a")
    assert (a["state"], a["finished"], a["items"]) == ("interrupted", ask.iso(5.0), [item])
    assert a["reason"] == "the Scraper restarted mid-request; the items listed were done"
    assert _result(tmp_path, "b")["state"] == "done"


def test_a_runner_rewrites_a_running_result_when_it_starts(tmp_path):
    from scraper.alerts import Alerts
    from scraper.guard import Guard
    from scraper.runner import Runner
    ask.write_result(tmp_path, {"id": "a", "kind": "read", "state": "running", "reason": None,
                                "started": ask.iso(1.0), "finished": None, "items": []})
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=1, targets=[])
    Runner(s, None, Guard(tmp_path / "g.sqlite3"), Alerts(tmp_path / "alerts.log", None),
           tmp_path / "samples")
    assert _result(tmp_path, "a")["state"] == "interrupted"


def test_payloads_keep_the_path_never_the_query():
    """A captured request url carries Paddy's app key in its query: only the
    path is written."""
    class Cap:
        recent = [(1.0, "http", "https://www.paddypower.com/smspp/event-page/v5?_ak=SECRET&x=1", "{}")]
        pinned = [(2.0, "ws", "wss://push.paddypower.com/socket?token=SECRET", "[]", "tab:Corners")]
    body = ask.payloads(Cap())
    assert body == {"recent": [{"ts": 1.0, "kind": "http", "path": "/smspp/event-page/v5", "text": "{}"}],
                    "pinned": [{"ts": 2.0, "kind": "ws", "path": "/socket", "text": "[]",
                                "label": "tab:Corners"}]}
    assert "SECRET" not in json.dumps(body)


# --- the runner, with no browser ------------------------------------------------
def _runner(tmp_path, targets=()):
    from scraper.alerts import Alerts
    from scraper.guard import Guard
    from scraper.runner import Runner
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=1, targets=list(targets))
    g = Guard(tmp_path / "g.sqlite3")
    g.quiet = (0, 0)
    return Runner(s, None, g, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples")


JOB = {"id": "j", "kind": "read", "job": "pp-epl-most", "from": "2026-11-28T12:00",
       "to": "2026-11-29T23:00", "parser": "pp_most"}


def test_a_job_whose_listing_cannot_be_loaded_is_stopped(tmp_path):
    """The job's listing is not open, so the request loads it (no browser
    here: the load fails), and with no listing read it stops."""
    r = _runner(tmp_path, TARGETS)
    _drop(tmp_path, "j", JOB)
    r._requests(now=1_000.0)
    body = _result(tmp_path, "j")
    assert (body["state"], body["reason"]) == ("stopped", "no listing of pp-epl-most could be read")
    (listing,) = body["listings"]
    assert listing["url"] == TARGETS[0].url and listing["state"] == "failed"
    assert listing["reason"].startswith("load failed: AttributeError")
    assert r.asking is None and r.guard.requested("paddypower", 1_000.0) == (1, 1)
    assert "j: stopped" in (tmp_path / "logs" / "ask.log").read_text()


def test_a_job_request_in_a_cooloff_is_stopped(tmp_path):
    r = _runner(tmp_path, TARGETS)
    r.guard.start_cooloff("paddypower", "Cloudflare check", 900.0)
    _drop(tmp_path, "j", JOB)
    r._requests(now=1_000.0)
    body = _result(tmp_path, "j")
    assert (body["state"], body["items"]) == ("stopped", [])
    assert body["reason"] == "paddypower is cooling off (Cloudflare check) - nothing more loaded"


def test_a_job_the_runner_does_not_have_is_refused(tmp_path):
    r = _runner(tmp_path, [])
    _drop(tmp_path, "j", JOB)
    r._requests(now=1_000.0)
    assert _result(tmp_path, "j")["reason"] == "no Paddy Power matches job called 'pp-epl-most'"


def test_a_request_that_fails_inside_ends_stopped_and_the_next_is_taken(tmp_path, monkeypatch):
    """Something going wrong mid-request never leaves it `running` (which
    would refuse every later request until a restart)."""
    r = _runner(tmp_path, [])
    monkeypatch.setattr(r, "_ask_read", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "urls": [PP], "keep": True})
    r._requests(now=1_000.0)
    a = _result(tmp_path, "a")
    assert (a["state"], a["reason"]) == ("stopped", "failed: RuntimeError: boom")
    assert [i["state"] for i in a["items"]] == ["skipped"]
    _drop(tmp_path, "b", {"id": "b", "kind": "read", "urls": [PP], "keep": True})
    r._requests(now=2_000.0)
    assert _result(tmp_path, "b")["state"] == "stopped"           # taken, not "another is running"


# --- the CLI: python -m scraper ask '<json>' -------------------------------------
@pytest.fixture
def cli(tmp_path, monkeypatch):
    """The CLI against tmp_path, a heartbeat saying the service runs, and a
    'service' that answers inside the CLI's wait (its sleep)."""
    from scraper import __main__ as cli
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "p", channel=None, alert_webhook=None,
                        healthcheck_url=None, capture_mb=1, targets=list(TARGETS))
    (tmp_path / "heartbeat").write_text(str(int(time.time())))
    clock = [0.0]
    monkeypatch.setattr(cli, "_clock", lambda: clock[0])

    def run(argv, answer=None):
        def sleep(sec):
            clock[0] += sec
            if answer:
                req = ask.pending(tmp_path)
                if req is not None:
                    ask.write_result(tmp_path, {**ask.read_result(tmp_path, req.id), **answer})
        monkeypatch.setattr(cli, "_sleep", sleep)
        with pytest.raises(SystemExit) as exc:
            cli.ask_cmd(s, argv)
        return exc.value.code
    return run


def test_ask_writes_the_request_waits_and_exits_0_on_done(tmp_path, cli, capsys):
    item = {"url": PP, "home": "Arsenal", "away": "Chelsea", "kickoff": ask.iso(1.0), "state": "read",
            "reason": None, "rows": [{"x": 1}], "saved": None}
    code = cli([json.dumps({"id": "c1", "kind": "read", "urls": [PP], "parser": "pp_most"}), "--json"],
               answer={"state": "done", "items": [item], "finished": ask.iso(2.0)})
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["id"], out["state"], out["items"]) == ("c1", "done", [item])
    assert list((tmp_path / "requests").glob("*ask-*")) == []      # taken; no temp file left


def test_ask_gives_an_id_when_none_is_named_and_prints_the_items(tmp_path, cli, capsys):
    item = {"url": PP, "home": "Arsenal", "away": "Chelsea", "kickoff": ask.iso(1.0), "state": "read",
            "reason": None, "rows": [{"x": 1}, {"x": 2}], "saved": "/d/ask-x-1"}
    assert cli([json.dumps({"kind": "read", "urls": [PP], "parser": "pp_most"})],
               answer={"state": "done", "items": [item],
                       "listings": [{"url": "https://www.paddypower.com/l", "state": "loaded",
                                     "reason": None}]}) == 0
    out = capsys.readouterr().out
    assert "  listing  loaded   https://www.paddypower.com/l" in out
    assert "done" in out and "Arsenal v Chelsea" in out and "2 row(s)" in out and "/d/ask-x-1" in out
    (done,) = (tmp_path / "requests" / "done").glob("ask-*.json")
    assert re.fullmatch(r"ask-\d{8}-\d{6}-[0-9a-f]{4}\.json", done.name)


def test_ask_exits_1_when_stopped_and_2_when_refused(tmp_path, cli, capsys):
    assert cli([json.dumps({"id": "c2", "kind": "read", "urls": [PP], "parser": "pp_most"})],
               answer={"state": "stopped", "reason": "paddypower is cooling off"}) == 1
    assert "stopped - paddypower is cooling off" in capsys.readouterr().out
    assert cli([json.dumps({"id": "c3", "kind": "read", "urls": [PP], "parser": "pp_most"})],
               answer={"state": "refused", "reason": "another request is running"}) == 2


def test_ask_refuses_a_bad_request_without_writing_it(tmp_path, cli, capsys):
    assert cli(['{"id": "c4", "kind": "read", "urls": ["https://example.com/"], "keep": true}',
                "--json"]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "not a Paddy Power page: https://example.com/"}
    assert cli(["{nope"]) == 2
    assert "not JSON" in capsys.readouterr().err
    assert not (tmp_path / "requests").exists() or not list((tmp_path / "requests").iterdir())


def test_ask_writes_nothing_when_the_service_is_not_running(tmp_path, cli, capsys):
    (tmp_path / "heartbeat").write_text(str(int(time.time()) - 600))
    assert cli([json.dumps({"id": "c5", "kind": "read", "urls": [PP], "parser": "pp_most"})]) == 1
    assert "not running" in capsys.readouterr().err
    assert not list((tmp_path / "requests").glob("ask-*"))


def test_ask_gives_up_waiting_but_the_request_goes_on(tmp_path, cli, capsys):
    assert cli([json.dumps({"id": "c6", "kind": "read", "urls": [PP], "parser": "pp_most"}),
                "--wait", "10"]) == 1
    err = capsys.readouterr().err
    assert "not answered within 10 s" in err and "ask-c6.json" in err
    assert (tmp_path / "requests" / "ask-c6.json").exists()     # still there for the service


def test_back_off_stops_a_request(tmp_path):
    r = _runner(tmp_path, [])
    r.sites["paddypower"].blocked_for, r.sites["paddypower"].retry_at = "Cloudflare check", 5_000.0
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "urls": [PP], "keep": True})
    r._requests(now=1_000.0)
    a = _result(tmp_path, "a")
    assert (a["state"], a["reason"]) == \
        ("stopped", "paddypower is backing off (Cloudflare check) - nothing more loaded")
    assert [i["state"] for i in a["items"]] == ["skipped"]


def test_a_final_result_not_written_is_said_loudly_and_written_next_tick(tmp_path, monkeypatch):
    r = _runner(tmp_path, [])
    r.guard.start_cooloff("paddypower", "HTTP 429", 900.0)       # it ends at once: stopped
    _drop(tmp_path, "a", {"id": "a", "kind": "read", "urls": [PP], "keep": True})
    real = ask.write_result
    calls = []

    def flaky(data_dir, body):
        calls.append(body["state"])
        return real(data_dir, body) if len(calls) != 2 else False   # the final write fails once
    monkeypatch.setattr(ask, "write_result", flaky)
    r._requests(now=1_000.0)
    assert _result(tmp_path, "a")["state"] == "running" and r.asking is None
    assert "its result could not be written" in (tmp_path / "alerts.log").read_text()
    _drop(tmp_path, "b", {"id": "b", "kind": "read", "urls": [PP], "keep": True})
    r._requests(now=1_020.0)
    assert _result(tmp_path, "a")["state"] == "stopped"            # written now
    assert _result(tmp_path, "b")["state"] == "stopped"            # and the next one was taken


def test_old_results_and_saved_pages_are_pruned_once_a_day(tmp_path):
    import os
    now = time.time()
    old, new = now - 15 * 86400, now - 13 * 86400
    for rid, at in (("old", old), ("new", new)):
        ask.write_result(tmp_path, ask.result(rid, "read", "done"))
        os.utime(ask.result_path(tmp_path, rid), (at, at))
        d = tmp_path / "dumps" / f"ask-{rid}-1"
        d.mkdir(parents=True)
        (d / "page.html").write_text("x")
        os.utime(d, (at, at))
    other = tmp_path / "dumps" / "pp-20261101-120000"
    other.mkdir()
    os.utime(other, (old, old))
    r = _runner(tmp_path, [])
    r._requests(now=now)
    assert not ask.result_path(tmp_path, "old").exists() and ask.result_path(tmp_path, "new").exists()
    assert sorted(p.name for p in (tmp_path / "dumps").iterdir()) == ["ask-new-1", "pp-20261101-120000"]
    os.utime(ask.result_path(tmp_path, "new"), (old, old))
    r._requests(now=now + 3600)                                      # not again within the day
    assert ask.result_path(tmp_path, "new").exists()
    r._requests(now=now + 25 * 3600)
    assert not ask.result_path(tmp_path, "new").exists()
