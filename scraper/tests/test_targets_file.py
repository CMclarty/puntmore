"""Targets are ONE file, targets.toml on the Hub: read at start, re-read by the
running service when it changes, and the state written to data/status.json for
the Hub. Nothing is fetched from or reported to Puntmore. No browser needed."""
import json
import os
import time
from types import SimpleNamespace

import pytest

from scraper import config
from scraper.alerts import Alerts
from scraper.guard import Guard
from scraper.runner import TARGETS_CHECK_S, Runner
from scraper.sites import SITES

ONE = '[[target]]\nname="one"\nsite="paddypower"\nurl="https://www.paddypower.com/one"\n'
TWO = '[[target]]\nname="two"\nsite="bookiebashing"\nurl="https://www.bookiebashing.net/two"\n'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCRAPER_TARGETS", raising=False)
    (tmp_path / "targets.toml").write_text(ONE)
    return tmp_path


def test_targets_come_from_data_targets_toml(env, monkeypatch):
    # The old server mode's variables change nothing: there is no other source.
    monkeypatch.setenv("PUNTMORE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SAVES_INGEST_KEY", "k")
    s = config.load()
    assert [t.name for t in s.targets] == ["one"]
    assert s.targets_file == env / "targets.toml"
    assert not hasattr(s, "from_server") and not (env / "config.json").exists()


def test_scraper_targets_names_another_file(env, monkeypatch):
    other = env / "other.toml"
    other.write_text(TWO)
    monkeypatch.setenv("SCRAPER_TARGETS", str(other))
    s = config.load()
    assert [t.name for t in s.targets] == ["two"] and s.targets_file == other


def _bump(path, text):
    """Rewrite the file with an mtime that has certainly moved."""
    path.write_text(text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def _runner(tmp_path):
    s = config.load()
    guard = Guard(tmp_path / "g.sqlite3")
    return Runner(s, ctx=None, guard=guard, alerts=Alerts(tmp_path / "alerts.log", None),
                  run_dir=tmp_path / "run")


def test_an_edit_applies_without_a_restart(env):
    r = _runner(env)
    now = time.time()
    r._reload_targets(now)                                   # unchanged: nothing happens
    assert [t.name for t in r.targets] == ["one"]
    _bump(env / "targets.toml", TWO)
    r._reload_targets(now + 1)                               # not due yet
    assert [t.name for t in r.targets] == ["one"]
    r._reload_targets(now + TARGETS_CHECK_S + 1)
    assert [t.name for t in r.targets] == ["two"]
    assert "one" in r.retired_targets                        # reported as retired


def test_a_broken_or_missing_file_keeps_the_running_targets(env, capsys):
    r = _runner(env)
    now = time.time()
    _bump(env / "targets.toml", '[[target]]\nname="half')    # a half-written save
    r._reload_targets(now + TARGETS_CHECK_S + 1)
    assert [t.name for t in r.targets] == ["one"]
    assert "keeping the current targets" in capsys.readouterr().out
    (env / "targets.toml").unlink()
    r._reload_targets(now + 2 * TARGETS_CHECK_S + 2)
    assert [t.name for t in r.targets] == ["one"]
    _bump(env / "targets.toml", ONE + TWO)                   # fixed: applies
    r._reload_targets(now + 3 * TARGETS_CHECK_S + 3)
    assert [t.name for t in r.targets] == ["one", "two"]


def test_status_is_written_locally_and_sent_nowhere(env, monkeypatch):
    import httpx

    def refuse(*a, **k):
        raise AssertionError("status must not leave the box")
    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "get", refuse)
    r = _runner(env)
    _bump(env / "targets.toml", TWO)
    now = time.time()
    r._reload_targets(now + TARGETS_CHECK_S + 1)
    r._report(now + TARGETS_CHECK_S + 1)
    body = json.loads((env / "status.json").read_text())
    assert body["targets_file"] == str(env / "targets.toml")
    states = {t["name"]: (t["state"], t["detail"]) for t in body["targets"]}
    assert states["one"] == ("retired", "removed from targets.toml")
    assert states["two"][0] == "running"
    assert {s["site"] for s in body["sites"]} == set(SITES)


def test_a_runner_without_a_file_never_reloads(tmp_path):
    s = SimpleNamespace(data_dir=tmp_path, capture_mb=10, capture_tab_mb=1, targets=[],
                        targets_file=None)
    guard = Guard(tmp_path / "g.sqlite3")
    r = Runner(s, ctx=None, guard=guard, alerts=Alerts(tmp_path / "alerts.log", None),
               run_dir=tmp_path / "run")
    r._reload_targets(time.time() + 10 * TARGETS_CHECK_S)
    assert r.targets == []


def test_parsers_are_modules():
    from scraper.parsers import PARSERS
    assert set(PARSERS) == {"pp_most", "bb_saves", "bb_builder", "example_tipster"}
    # Both are written (test_pp_most.py, test_bb_saves.py); bb_saves drives a
    # page, so with no page it fails like any parser would.
    with pytest.raises(Exception) as exc:
        PARSERS["bb_saves"](None, None)
    assert not isinstance(exc.value, NotImplementedError)
    with pytest.raises(Exception) as exc:
        PARSERS["bb_builder"](None, None)
    assert not isinstance(exc.value, NotImplementedError)


def test_a_timing_change_reschedules_a_fetch_target_in_place(tmp_path):
    tips = {"name": "tips", "site": "generic", "url": "https://tips.example.org/"}
    s = SimpleNamespace(data_dir=tmp_path, capture_mb=10, capture_tab_mb=1, targets_file=None,
                        targets=[config._target({**tips, "every_min": 600})])
    guard = Guard(tmp_path / "g.sqlite3")
    r = Runner(s, ctx=None, guard=guard, alerts=Alerts(tmp_path / "alerts.log", None),
               run_dir=tmp_path / "run")
    now = time.time()
    r.last_load_at["tips"], r.next_fetch["tips"] = now, now + 36_000
    r.apply_targets([config._target({**tips, "every_min": 60})])
    assert now + 0.75 * 3600 <= r.next_fetch["tips"] <= now + 1.35 * 3600
