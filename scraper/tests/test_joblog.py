import json

from scraper import joblog, runner
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
    for i in range(6000):                                            # a normal log: short lines
        joblog.write(tmp_path, "j", f"line {i} " + "x" * 80, now=float(i))
    assert joblog.path(tmp_path, "j").stat().st_size <= joblog.MAX_BYTES
    lines = joblog.read(tmp_path, "j", n=100_000)
    assert joblog.KEEP_LINES == 1000 <= len(lines) < 6000
    assert lines[-1]["msg"].startswith("line 5999 ")
    assert [x["ts"] for x in lines] == [float(i) for i in range(6000 - len(lines), 6000)]
    joblog._trim(joblog.path(tmp_path, "j"))
    assert len(joblog.read(tmp_path, "j", n=100_000)) == joblog.KEEP_LINES


def test_long_lines_keep_a_log_under_512_kb(tmp_path):
    for i in range(1000):
        joblog.write(tmp_path, "j", f"{i} " + "x" * joblog.MSG_MAX, now=float(i))
    assert joblog.path(tmp_path, "j").stat().st_size <= joblog.MAX_BYTES
    assert joblog.read(tmp_path, "j", n=1)[0]["msg"].startswith("999 ")
    for i in range(100):                                             # escaped by json: ~12 KB a line
        joblog.write(tmp_path, "u", f"{i} " + "é" * joblog.MSG_MAX, now=float(i))
    assert joblog.path(tmp_path, "u").stat().st_size <= joblog.MAX_BYTES
    assert joblog.read(tmp_path, "u", n=1)[0]["msg"].startswith("99 ")


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


def _tips_job(tmp_path):
    from scraper import config
    t = config.Target(name="tips", site="generic", url="https://tips.example.org/today",
                      mode="fetch", post="hub:tips")
    return t, _runner(tmp_path, [t])


def _tip(i):
    return {"event": f"A{i} v B{i}", "market": "Match Odds", "selection": f"A{i}"}


def _msgs(tmp_path, name):
    return [x["msg"] for x in joblog.read(tmp_path, name, n=100)]


def test_a_delivery_logs_what_was_handed_and_what_was_left_out(tmp_path):
    t, r = _tips_job(tmp_path)
    rows = [_tip(0), _tip(1), {"event": "no market"}, _tip(2), {"selection": "x"}]
    assert r._deliver(t, rows, 100.0)
    assert not r._deliver(t, [{"event": "no market"}], 101.0)      # nothing handed: no line
    assert _msgs(tmp_path, "tips") == ["3 row(s) handed to the Hub (hub:tips), 2 left out"]


def test_a_repeated_delivery_is_logged_on_a_change_or_every_10_min(tmp_path):
    t, r = _tips_job(tmp_path)
    t0 = 1_000.0
    r._deliver(t, [_tip(0), _tip(1)], t0)
    r._deliver(t, [_tip(0), _tip(1)], t0 + 60)                      # same count inside 10 min
    assert _msgs(tmp_path, "tips") == ["2 row(s) handed to the Hub (hub:tips)"]
    r._deliver(t, [_tip(0), _tip(1), _tip(2)], t0 + 120)            # a new count: at once
    r._deliver(t, [_tip(0), _tip(1), _tip(2), {"event": "bad"}], t0 + 180)   # one left out: a change
    r._deliver(t, [_tip(0), _tip(1), _tip(2), {"event": "bad"}], t0 + 180 + 599)
    r._deliver(t, [_tip(0), _tip(1), _tip(2), {"event": "bad"}], t0 + 180 + runner.DELIVERY_SAID_S)
    assert _msgs(tmp_path, "tips")[1:] == ["3 row(s) handed to the Hub (hub:tips)",
                                           "3 row(s) handed to the Hub (hub:tips), 1 left out",
                                           "3 row(s) handed to the Hub (hub:tips), 1 left out"]
    assert runner.DELIVERY_SAID_S == 600


def test_a_changed_job_forgets_its_last_delivery_line(tmp_path):
    import dataclasses
    t, r = _tips_job(tmp_path)
    r._deliver(t, [_tip(0)], 1_000.0)
    r.apply_targets([dataclasses.replace(t, url="https://tips.example.org/other")])
    assert "tips" not in r.delivery_said


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


def test_a_long_message_is_cut_to_its_cap(tmp_path):
    joblog.write(tmp_path, "j", "x" * 5000, now=1.0)
    msg = joblog.read(tmp_path, "j")[0]["msg"]
    assert len(msg) == joblog.MSG_MAX == 2000 and msg.endswith("…") and msg.startswith("xxx")
    joblog.write(tmp_path, "j", "y" * 2000, now=2.0)
    assert joblog.read(tmp_path, "j", n=1)[0]["msg"] == "y" * 2000


def test_a_trimmed_log_keeps_its_permissions(tmp_path):
    joblog.write(tmp_path, "j", "first", now=0.0)
    p = joblog.path(tmp_path, "j")
    p.chmod(0o640)
    for i in range(2000):
        joblog.write(tmp_path, "j", "x" * 300, now=float(i))
    assert len(joblog.read(tmp_path, "j", n=10_000)) < 2000          # it was trimmed
    assert p.stat().st_mode & 0o777 == 0o640
    assert [q.name for q in p.parent.iterdir()] == ["j.log"]       # no temp file left


def test_dot_and_empty_names_stay_in_logs(tmp_path):
    for name in ("..", ""):
        joblog.write(tmp_path, name, f"hello {name}", now=1.0)
        p = joblog.path(tmp_path, name)
        assert p.parent == tmp_path / "logs" and p.resolve().parent == (tmp_path / "logs").resolve()
        assert joblog.read(tmp_path, name) == [{"ts": 1.0, "msg": f"hello {name}"}]
    assert sorted(q.parent for q in tmp_path.rglob("*.log")) == [tmp_path / "logs"] * 2


def test_removing_a_log_never_raises(tmp_path):
    joblog.remove(tmp_path, "nobody")                                # no file
    stuck = joblog.path(tmp_path, "stuck")
    (stuck / "inside").mkdir(parents=True)                           # a directory can't be unlinked
    joblog.remove(tmp_path, "stuck")
    assert stuck.exists()


def test_min_rows_breach_and_recovery_reach_the_job_log(tmp_path, capsys):
    from scraper import config
    from scraper.tests.test_generic_target import _runner as _generic_runner
    r = _generic_runner(tmp_path)
    t = config._target({"name": "tips-a", "site": "generic", "url": "https://a.example.com",
                        "parser": "example_tipster", "post": "hub:tips", "min_rows": 2})
    r._check_rows(t, [{"event": "x"}], 1_000.0)
    r._check_rows(t, [{"event": "x"}], 1_060.0)                     # said once per change
    r._check_rows(t, [{"event": "x"}, {"event": "y"}], 1_120.0)
    assert _msgs(tmp_path, "tips-a") == ["only 1 row(s), expected at least 2 - layout change?",
                                         "healthy again (2 rows)"]
    out = capsys.readouterr().out
    assert out.count("layout change?") == 1 and out.count("healthy again") == 1   # printed once, by the alert


def test_rows_sent_counts_only_the_rows_handed(tmp_path, monkeypatch):
    import dataclasses
    from types import SimpleNamespace
    t, r = _tips_job(tmp_path)
    t = dataclasses.replace(t, parser="three_and_two_bad")
    rows = [_tip(0), {"event": "no market"}, _tip(1), {"selection": "x"}, _tip(2)]
    monkeypatch.setitem(runner.PARSERS, "three_and_two_bad", lambda page, capture: rows)
    tab = SimpleNamespace(target=t, page=None, capture=None, next_parse=0.0, url=t.url)
    r._parse(tab, 1_000.0, force=True)
    r._parse(tab, 1_060.0, force=True)
    assert r.rows_posted["tips"] == 6                                # 3 handed each run, not 5
