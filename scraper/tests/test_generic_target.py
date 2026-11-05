"""Bet Finder sources as Worker targets: site rules, fetch mode, row health, the
hand-off to the Hub. No browser needed."""
import json
import time
from types import SimpleNamespace

import pytest

from scraper import config
from scraper.alerts import Alerts
from scraper.guard import Guard
from scraper.runner import Runner, deliver_to_hub, emit_event
from scraper.sites import SITES


def test_generic_site_budget_is_in_code():
    g = SITES["generic"]
    assert (g.per_hour, g.per_day, g.quiet_hours, g.block_means_cooloff) == (60, 400, False, True)


def test_generic_targets_may_not_open_the_bookmakers():
    assert config.host_allowed("https://tips.example.com/today", "generic")
    assert not config.host_allowed("https://www.paddypower.com/football", "generic")
    assert not config.host_allowed("https://bookiebashing.net/tools", "generic")
    assert config.host_allowed("https://www.paddypower.com/football", "paddypower")
    assert not config.host_allowed("https://tips.example.com/today", "paddypower")


def test_generic_target_defaults_to_fetch_and_carries_bet_finder_fields():
    t = config._target({"name": "t", "site": "generic", "url": "https://tips.example.com",
                        "parser": "example_tipster", "post": "hub:tips", "min_rows": 3,
                        "trust_weight": 1.7, "wait_for": ".tips"})
    assert t.mode == "fetch" and t.enabled and t.min_rows == 3 and t.wait_for == ".tips"
    assert t.trust_weight == 1.0          # clamped to 0..1
    with pytest.raises(ValueError):
        config._target({"name": "t", "site": "generic", "url": "https://www.paddypower.com/x"})
    with pytest.raises(ValueError):
        config._target({"name": "t", "site": "generic", "url": "https://x.example.com",
                        "matches": {"link_re": "-v-"}})


def test_paddy_targets_keep_their_page_by_default():
    t = config._target({"name": "p", "site": "paddypower", "url": "https://www.paddypower.com/football"})
    assert t.mode == "keep"


def test_disabled_targets_reserve_their_name_but_never_run(tmp_path):
    toml = tmp_path / "t.toml"
    toml.write_text('''
[[target]]
name = "a"
site = "generic"
url = "https://a.example.com"
enabled = false
[[target]]
name = "a"
site = "generic"
url = "https://b.example.com"
''')
    with pytest.raises(ValueError):
        config.load_targets(toml)
    toml.write_text('''
[[target]]
name = "a"
site = "generic"
url = "https://a.example.com"
enabled = false
''')
    assert config.load_targets(toml) == []


def _runner(tmp_path):
    s = SimpleNamespace(data_dir=tmp_path, capture_mb=10, capture_tab_mb=1, targets=[],
                        targets_source="toml", from_server=False)
    guard = Guard(tmp_path / "g.sqlite3", {k: (v.per_hour, v.per_day) for k, v in SITES.items()})
    return Runner(s, ctx=None, guard=guard, alerts=Alerts(tmp_path / "alerts.log", None),
                  run_dir=tmp_path / "run")


def test_rows_are_handed_to_the_hub_as_one_file_per_run(tmp_path):
    t = config._target({"name": "tips-a", "site": "generic", "url": "https://a.example.com",
                        "parser": "example_tipster", "post": "hub:tips", "trust_weight": 0.5})
    rows = [{"event": "A v B", "market": "Match Result", "selection": "A", "advised_price": 2.1}]
    now = time.time()
    assert deliver_to_hub(tmp_path, t, rows, now)
    files = list((tmp_path / "hub" / "tips").glob("*.json"))
    assert len(files) == 1 and not list((tmp_path / "hub" / "tips").glob("*.tmp"))
    body = json.loads(files[0].read_text())
    assert body["source"] == "tips-a" and body["channel"] == "tips" and body["trust_weight"] == 0.5
    assert body["rows"] == rows


def test_short_row_count_alerts_once_and_tells_the_hub(tmp_path):
    r = _runner(tmp_path)
    t = config._target({"name": "tips-a", "site": "generic", "url": "https://a.example.com",
                        "parser": "example_tipster", "post": "hub:tips", "min_rows": 2})
    now = time.time()
    r._check_rows(t, [{"event": "x"}], now)
    r._check_rows(t, [{"event": "x"}], now + 60)
    events = list((tmp_path / "hub" / "events").glob("*.json"))
    assert len(events) == 1
    body = json.loads(events[0].read_text())
    assert body["kind"] == "unhealthy_source" and body["ref"] == "tips-a"
    assert "layout change" in r.parser_said["tips-a"]
    log = (tmp_path / "alerts.log").read_text()
    assert log.count("layout change") == 1
    r._check_rows(t, [{"event": "x"}, {"event": "y"}], now + 120)
    assert "tips-a" not in r.health_said and "healthy again" in (tmp_path / "alerts.log").read_text()


def test_a_block_is_an_event_for_the_hub(tmp_path):
    assert emit_event(tmp_path, "blocked_source", "generic", "HTTP 403 on https://a.example.com", time.time())
    body = json.loads(next((tmp_path / "hub" / "events").glob("*.json")).read_text())
    assert body["kind"] == "blocked_source" and body["ref"] == "generic"


def test_fetch_target_schedules_before_parsing_and_backs_off_on_a_failed_load(tmp_path):
    r = _runner(tmp_path)
    t = config._target({"name": "tips-a", "site": "generic", "url": "https://a.example.com",
                        "parser": "example_tipster", "post": "hub:tips", "every_min": 60})
    now = time.time()
    r._load = lambda target, key, url, when: None          # a load that fails
    r._tick_fetch(t, now)
    assert r.next_fetch["tips-a"] == now + 300
    calls = []
    page = SimpleNamespace(content=lambda: '<li class="tip">A v B | Match Result | A | 2.1</li>')
    tab = SimpleNamespace(target=t, page=page, capture=None, next_parse=0.0, url=t.url)
    r._load = lambda target, key, url, when: tab
    r._close = lambda key: calls.append(("close", key))
    r._tick_fetch(t, now + 300)
    assert calls == [("close", "fetch:tips-a")]
    assert now + 300 + 3600 * 0.75 <= r.next_fetch["tips-a"] <= now + 300 + 3600 * 1.35
    assert len(list((tmp_path / "hub" / "tips").glob("*.json"))) == 1
