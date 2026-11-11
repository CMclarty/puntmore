"""A target's `window` and `priority`, with a stand-in browser: outside its
window a target is not loaded and its tabs close; when a site's hourly budget
cannot cover every target that wants a load, higher priority goes first."""
import os
import random
import time
from datetime import datetime, timedelta

from scraper import config, schedule
from scraper.tests.test_pacing import _loads, _runner


def _at(hh, mm=0, day=None):
    """A local timestamp at hh:mm (today, or `day` days from today)."""
    d = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (d + timedelta(days=day or 0)).timestamp()


def test_in_window_is_local_time_end_exclusive_and_may_wrap_midnight():
    assert schedule.in_window(None, _at(3))
    assert schedule.in_window("07:00-23:30", _at(7))
    assert schedule.in_window("07:00-23:30", _at(23, 29))
    assert not schedule.in_window("07:00-23:30", _at(23, 30))
    assert not schedule.in_window("07:00-23:30", _at(6, 59))
    assert schedule.in_window("22:00-02:00", _at(23))
    assert schedule.in_window("22:00-02:00", _at(1, 59))
    assert not schedule.in_window("22:00-02:00", _at(2))
    assert not schedule.in_window("22:00-02:00", _at(12))


def _bb(name, **kw):
    return config.Target(name=name, site="bookiebashing", stale_min=10_000,
                         url=f"https://www.bookiebashing.net/{name}", **kw)


def test_outside_its_window_a_target_closes_and_reopens_at_the_start(tmp_path):
    t = _bb("bb", window="09:00-10:00")
    r = _runner(tmp_path, [t])
    r.tick(_at(8, 59))
    assert "bb" not in r.tabs and _loads(r) == []                # not loaded before the window
    st = {x["name"]: x for x in r.status(_at(8, 59))["targets"]}["bb"]
    assert (st["state"], st["detail"]) == ("paused", "outside window")
    r.tick(_at(9))
    assert "bb" in r.tabs and len(_loads(r)) == 1                # reopened at the start
    assert r.status(_at(9, 30))["targets"][0]["state"] == "running"
    first = r.tabs["bb"].page
    r.tick(_at(10))
    assert "bb" not in r.tabs                                    # closed at the end
    assert r.status(_at(10))["targets"][0]["detail"] == "outside window"
    r.tick(_at(9, day=1))
    assert "bb" in r.tabs and r.tabs["bb"].page is not first     # and back the next day


def test_a_fetch_target_runs_at_its_window_start(tmp_path):
    t = config.Target(name="tips", site="generic", url="https://tips.example.org/",
                      mode="fetch", every_min=600, window="09:00-10:00")
    r = _runner(tmp_path, [t])
    r._load = lambda target, key, url, when: None               # a load that fails
    r.tick(_at(8))
    assert "tips" not in r.next_fetch                           # nothing tried outside
    r.tick(_at(9, 30))
    assert r.next_fetch["tips"] == _at(9, 30) + schedule.FETCH_RETRY_S
    r.tick(_at(10))
    assert "tips" not in r.next_fetch                           # forgotten at the end...
    seen = []
    r._load = lambda target, key, url, when: seen.append(when)
    r.tick(_at(9, day=1))
    assert seen == [_at(9, day=1)]                              # ...so it runs at the start


def test_order_keeps_file_order_until_the_budget_is_contended():
    a, b, c = _bb("a", priority=0), _bb("b", priority=5), _bb("c", priority=5)
    wanting = {"a": 100.0, "b": 300.0, "c": 200.0}
    assert schedule.order([a, b, c], wanting, lambda site: 3) == [a, b, c]
    # 2 loads left, 3 want one: priority first, then the more overdue (c)
    assert schedule.order([a, b, c], wanting, lambda site: 2) == [c, b, a]
    # a target that wants nothing keeps its place behind the wanting ones
    assert schedule.order([a, b, c], {"a": 100.0, "b": 300.0}, lambda site: 1) == [b, a, c]


def test_order_reorders_only_the_contended_site():
    pp = config.Target(name="pp", site="paddypower", url="https://www.paddypower.com/x")
    a, b = _bb("a", priority=0), _bb("b", priority=1)
    left = {"bookiebashing": 0, "paddypower": 10}.get
    got = schedule.order([a, pp, b], {"a": 1.0, "b": 2.0, "pp": 0.0}, left)
    assert got == [b, pp, a]                                    # bb's slots refilled by priority


def test_a_contended_budget_goes_to_the_higher_priority_target(tmp_path):
    """Bookie Bashing's 8 loads an hour, three pages each wanting one every
    5 minutes (36 an hour). The hour's budget goes in a burst and then waits
    for the oldest load to age out; each time it frees up, every page is
    overdue and the priority-2 page gets the slot. The priority-0 pages share
    what it leaves (it wants a load only every 5 minutes), so neither starves."""
    random.seed(3)          # the jittered reloads: one draw passed 8 of 10 runs, which is not a test
    low1, high, low2 = _bb("low1", every_min=5), _bb("high", every_min=5, priority=2), _bb("low2", every_min=5)
    r = _runner(tmp_path, [low1, high, low2])
    t0 = time.time()
    for i in range(4 * 60 * 4):                                 # 4 hours of 15 s ticks
        r.tick(t0 + 15 * i)
    loads = [(u.rsplit("/", 1)[1], ts) for u, ts in
             r.guard.db.execute("SELECT url, ts FROM loads ORDER BY ts")]
    n = {name: sum(1 for x, _ in loads if x == name) for name in ("low1", "high", "low2")}
    assert sum(n.values()) <= 4 * 8 + 1
    assert n["high"] > n["low1"] and n["high"] > n["low2"]
    assert n["low1"] >= 4 and n["low2"] >= 4
    after_wait = [name for (_, a), (name, b) in zip(loads, loads[1:]) if b - a > 20 * 60]
    assert len(after_wait) == 3 and set(after_wait) == {"high"}
    # The first load, with the budget not yet contended, keeps file order.
    assert loads[0][0] == "low1"


# --- the kick-off sweep ------------------------------------------------------
def _gen(name, **kw):
    return config.Target(name=name, site="generic", url=f"https://tips.example.com/{name}",
                         parser="example_tipster", post="hub:tips", **kw)


def test_near_window_swaps_the_interval_and_a_passed_kickoff_does_not_count():
    now = time.time()
    t = config._target({"name": "pp", "site": "paddypower", "url": "https://www.paddypower.com/x",
                        "every_min": 180, "near_min": 30})
    assert t.near_hours == 3.0
    assert schedule.effective_every(t, now + 2 * 3600, now) == 30
    assert schedule.effective_every(t, now + 4 * 3600, now) == 180
    assert schedule.effective_every(t, now - 60, now) == 180
    assert schedule.effective_every(t, None, now) == 180
    plain = config._target({"name": "pp2", "site": "paddypower", "url": "https://www.paddypower.com/y",
                            "every_min": 180})
    assert schedule.effective_every(plain, now + 60, now) == 180      # no near_min: nothing changes


def test_kickoff_rows_are_read_as_iso_in_any_of_the_shapes_parsers_emit():
    now = 1_800_000_000.0
    rows = [{"kickoff": "2027-01-15T15:00:00Z"}, {"kickoff": "2027-01-15T12:30:00.000Z"},
            {"kickoff": "2027-01-15T20:00:00+01:00"}, {"kickoff": "garbage"}, {"no": "kickoff"}, "not a row"]
    assert schedule.next_kickoff(rows, now) == schedule.kickoff_ts("2027-01-15T12:30:00Z")
    assert schedule.next_kickoff(rows, schedule.kickoff_ts("2027-01-16T00:00:00Z")) is None
    assert schedule.kickoff_ts("2027-01-15T15:00:00") == schedule.kickoff_ts("2027-01-15T15:00:00Z")


def test_nearest_kickoff_goes_first_among_equal_priority_when_the_budget_is_short():
    a, b, c = _gen("a"), _gen("b"), _gen("c", priority=1)
    wanting = {"a": 100.0, "b": 200.0, "c": 300.0}
    kick = {"a": 5000.0, "b": 2000.0}
    assert [t.name for t in schedule.order([a, b, c], wanting, lambda s: 1, kick)] == ["c", "b", "a"]
    assert [t.name for t in schedule.order([a, b, c], wanting, lambda s: 9, kick)] == ["a", "b", "c"]
    assert [t.name for t in schedule.order([a, b], wanting, lambda s: 1)] == ["a", "b"]      # no kick-offs: overdue first


def test_a_kickoff_in_the_rows_pulls_the_reload_forward_then_lets_go(tmp_path, monkeypatch):
    """A kept-open page reloaded only when stale, with near_min 10: once its
    parser reports a kick-off two hours out it reloads about every 10 min,
    and after the kick-off it is back to the page's own rhythm (here the
    half-hourly recycle, schedule.RECYCLE_H)."""
    from scraper import runner as runner_mod
    t0 = time.time()
    kickoff = t0 + 2 * 3600
    rows = [{"home": "A", "away": "B", "kickoff": datetime.fromtimestamp(kickoff).astimezone().isoformat()}]
    monkeypatch.setitem(runner_mod.PARSERS, "pp_most", lambda page, capture: rows)
    t = config.Target(name="pp", site="paddypower", url="https://www.paddypower.com/football/epl",
                      stale_min=10_000, near_min=10, parser="pp_most")
    r = _runner(tmp_path, [t])
    for i in range(0, 3600, 15):
        r.tick(t0 + i)
    st = {x["name"]: x for x in r.status(t0 + 3600)["targets"]}["pp"]
    assert st["near"] is True and abs(st["next_kickoff"] - kickoff) < 1
    for i in range(3600, 4 * 3600, 15):
        r.tick(t0 + i)
    loads = [ts for _site, ts in _loads(r)]
    before = [ts for ts in loads if ts < kickoff]
    gaps = [b - a for a, b in zip(before, before[1:])]
    assert gaps and all(7 * 60 <= g <= 14 * 60 for g in gaps), gaps           # 10 min x jitter
    after = [ts for ts in loads if ts > kickoff]
    assert all(b - a >= 20 * 60 for a, b in zip(after, after[1:])), after     # the recycle rhythm again
    assert r.status(t0 + 4 * 3600)["targets"][0]["near"] is False


# --- days and until ----------------------------------------------------------
FRI, SAT, SUN, THU = (datetime(2026, 11, d) for d in (13, 14, 15, 12))   # 13 Nov 2026 is a Friday


def _on(day, hh, mm=0):
    return day.replace(hour=hh, minute=mm).timestamp()


def test_days_and_until_decide_whether_a_job_is_active():
    t = _bb("j", days=("fri", "sat"))
    assert schedule.inactive(t, _on(FRI, 10)) is None
    assert schedule.inactive(t, _on(SAT, 23, 59)) is None
    assert schedule.inactive(t, _on(THU, 10)) == "not a run day"
    assert schedule.inactive(t, _on(SUN, 0)) == "not a run day"
    assert schedule.inactive(_bb("every"), _on(THU, 10)) is None             # no days = every day
    u = _bb("u", until="2026-11-14")
    assert schedule.inactive(u, _on(SAT, 23, 59)) is None
    assert schedule.inactive(u, _on(SUN, 0)) == "finished"
    w = _bb("w", window="09:00-10:00", until="2026-11-14")
    assert schedule.inactive(w, _on(FRI, 8)) == "outside window"
    assert schedule.inactive(w, _on(SUN, 9, 30)) == "finished"               # finished wins


def test_a_window_past_midnight_belongs_to_the_day_it_started():
    t = _bb("late", window="20:00-02:00", days=("sat",), until="2026-11-14")
    assert schedule.inactive(t, _on(SAT, 21)) is None
    assert schedule.inactive(t, _on(SUN, 1, 59)) is None                     # Saturday's run
    assert schedule.inactive(t, _on(SUN, 2)) == "finished"
    assert schedule.inactive(t, _on(SAT, 1, 30)) == "not a run day"          # Friday's run
    assert schedule.inactive(_bb("x", window="20:00-02:00", days=("sat",)), _on(SUN, 21)) == "not a run day"
    assert schedule.inactive(_bb("x", window="20:00-02:00", days=("sat",)), _on(SUN, 20)) == "not a run day"
    assert schedule.job_day("20:00-02:00", _on(SUN, 1)) == SAT.date()
    assert schedule.job_day(None, _on(SUN, 1)) == SUN.date()


def test_off_days_close_the_job_and_status_says_why(tmp_path):
    r = _runner(tmp_path, [_bb("bb", days=("fri",), until="2026-11-14")])
    r.tick(_on(FRI, 10))
    assert "bb" in r.tabs and len(_loads(r)) == 1
    r.tick(_on(SAT, 10))
    assert "bb" not in r.tabs
    st = r.status(_on(SAT, 10))["targets"][0]
    assert (st["state"], st["detail"]) == ("paused", "not a run day")
    r.tick(_on(SUN, 10))
    st = r.status(_on(SUN, 10))["targets"][0]
    assert (st["state"], st["detail"]) == ("finished", "ended 2026-11-14")
    assert len(_loads(r)) == 1


def test_a_days_or_until_edit_keeps_a_running_jobs_page_open(tmp_path):
    from scraper import targets as T
    job = _bb("bb", days=("fri",), until="2026-11-14")
    r = _runner(tmp_path, [job])
    r.targets_file = tmp_path / "targets.toml"
    T.save(r.targets_file, [job])
    r.targets_mtime = r._mtime()
    r.tick(_on(FRI, 10))
    tab, page = r.tabs["bb"], r.tabs["bb"].page
    closed = []
    close = r._close
    r._close = lambda key: (closed.append(key), close(key))
    T.set(r.targets_file, "bb", days=["fri", "sat"], until="2026-11-30")     # still active on Friday
    st = r.targets_file.stat()
    os.utime(r.targets_file, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    r._reload_targets(_on(FRI, 10, 1))
    r.tick(_on(FRI, 10, 1))
    assert closed == [] and r.tabs["bb"] is tab and tab.page is page
    assert len(_loads(r)) == 1
    assert (tab.target.days, tab.target.until) == (("fri", "sat"), "2026-11-30")
