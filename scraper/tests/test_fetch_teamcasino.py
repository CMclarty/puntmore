"""A Team Casino job in fetch mode (load, parse once, close, every N minutes
inside a window), with a stand-in browser: the job is accepted with no
`post`, a restart does not load it before it is due, and its status keeps
the rows-read line once its page is closed. The sign-in and the wait for
the offers are in test_e2e_teamcasino.py, in a real Chrome."""
import json
from datetime import datetime

from scraper import __main__ as cli
from scraper import browser, config
from scraper import runner as runner_mod
from scraper import targets as T
from scraper.parsers.tc_reload import tc_reload
from scraper.tests.test_pacing import _loads, _runner
from scraper.tests.test_tc_reload import PAGE, URL, _Page

WAIT_FOR = "#offers-main-wrap table[data-offer-id]"


def _midday():
    return datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()


def _tc(**kw):
    return config.Target(name="team-casino", site="teamcasino", url=URL, mode="fetch",
                         parser="tc_reload", wait_for=WAIT_FOR, **kw)


def _stand_in(monkeypatch):
    """Signed in, and the page is the 13 Nov dashboard."""
    monkeypatch.setattr(browser, "logged_out", lambda *a, **k: False)
    monkeypatch.setitem(runner_mod.PARSERS, "tc_reload", lambda page, cap: tc_reload(_Page(PAGE), cap))


def test_the_flags_turn_the_kept_open_job_into_a_fetch_job(tmp_path, capsys):
    """The live job as it is (kept open, no parser), then the flags given
    for it: accepted for site teamcasino with parser tc_reload and no post."""
    f = tmp_path / "targets.toml"
    T.add(f, {"name": "team-casino", "site": "teamcasino", "url": URL, "stale_min": 600, "mode": "keep"})
    assert cli.targets_cmd(["set", "team-casino", "--file", str(f), "--mode", "fetch",
                            "--parser", "tc_reload", "--every-min", "100", "--window", "08:00-23:00",
                            "--wait-for", WAIT_FOR]) in (0, None)
    t = T.get(f, "team-casino")
    assert (t.mode, t.parser, t.post, t.every_min, t.window, t.wait_for) == (
        "fetch", "tc_reload", None, 100.0, "08:00-23:00", WAIT_FOR)
    assert config.load_targets(f)[0] == t


def test_a_fetch_run_loads_once_parses_closes_and_the_status_keeps_the_count(tmp_path, monkeypatch):
    _stand_in(monkeypatch)
    r = _runner(tmp_path, [_tc(every_min=100)])
    t0 = _midday()
    r.tick(t0)
    assert len(_loads(r)) == 1 and r.tabs == {}                # loaded, read, closed
    for i in range(1, 30):                                      # nothing more before it is due
        r.tick(t0 + 60 * i)
    assert len(_loads(r)) == 1
    st = r.status(t0 + 1800)["targets"][0]
    assert st["state"] == "running"
    assert st["detail"] == ("0 tabs open; 12 rows read, sent nowhere (no post); "
                            "the page says 121 offers available, 12 read")
    assert len(json.loads((tmp_path / "latest" / "team-casino.json").read_text())["rows"]) == 12


def test_a_restart_does_not_load_a_fetch_job_before_it_is_due(tmp_path, monkeypatch):
    """The next run is drawn from the job's last load in the guard's record,
    which outlives the process: a restart (every restart.hours) is not a
    load. A fetch job keeps no page open, so no recycle reloads it either."""
    _stand_in(monkeypatch)
    t = _tc(every_min=100)
    r = _runner(tmp_path, [t])
    t0 = _midday()
    r.tick(t0)
    assert len(_loads(r)) == 1
    again = _runner(tmp_path, [t])                              # a new process, the same data
    for i in range(1, 75):                                      # 100 min x 0.75 at the soonest
        again.tick(t0 + 60 * i)
        assert again.tabs == {}
    assert len(_loads(again)) == 1
    for i in range(75, 140):                                    # 100 min x 1.35 at the latest
        again.tick(t0 + 60 * i)
    assert len(_loads(again)) == 2


def test_a_fetch_job_runs_at_its_window_start_after_a_night_closed(tmp_path, monkeypatch):
    _stand_in(monkeypatch)
    t = _tc(every_min=100, window="08:00-23:00")
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    r = _runner(tmp_path, [t])
    r.tick(day + 22 * 3600)                                     # 22:00: inside, runs
    assert len(_loads(r)) == 1
    for m in range(1, 9 * 60, 5):                               # 22:05 .. 06:55 next day
        r.tick(day + 22 * 3600 + 60 * m)
    n = len(_loads(r))
    assert n <= 2                                               # at most once more before 23:00
    r.tick(day + 32 * 3600)                                     # 08:00 next day: window open
    assert len(_loads(r)) == n + 1
