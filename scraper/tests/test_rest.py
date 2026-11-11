"""rest.py: a job's rest, kept in data/resting.json so it survives a restart."""
import os

import pytest

from scraper import rest


def test_round_trip(tmp_path):
    resting = {"bb-saves": {"until": 1_000.5, "why": "no games within 48 hours"}}
    rest.save(tmp_path, resting)
    assert rest.load(tmp_path) == resting
    assert (tmp_path / "resting.json").exists()


def test_missing_or_unreadable_file_is_empty(tmp_path):
    assert rest.load(tmp_path) == {}
    (tmp_path / "resting.json").write_text("{not json")
    assert rest.load(tmp_path) == {}
    (tmp_path / "resting.json").write_text('["a list"]')
    assert rest.load(tmp_path) == {}
    (tmp_path / "resting.json").write_text('{"a": {"until": "soon"}, "b": {"until": 5, "why": "x"}}')
    assert rest.load(tmp_path) == {"b": {"until": 5.0, "why": "x"}}


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_save_to_an_unwritable_dir_does_not_raise(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        rest.save(ro, {"x": {"until": 1.0, "why": "y"}})
        rest.save(tmp_path / "missing" / "deeper", {"x": {"until": 1.0, "why": "y"}})
    finally:
        ro.chmod(0o700)
    assert rest.load(ro) == {}


def test_rest_carries_until_and_why():
    r = rest.Rest(123.0, "no games listed under the configured competitions")
    assert (r.until, r.why) == (123.0, "no games listed under the configured competitions")
    assert isinstance(r, Exception)
    assert rest.MAX_REST_S == 7 * 86400 and rest.MIN_REST_S == 60


def test_when_is_london_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ts = datetime(2026, 11, 19, 12, 30, tzinfo=ZoneInfo("Europe/London")).timestamp()
    assert rest.when(ts) == "Thu 19 Nov 12:30"
    ts = datetime(2026, 7, 4, 9, 5, tzinfo=ZoneInfo("UTC")).timestamp()
    assert rest.when(ts) == "Sat 4 Jul 10:05"                       # BST
