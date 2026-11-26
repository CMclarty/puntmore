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
    assert g.last_load("paddypower", "r3") == now         # stored like any load
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


def test_the_column_is_added_in_place_keeping_the_rows(tmp_path):
    """A guard.sqlite3 from before requests: its loads (and cool-off) are kept,
    each counted as the sweep's."""
    path = tmp_path / "g.sqlite3"
    now = time.time()
    db = sqlite3.connect(str(path))
    db.executescript("CREATE TABLE loads (site TEXT, ts REAL, url TEXT);"
                     "CREATE INDEX loads_site_ts ON loads(site, ts);"
                     "CREATE TABLE cooloff (site TEXT PRIMARY KEY, since REAL, reason TEXT, resumed INTEGER);")
    db.executemany("INSERT INTO loads VALUES (?,?,?)", [("paddypower", now - 10, "a"),
                                                        ("paddypower", now - 5, "b")])
    db.execute("INSERT INTO cooloff VALUES ('generic', ?, 'HTTP 429', 0)", (now,))
    db.commit()
    db.close()
    g = Guard(path, {"paddypower": (3, 5)}, quiet=(0, 0), cooloff_hours=24)
    assert g.spent("paddypower", now) == (2, 2)
    assert g.requested("paddypower", now) == (0, 0)
    assert g.cooloff("generic")["reason"] == "HTTP 429"
    g.record_load("paddypower", "r", now, requested=True)
    assert g.requested("paddypower", now) == (1, 1)
    g2 = Guard(path, {"paddypower": (3, 5)}, quiet=(0, 0), cooloff_hours=24)    # migrated once
    assert (g2.spent("paddypower", now), g2.requested("paddypower", now)) == ((2, 2), (1, 1))


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
