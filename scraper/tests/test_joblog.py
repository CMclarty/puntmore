import json

from scraper import joblog
from scraper.tests.test_pacing import _runner
from scraper.tests.test_schedule import _at, _bb


def test_one_json_line_each_newest_last(tmp_path):
    joblog.write(tmp_path, "pp-saves", "first", now=100.0)
    joblog.write(tmp_path, "pp-saves", "second", now=101.0)
    assert joblog.read(tmp_path, "pp-saves") == [{"ts": 100.0, "msg": "first"}, {"ts": 101.0, "msg": "second"}]
    assert joblog.read(tmp_path, "pp-saves", n=1) == [{"ts": 101.0, "msg": "second"}]
    first = (tmp_path / "logs" / "pp-saves.log").read_text().splitlines()[0]
    assert json.loads(first) == {"ts": 100.0, "msg": "first"}
    assert joblog.read(tmp_path, "nobody") == []


def test_past_512_kb_a_log_keeps_its_newest_1000_lines(tmp_path):
    for i in range(3000):
        joblog.write(tmp_path, "j", f"line {i} " + "x" * 300, now=float(i))
    assert joblog.path(tmp_path, "j").stat().st_size <= joblog.MAX_BYTES + 400
    lines = joblog.read(tmp_path, "j", n=10_000)
    assert 1000 <= len(lines) < 1600
    assert lines[-1]["msg"].startswith("line 2999 ")
    assert [x["ts"] for x in lines] == [float(i) for i in range(3000 - len(lines), 3000)]


def test_a_torn_line_is_skipped_and_odd_names_stay_in_logs(tmp_path):
    joblog.write(tmp_path, "a/../b c", "ok", now=1.0)
    p = joblog.path(tmp_path, "a/../b c")
    assert p.parent == tmp_path / "logs"
    with p.open("a") as f:
        f.write('{"ts": 2.0, "msg": "torn')
    assert joblog.read(tmp_path, "a/../b c") == [{"ts": 1.0, "msg": "ok"}]


def test_the_write_after_a_torn_line_starts_a_fresh_one(tmp_path):
    joblog.write(tmp_path, "j", "before", now=1.0)
    with joblog.path(tmp_path, "j").open("a") as f:
        f.write('{"ts": 2.0, "msg": "torn')
    joblog.write(tmp_path, "j", "after", now=3.0)
    assert joblog.read(tmp_path, "j") == [{"ts": 1.0, "msg": "before"}, {"ts": 3.0, "msg": "after"}]


def test_a_write_that_fails_never_raises(tmp_path):
    (tmp_path / "logs").write_text("a file where the directory should be")
    joblog.write(tmp_path, "j", "lost")


def test_the_runner_logs_what_it_prints_about_a_job(tmp_path, capsys):
    r = _runner(tmp_path, [_bb("bb", window="09:00-10:00")])
    r.tick(_at(8, 59))
    r.tick(_at(9))
    out = capsys.readouterr().out
    assert "bb: outside window 09:00-10:00 - paused" in out and "bb: window open - running" in out
    assert [x["msg"] for x in joblog.read(tmp_path, "bb")] == ["outside window 09:00-10:00 - paused",
                                                              "window open - running"]


def test_a_tick_survives_a_log_it_cannot_write(tmp_path):
    (tmp_path / "logs").write_text("not a directory")
    r = _runner(tmp_path, [_bb("bb", window="09:00-10:00")])
    r.tick(_at(8, 59))
    r.tick(_at(9))
    assert "bb" in r.tabs


def test_a_delivery_that_went_is_noted_in_the_log(tmp_path):
    from scraper import config
    t = config.Target(name="tips", site="generic", url="https://tips.example.org/today",
                      mode="fetch", post="hub:tips")
    r = _runner(tmp_path, [t])
    assert r._deliver(t, [{"event": "A v B", "market": "Match Odds", "selection": "A"}], 100.0)
    assert not r._deliver(t, [{"event": "no market"}], 101.0)
    assert [x["msg"] for x in joblog.read(tmp_path, "tips")] == ["1 row(s) handed to the Hub (hub:tips)"]


def test_dump_lines_go_to_the_jobs_log_and_never_make_one_for_a_stranger(tmp_path, capsys):
    r = _runner(tmp_path, [_bb("bb")])
    for name in ("bb", "ghost"):
        (tmp_path / "requests" / f"dump-{name}").touch()
    r._requests()
    assert "dump ghost: no open page for that target" in capsys.readouterr().out
    assert not joblog.path(tmp_path, "ghost").exists()
    assert [x["msg"] for x in joblog.read(tmp_path, "bb")] == ["dump: no open page for that target"]
    r.tick(_at(12))
    (tmp_path / "requests" / "dump-bb").touch()
    r._requests()
    assert joblog.read(tmp_path, "bb", n=1)[0]["msg"].startswith("dump: saved ")
