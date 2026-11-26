"""Requested loads (ask.py) in the guard: recorded, shown apart, and never
spending the site's budget."""
import sqlite3
import time

from scraper.guard import Guard
from scraper.status import site_status


def _g(tmp_path, hour=3, day=5):
    g = Guard(tmp_path / "g.sqlite3", {"paddypower": (hour, day), "bookiebashing": (8, 120),
                                       "teamcasino": (6, 30), "generic": (60, 400)},
              quiet=(0, 0), cooloff_hours=24)
    return g


def test_a_requested_load_is_stored_and_spends_no_budget(tmp_path):
    g = _g(tmp_path)
    now = time.time()
    g.record_load("paddypower", "u", now)
    for i in range(10):                                   # far past the hour's 3 and the day's 5
        g.record_load("paddypower", f"r{i}", now, requested=True)
    assert g.spent("paddypower", now) == (1, 1)
    assert g.requested("paddypower", now) == (10, 10)
    assert g.can_load("paddypower", now) == (True, "")
    assert g.last_load("paddypower", "r3") is None        # a fetch job's record is the sweep's
    later = now + 2 * 3600
    assert g.requested("paddypower", later) == (0, 10)    # out of the hour, still today
    assert g.requested("bookiebashing", now) == (0, 0)


def test_the_sweep_still_spends_its_budget(tmp_path):
    g = _g(tmp_path)
    now = time.time()
    g.record_load("paddypower", "r", now, requested=True)
    for _ in range(3):
        g.record_load("paddypower", "u", now)
    assert g.can_load("paddypower", now) == (False, "hourly budget (3) spent")


def test_the_loads_table_is_left_as_the_old_service_writes_it(tmp_path):
    """The service before requests (still running while a new CLI asks, and
    after a rollback) inserts three values into `loads`. A new Guard opening
    the file must leave that working: requested loads have a table of their own."""
    path = tmp_path / "g.sqlite3"
    now = time.time()
    g = Guard(path, {"paddypower": (3, 5)}, quiet=(0, 0), cooloff_hours=24)
    g.record_load("paddypower", "r", now, requested=True)
    db = sqlite3.connect(str(path))
    assert [r[1] for r in db.execute("PRAGMA table_info(loads)")] == ["site", "ts", "url"]
    db.execute("INSERT INTO loads VALUES (?,?,?)", ("paddypower", now, "old"))   # the old code's insert
    db.commit()
    db.close()
    g2 = Guard(path, {"paddypower": (3, 5)}, quiet=(0, 0), cooloff_hours=24)
    assert g2.spent("paddypower", now) == (1, 1) and g2.requested("paddypower", now) == (1, 1)
    assert g2.last_load("paddypower", "old") == now


def test_requested_loads_older_than_two_days_go(tmp_path):
    g = _g(tmp_path)
    now = time.time()
    g.record_load("paddypower", "r", now - 3 * 24 * 3600, requested=True)
    g.record_load("paddypower", "r", now, requested=True)
    n = g.db.execute("SELECT COUNT(*) FROM requested_loads").fetchone()[0]
    assert n == 1


def test_site_status_carries_the_requested_loads(tmp_path):
    g = _g(tmp_path)
    now = time.time()
    g.record_load("paddypower", "r", now, requested=True)
    pp = next(s for s in site_status(g, now) if s["site"] == "paddypower")
    assert (pp["loads_hour"], pp["requested_hour"], pp["requested_day"]) == (0, 1, 1)


def test_the_status_command_says_requested_loads_only_when_there_are_any(tmp_path, monkeypatch, capsys):
    from scraper import __main__ as cli
    from scraper import config
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "p", channel=None, alert_webhook=None,
                        healthcheck_url=None, capture_mb=1, targets=[])
    cli.status(s)
    assert "requested" not in capsys.readouterr().out
    now = time.time()
    g = Guard(tmp_path / "guard.sqlite3")
    for _ in range(2):
        g.record_load("paddypower", "r", now - 7200, requested=True)
    g.record_load("paddypower", "r", now, requested=True)
    cli.status(s)
    out = capsys.readouterr().out
    assert "paddypower  requested: 1 this hour, 3 today" in out.splitlines()
    assert "bookiebashing  requested" not in out
