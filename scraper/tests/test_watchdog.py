"""The watchdog ends a service whose tick is stuck, and leaves a live one be. It
also says so: once when the loop has made no progress for `alerts.stuck_s`,
once when it ticks again, and, after it has ended the service, once at the
next start (data/stalled.json). Its alert can never hold up the exit."""
import json
import threading
import time

import pytest

from scraper import behaviour, standing
from scraper.__main__ import Watchdog, watchdog

STALLED = "stalled: no progress for 3 min - the watchdog restarts it at 10 min"


def test_a_stuck_tick_exits_and_a_live_one_does_not():
    exits = []
    last_tick = [time.monotonic()]
    watchdog(last_tick, limit_s=0.2, every_s=0.05, exit=exits.append)
    for _ in range(6):                     # ticking: never older than the limit
        time.sleep(0.05)
        last_tick[0] = time.monotonic()
    assert exits == []
    time.sleep(0.4)                        # stuck
    assert exits and exits[0] == 1


@pytest.fixture(autouse=True)
def _defaults(tmp_path_factory):
    behaviour.load(tmp_path_factory.mktemp("no-overrides"))
    yield
    behaviour.load(tmp_path_factory.mktemp("no-overrides"))


class _Alerts:
    """alerts.Alerts as the watchdog sees it; `block` holds every send (a
    webhook that hangs), `fail` makes it raise."""
    def __init__(self, block=None, fail=False):
        self.sent, self.block, self.fail = [], block, fail

    def send(self, message, image=None):
        self.sent.append(message)
        if self.fail:
            raise OSError("alerts.log: disk full")
        if self.block is not None:
            self.block.wait()


def _until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


def _dog(tmp_path, alerts):
    clock, exits = [0.0], []
    last = [0.0]
    dog = Watchdog(last, exit=exits.append, alerts=alerts, data_dir=tmp_path, clock=lambda: clock[0])
    return dog, last, clock, exits


def _checks(dog, clock, start, end, every=30):
    for t in range(start, end + 1, every):
        clock[0] = t
        dog.check()


def test_a_stall_alerts_once_and_its_end_once(tmp_path):
    a = _Alerts()
    dog, last, clock, exits = _dog(tmp_path, a)
    _checks(dog, clock, 0, 150)
    time.sleep(0.05)
    assert a.sent == []                                  # 150 s: not yet
    _checks(dog, clock, 180, 420)                        # every 30 s check from 3 min to 7 min
    assert _until(lambda: a.sent == [STALLED])
    time.sleep(0.05)
    assert a.sent == [STALLED]                           # once, not every check
    last[0] = 440.0                                      # the loop ticks again, 7 min 20 s on
    _checks(dog, clock, 450, 600)
    assert _until(lambda: len(a.sent) == 2)
    assert a.sent[1] == "running again after a 7 min stall"
    for t in range(630, 800, 30):                        # and on, ticking: nothing more
        last[0] = clock[0] = t
        dog.check()
    time.sleep(0.05)
    assert len(a.sent) == 2 and exits == []
    assert not (tmp_path / "stalled.json").exists()


@pytest.mark.parametrize("alerts", ["blocks", "raises"])
def test_the_exit_writes_stalled_json_and_happens_whatever_the_alert_does(tmp_path, alerts):
    hold = threading.Event()
    a = _Alerts(block=hold) if alerts == "blocks" else _Alerts(fail=True)
    dog, last, clock, exits = _dog(tmp_path, a)
    try:
        _checks(dog, clock, 0, 600)
        assert _until(lambda: a.sent == [STALLED])       # sent (and hanging, or failed)
        assert exits == []
        clock[0] = 630
        before = time.time()
        dog.check()
        assert exits == [1]
        body = json.loads((tmp_path / "stalled.json").read_text())
        assert body["stuck_s"] == 630 and before - 1 <= body["at"] <= time.time() + 1
        assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".stalled*"))
    finally:
        hold.set()


def test_an_unwritable_data_dir_still_exits(tmp_path):
    dog, last, clock, exits = _dog(tmp_path / "missing" / "deeper", _Alerts())
    clock[0] = 700
    dog.check()
    assert exits == [1]


def test_the_next_start_says_it_was_restarted_and_removes_the_file(tmp_path):
    standing.write_stalled(tmp_path, 630.4)
    a = _Alerts()
    standing.restarted(tmp_path, a)
    assert a.sent == ["restarted by the watchdog after a 10 min stall"]
    assert not (tmp_path / "stalled.json").exists()
    standing.restarted(tmp_path, a)                      # said once
    assert len(a.sent) == 1


def test_a_damaged_stalled_json_is_removed_quietly(tmp_path):
    (tmp_path / "stalled.json").write_text("{half")
    a = _Alerts(fail=True)
    standing.restarted(tmp_path, a)                      # never raises
    assert not (tmp_path / "stalled.json").exists()


def test_0_is_off(tmp_path):
    behaviour.set_(tmp_path, "alerts.stuck_s", 0)
    behaviour.load(tmp_path)
    a = _Alerts()
    dog, last, clock, exits = _dog(tmp_path, a)
    _checks(dog, clock, 0, 600)
    time.sleep(0.05)
    assert a.sent == [] and exits == []
    clock[0] = 630
    dog.check()
    assert exits == [1]                                  # the restart itself is not an alert
    standing.restarted(tmp_path, a)
    assert a.sent == [] and not (tmp_path / "stalled.json").exists()


def test_the_threshold_is_read_at_each_check(tmp_path):
    a = _Alerts()
    dog, last, clock, exits = _dog(tmp_path, a)
    _checks(dog, clock, 0, 90)
    behaviour.set_(tmp_path, "alerts.stuck_s", 60)
    behaviour.load(tmp_path)
    _checks(dog, clock, 120, 120)
    assert _until(lambda: a.sent == ["stalled: no progress for 2 min - the watchdog restarts it at 10 min"])
