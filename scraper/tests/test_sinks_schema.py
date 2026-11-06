"""Every file the Scraper drops for the Hub is one envelope of the Hub <-> Render protocol
(hub/schemas/protocol.schema.json, types scraper_tips and scraper_event). The Hub's tests
ingest envelopes checked against the same file (hub/tests/test_inbox.py)."""
import json
import time
from pathlib import Path

from jsonschema import Draft202012Validator

from scraper import config
from scraper.sinks import deliver_to_hub, emit_event

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "hub" / "schemas" / "protocol.schema.json"
SCHEMA = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def _only(d: Path) -> dict:
    (f,) = d.glob("*.json")
    return json.loads(f.read_text())


def test_a_tips_drop_is_a_valid_scraper_tips_envelope(tmp_path):
    t = config._target({"name": "tips-a", "site": "generic", "url": "https://a.example.com",
                        "parser": "example_tipster", "post": "hub:tips", "trust_weight": 0.5})
    rows = [{"event": "A v B", "market": "Match Result", "selection": "A", "advised_price": 2.1,
             "tip_time": "2026-11-05T08:00:00+00:00", "origin": "tipster"},
            {"event": "C v D", "market": "Match Result", "selection": "D"}]
    assert deliver_to_hub(tmp_path, t, rows, time.time())
    env = _only(tmp_path / "hub" / "tips")
    SCHEMA.validate(env)
    assert env["type"] == "scraper_tips" and env["client_id"] == "scraper" and len(env["msg_id"]) == 32
    assert env["body"]["source"] == "tips-a" and env["body"]["channel"] == "tips"
    assert all(len(r["tip_id"]) == 16 for r in env["body"]["rows"])


def test_an_event_is_a_valid_scraper_event_envelope(tmp_path):
    assert emit_event(tmp_path, "blocked_source", "paddypower", "HTTP 403 at /football", time.time())
    env = _only(tmp_path / "hub" / "events")
    SCHEMA.validate(env)
    assert env["type"] == "scraper_event" and env["body"]["kind"] == "blocked_source"


def test_two_drops_never_share_a_msg_id(tmp_path):
    now = time.time()
    emit_event(tmp_path, "unhealthy_source", "a", "x", now)
    emit_event(tmp_path, "unhealthy_source", "b", "y", now)
    ids = {json.loads(f.read_text())["msg_id"] for f in (tmp_path / "hub" / "events").glob("*.json")}
    assert len(ids) == 2


def test_rows_the_hub_would_reject_are_left_out_so_the_file_is_valid(tmp_path):
    t = config._target({"name": "tips-a", "site": "generic", "url": "https://a.example.com",
                        "parser": "example_tipster", "post": "hub:tips"})
    rows = [{"event": "A v B", "market": "Match Result", "selection": "A"},
            {"event": "", "market": "x", "selection": "y"},                                  # no event
            {"event": "E v F", "market": "m", "selection": "s", "advised_price": 5000},     # price
            {"event": "G v H", "market": "m", "selection": "s", "advised_price": 1.0}]      # same rule as the Hub
    assert deliver_to_hub(tmp_path, t, rows, time.time())
    env = _only(tmp_path / "hub" / "tips")
    SCHEMA.validate(env)
    assert [r["event"] for r in env["body"]["rows"]] == ["A v B"]
    # nothing good: no file at all
    assert not deliver_to_hub(tmp_path, t, [{"event": "", "market": "", "selection": ""}], time.time())
    assert len(list((tmp_path / "hub" / "tips").glob("*.json"))) == 1
