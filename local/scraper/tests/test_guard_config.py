import time

import pytest

from scraper import config
from scraper.guard import Guard


def _g(tmp_path, hour=3, day=5):
    g = Guard(tmp_path / "g.sqlite3", {"paddypower": (hour, day)}, quiet=(1, 7), cooloff_hours=24)
    return g


def test_hourly_and_daily_budget(tmp_path):
    g = _g(tmp_path)
    g.quiet = (0, 0)
    now = time.time()
    for i in range(3):
        assert g.can_load("paddypower", now)[0]
        g.record_load("paddypower", "u", now)
    assert g.can_load("paddypower", now) == (False, "hourly budget (3) spent")
    later = now + 3601
    g.record_load("paddypower", "u", later)
    g.record_load("paddypower", "u", later)
    assert g.can_load("paddypower", later + 1) == (False, "daily budget (5) spent")


def test_budget_survives_restart(tmp_path):
    g = _g(tmp_path)
    g.quiet = (0, 0)
    now = time.time()
    for _ in range(3):
        g.record_load("paddypower", "u", now)
    g2 = _g(tmp_path)
    g2.quiet = (0, 0)
    assert not g2.can_load("paddypower", now)[0]


def test_quiet_hours(tmp_path):
    g = _g(tmp_path)
    at = lambda h: time.mktime((2026, 10, 18, h, 30, 0, 0, 0, -1))
    assert g.in_quiet(at(1)) and g.in_quiet(at(6))
    assert not g.in_quiet(at(0)) and not g.in_quiet(at(7))
    assert g.can_load("paddypower", at(3)) == (False, "quiet hours")
    assert g.can_load("paddypower", at(3), quiet_applies=False)[0]


def test_cooloff_needs_time_and_resume(tmp_path):
    g = _g(tmp_path)
    g.quiet = (0, 0)
    now = time.time()
    g.start_cooloff("paddypower", "HTTP 429", now)
    assert g.cooloff_active("paddypower", now + 25 * 3600)       # time alone: no
    g.resume("paddypower")
    assert g.cooloff_active("paddypower", now + 3600)            # resume alone: no
    assert not g.cooloff_active("paddypower", now + 25 * 3600)   # both: yes
    assert g.cooloff("paddypower") is None


def test_targets_clamped_and_hosts_checked(tmp_path):
    f = tmp_path / "t.toml"
    f.write_text('''
[[target]]
name = "a"
site = "paddypower"
url = "https://www.paddypower.com/football/x"
every_min = 1
stale_min = 1
  [target.matches]
  max_tabs = 50
  open_gap_min = 0
  relist_min = 1
''')
    (t,) = config.load_targets(f)
    assert t.every_min == 5 and t.stale_min == 10
    assert t.matches.max_tabs == 6 and t.matches.open_gap_min == 2 and t.matches.relist_min == 30

    f.write_text('[[target]]\nname="b"\nsite="paddypower"\nurl="https://evil.example/x"\n')
    with pytest.raises(ValueError, match="allowed host"):
        config.load_targets(f)
    f.write_text('[[target]]\nname="b"\nsite="betfair"\nurl="https://www.paddypower.com/"\n')
    with pytest.raises(ValueError, match="unknown site"):
        config.load_targets(f)


def test_example_targets_parse():
    targets = config.load_targets(config.HERE / "targets.example.toml")
    assert {t.site for t in targets} == {"paddypower", "bookiebashing"}
