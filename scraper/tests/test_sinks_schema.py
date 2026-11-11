"""Every file the Scraper drops for the Hub is one envelope of the Hub <-> Render protocol
(hub/schemas/protocol.schema.json, types scraper_tips, scraper_event and scraper_prices). The
Hub's tests ingest envelopes checked against the same file (hub/tests/test_inbox.py)."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from scraper import config
from scraper import sinks
from scraper.sinks import PRICE_ROUTES, Sinks, deliver_prices, deliver_to_hub, emit_event

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


MOST = [{"home": "Chelsea", "away": "Man Utd", "kickoff": "2026-11-08T16:30:00Z", "market": "most_corners",
         "home_price": 1.5333, "tie_price": 9.0, "away_price": 2.375}]


def _price_target(post="/api/paddy/most-prices"):
    return config._target({"name": "pp-epl-most", "site": "paddypower",
                           "url": "https://www.paddypower.com/football/english-premier-league",
                           "parser": "pp_most", "post": post})


def test_a_price_target_drops_its_rows_for_the_hub_and_sends_nothing(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("the Scraper must not post to Render"))
    t, now = _price_target(), time.time()
    assert Sinks(SimpleNamespace(data_dir=tmp_path)).deliver(t, MOST, now)
    env = _only(tmp_path / "hub" / "prices")
    SCHEMA.validate(env)
    assert env["type"] == "scraper_prices" and env["client_id"] == "scraper" and len(env["msg_id"]) == 32
    assert env["body"] == {"source": "pp-epl-most", "at": now, "url": t.url,
                           "route": "/api/paddy/most-prices", "rows": MOST}
    assert not list((tmp_path / "hub" / "tips").glob("*"))     # never where an old inbox reads tips


def test_bb_saves_rows_take_the_same_path_unchanged(tmp_path):
    t = config._target({"name": "bb-saves", "site": "bookiebashing",
                        "url": "https://www.bookiebashing.net/tools/betbuilder/", "every_min": 10,
                        "parser": "bb_saves", "post": "/api/paddy/saves-fair"})
    rows = [{"home": "Arsenal", "away": "Spurs", "kickoff": "2026-11-08T16:30:00Z", "line": 6.5, "over": 1.952}]
    assert deliver_prices(tmp_path, t, rows, time.time())
    env = _only(tmp_path / "hub" / "prices")
    SCHEMA.validate(env)
    assert env["body"]["route"] == "/api/paddy/saves-fair" and env["body"]["rows"] == rows


def test_two_tabs_of_one_target_in_one_tick_make_two_files(tmp_path):
    t, now = _price_target(), time.time()
    other = [{**MOST[0], "home": "Arsenal", "away": "Spurs"}]
    assert deliver_prices(tmp_path, t, MOST, now) and deliver_prices(tmp_path, t, other, now)
    files = sorted((tmp_path / "hub" / "prices").glob("*.json"))
    assert len(files) == 2 and all(f.name.startswith(f"{int(now * 1000)}-pp-epl-most-") for f in files)
    assert sorted(json.loads(f.read_text())["body"]["rows"][0]["home"] for f in files) == ["Arsenal", "Chelsea"]


def test_price_rows_are_not_filtered_render_validates_them(tmp_path):
    odd = [{**MOST[0], "home_price": 500.0}]          # Render's model refuses it; today's body carried it
    assert deliver_prices(tmp_path, _price_target(), odd, time.time())
    assert _only(tmp_path / "hub" / "prices")["body"]["rows"] == odd


def test_no_rows_or_no_route_drops_nothing(tmp_path):
    assert not deliver_prices(tmp_path, _price_target(), [], time.time())
    unknown = SimpleNamespace(name="x", url="https://www.paddypower.com/", post="/api/paddy/feed")
    assert not deliver_prices(tmp_path, unknown, MOST, time.time())
    assert not list((tmp_path / "hub" / "prices").glob("*"))      # no file, whether or not the folder exists


@pytest.mark.parametrize("fails", ["write", "rename"])
def test_a_failed_price_drop_returns_false_and_leaves_no_tmp_file(tmp_path, monkeypatch, fails):
    """A full disk or a rename that fails costs one parse's prices: the Scraper keeps running, and
    the half-written `*.json.tmp` is removed, not left in prices/ for good."""
    real_write = Path.write_text

    def write_half(self, text, *a, **k):
        real_write(self, text[:10], *a, **k)
        raise OSError(28, "No space left on device")

    def no_rename(self, target):
        raise OSError(1, "Operation not permitted")
    if fails == "write":
        monkeypatch.setattr(Path, "write_text", write_half)
    else:
        monkeypatch.setattr(Path, "rename", no_rename)
    assert deliver_prices(tmp_path, _price_target(), MOST, time.time()) is False
    assert list((tmp_path / "hub" / "prices").iterdir()) == []


def test_the_scrapers_routes_are_the_schemas():
    assert set(PRICE_ROUTES) == set(SCHEMA.schema["$defs"]["scraper_prices"]["properties"]["route"]["enum"])


def test_a_post_with_no_hub_route_is_refused_when_targets_load():
    for post in ("/api/paddy/feed", "/api/finder/rows", "tips"):
        with pytest.raises(ValueError, match="post must be"):
            _price_target(post)
    assert _price_target("/api/paddy/saves-fair").post == "/api/paddy/saves-fair"
    assert _price_target("hub:tips").post == "hub:tips"


def test_the_scraper_holds_no_render_url_or_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCRAPER_TARGETS", raising=False)
    monkeypatch.setenv("SAVES_INGEST_KEY", "k")
    s = config.load()
    assert not hasattr(s, "ingest_key") and not hasattr(s, "puntmore_url")
    assert not hasattr(sinks, "RenderSink")
