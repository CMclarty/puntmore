"""A target's `window` and `priority`, with a stand-in browser: outside its
window a target is not loaded and its tabs close; when a site's hourly budget
cannot cover every target that wants a load, higher priority goes first."""
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
