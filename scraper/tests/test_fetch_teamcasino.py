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


def _stand_in(monkeypatch, rows=None):
    """Signed in, and the page is the 13 Nov dashboard (or `rows()` read)."""
    monkeypatch.setattr(browser, "logged_out", lambda *a, **k: False)
    monkeypatch.setitem(runner_mod.PARSERS, "tc_reload",
                        rows or (lambda page, cap: tc_reload(_Page(PAGE), cap)))


def _at(y, mo, d, h, mi=0):
    """A fixed local time, on a day with no clock change."""
    return datetime(y, mo, d, h, mi).timestamp()


def _ticks(r, start, end, step=15):
    t = start
    while t <= end:
        r.tick(t)
        t += step


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


def test_a_fetch_job_runs_at_once_when_its_window_opens(tmp_path, monkeypatch):
    """Outside its window a fetch job forgets its next run, so it runs as
    soon as the window opens (_outside_window), whatever its last read."""
    _stand_in(monkeypatch)
    t = _tc(every_min=100, window="08:00-23:00")
    r = _runner(tmp_path, [t])
    r.tick(_at(2026, 11, 18, 22, 50))                           # 22:50: inside, runs
    assert len(_loads(r)) == 1
    _ticks(r, _at(2026, 11, 18, 22, 55), _at(2026, 11, 19, 7, 55), step=300)
    assert len(_loads(r)) == 1                                  # nothing in the night
    r.tick(_at(2026, 11, 19, 8, 0))                             # 08:00: window open
    assert len(_loads(r)) == 2


def _signed_in_then_failing(monkeypatch, tmp_path):
    """The first load finds Team Casino logged out and sends a sign-in (the
    stand-in login); every load after that fails (a goto that raises)."""
    from scraper import login
    from scraper.tests.test_pacing import _Ctx, _Nothing, _Page as _PacingPage

    class Failing(_PacingPage):
        def goto(self, *a, **k):
            if ctx.fail:
                raise TimeoutError("goto timed out")
            return _Nothing()

    class Ctx(_Ctx):
        fail = False

        def new_page(self):
            return Failing([])
    ctx = Ctx()
    _stand_in(monkeypatch)
    monkeypatch.setattr(browser, "logged_out", lambda *a, **k: True)
    monkeypatch.setattr(login, "credentials", lambda site: ("u", "p"))
    monkeypatch.setattr(login, "attempt", lambda page, site, creds, **k: None)
    r = _runner(tmp_path, [_tc(every_min=100)])
    r.ctx = ctx
    return r, ctx


def test_after_a_sign_in_one_follow_up_load_comes_at_once_then_fetch_retry_s(tmp_path, monkeypatch):
    from scraper import behaviour
    r, ctx = _signed_in_then_failing(monkeypatch, tmp_path)
    t0 = _midday()
    r.tick(t0)
    assert "teamcasino" in r.login_pending and len(_loads(r)) == 2   # the visit, the sign-in
    ctx.fail = True
    _ticks(r, t0 + 15, t0 + 290)
    assert len(_loads(r)) == 3                                  # one follow-up, which failed
    follow_up = _loads(r)[-1][1]
    retry = behaviour.get("fetch.retry_s")
    _ticks(r, t0 + 305, follow_up + retry - 15)
    assert len(_loads(r)) == 3                                  # nothing until fetch.retry_s
    _ticks(r, follow_up + retry, follow_up + retry + 240)
    assert len(_loads(r)) == 4


def test_a_restart_then_a_timing_change_takes_effect(tmp_path, monkeypatch):
    import dataclasses
    _stand_in(monkeypatch)
    t = _tc(every_min=300)
    t0 = _midday()
    r = _runner(tmp_path, [t])
    r.tick(t0)
    again = _runner(tmp_path, [t])                              # a restart
    again.tick(t0 + 60)                                         # its next run drawn: 225-405 min
    again.apply_targets([dataclasses.replace(t, every_min=30)])     # in place
    _ticks(again, t0 + 75, t0 + 30 * 60 * 1.35 + 300, step=30)
    assert len(_loads(again)) == 2


def test_wake_re_adding_or_enabling_a_fetch_job_runs_it_at_once(tmp_path, monkeypatch):
    """As before the last-read note: only a restart keeps the interval."""
    import dataclasses
    _stand_in(monkeypatch)
    t = _tc(every_min=300)
    t0 = _midday()
    r = _runner(tmp_path, [t])
    r.tick(t0)
    r._start_rest(t, t0 + 7200, "nothing today", t0 + 60)
    _ticks(r, t0 + 75, t0 + 600, step=60)
    assert len(_loads(r)) == 1
    r._end_rest(t.name)                                         # `wake`
    _ticks(r, t0 + 660, t0 + 900)
    assert len(_loads(r)) == 2
    r.apply_targets([])                                         # removed (or disabled)
    r.tick(t0 + 960)
    r.apply_targets([t])                                        # added again (or enabled)
    _ticks(r, t0 + 1020, t0 + 1260)
    assert len(_loads(r)) == 3
    r.apply_targets([dataclasses.replace(t, url=URL + "?changed")])   # a new page
    _ticks(r, t0 + 1320, t0 + 1560)
    assert len(_loads(r)) == 4


def test_a_restart_after_a_load_that_read_nothing_retries_on_fetch_retry_s(tmp_path, monkeypatch):
    from scraper import behaviour
    _stand_in(monkeypatch, rows=lambda page, cap: [])
    t = _tc(every_min=300)
    t0 = _midday()
    _runner(tmp_path, [t]).tick(t0)                             # read nothing
    again = _runner(tmp_path, [t])
    retry = behaviour.get("fetch.retry_s")
    _ticks(again, t0 + 15, t0 + retry - 15)
    assert len(_loads(again)) == 1
    _ticks(again, t0 + retry, t0 + retry + 120)
    assert len(_loads(again)) == 2


def test_a_restart_between_a_sign_in_and_its_read_retries_on_fetch_retry_s(tmp_path, monkeypatch):
    from scraper import behaviour
    r, ctx = _signed_in_then_failing(monkeypatch, tmp_path)
    t0 = _midday()
    r.tick(t0)                                                  # the visit and the sign-in
    _stand_in(monkeypatch)                                      # signed in now
    again = _runner(tmp_path, [_tc(every_min=100)])             # a restart before the read
    retry = behaviour.get("fetch.retry_s")
    _ticks(again, t0 + 15, t0 + retry - 15)
    assert len(_loads(again)) == 2
    _ticks(again, t0 + retry, t0 + retry + 120)
    assert len(_loads(again)) == 3
