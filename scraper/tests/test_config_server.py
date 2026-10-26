"""Targets from Puntmore (GET /api/scraper/config), the cache when it is
unreachable, targets.toml when neither - against a fake HTTP server."""
import http.server
import json
import threading

import pytest

from scraper import config

KEY = "k3y"
SERVED = {
    "targets": [
        {"name": "pp-epl-most", "site": "paddypower",
         "url": "https://www.paddypower.com/football/english-premier-league",
         "every_min": None, "stale_min": 20, "parser": "pp_most", "post": "/api/paddy/most-prices",
         "matches": {"link_re": "-v-", "max_tabs": 3, "market_tabs": ["Corners", "Cards"],
                     "open_gap_min": 4, "tab_max_hours": 8, "relist_min": 60}},
        {"name": "bb-saves", "site": "bookiebashing", "url": "https://www.bookiebashing.net/saves",
         "every_min": 10, "stale_min": 20, "parser": "bb_saves", "post": "/api/paddy/saves-fair"},
    ],
    "timings": {},
}


class Server(http.server.BaseHTTPRequestHandler):
    body = SERVED
    seen_keys: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Server.seen_keys.append(self.headers.get("X-API-Key"))
        if self.path != "/api/scraper/config" or self.headers.get("X-API-Key") != KEY:
            self.send_response(401)
            self.end_headers()
            return
        b = json.dumps(Server.body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Server.body, Server.seen_keys = SERVED, []
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCRAPER_TARGETS", raising=False)
    monkeypatch.setenv("SAVES_INGEST_KEY", KEY)
    (tmp_path / "targets.toml").write_text(
        '[[target]]\nname="offline"\nsite="paddypower"\nurl="https://www.paddypower.com/x"\n')
    return tmp_path


def test_targets_come_from_the_server_and_are_cached(env, server, monkeypatch):
    monkeypatch.setenv("PUNTMORE_URL", server)
    s = config.load()
    assert s.from_server and s.targets_source == "server"
    assert [t.name for t in s.targets] == ["pp-epl-most", "bb-saves"]
    pp = s.targets[0]
    assert pp.every_min is None and pp.matches.market_tabs == ("Corners", "Cards")
    assert Server.seen_keys == [KEY]
    assert json.loads((env / "config.json").read_text())["targets"][1]["name"] == "bb-saves"


def test_unreachable_server_falls_back_to_the_cache_then_the_toml(env, server, monkeypatch):
    monkeypatch.setenv("PUNTMORE_URL", server)
    config.load()                                            # writes the cache
    monkeypatch.setenv("PUNTMORE_URL", "http://127.0.0.1:9")  # nothing listens there
    s = config.load()
    assert s.targets_source == "cache" and [t.name for t in s.targets] == ["pp-epl-most", "bb-saves"]
    (env / "config.json").unlink()
    s = config.load()
    assert s.targets_source == "toml" and [t.name for t in s.targets] == ["offline"]
    assert s.from_server                                     # still re-fetched while running


def test_scraper_targets_env_forces_the_toml(env, server, monkeypatch):
    monkeypatch.setenv("PUNTMORE_URL", server)
    monkeypatch.setenv("SCRAPER_TARGETS", str(env / "targets.toml"))
    s = config.load()
    assert not s.from_server and s.targets_source == "toml" and Server.seen_keys == []


def test_without_url_and_key_it_is_the_toml(env, monkeypatch):
    monkeypatch.delenv("PUNTMORE_URL", raising=False)
    s = config.load()
    assert not s.from_server and [t.name for t in s.targets] == ["offline"]


def test_one_bad_served_target_is_skipped_not_fatal(env, server, monkeypatch):
    monkeypatch.setenv("PUNTMORE_URL", server)
    Server.body = {"targets": [
        {"name": "evil", "site": "paddypower", "url": "https://evil.example/x"},
        {"name": "nosite", "site": "betfair", "url": "https://www.paddypower.com/x"},
        SERVED["targets"][1], SERVED["targets"][1],          # duplicate name
    ]}
    s = config.load()
    assert [t.name for t in s.targets] == ["bb-saves"]


def test_served_timings_below_the_floors_are_still_clamped(env, server, monkeypatch):
    monkeypatch.setenv("PUNTMORE_URL", server)
    t = dict(SERVED["targets"][0], every_min=1, stale_min=1,
             matches=dict(SERVED["targets"][0]["matches"], max_tabs=40, open_gap_min=0,
                          relist_min=1))
    Server.body = {"targets": [t]}
    (pp,) = config.load().targets
    assert (pp.every_min, pp.stale_min) == (5, 10)
    assert (pp.matches.max_tabs, pp.matches.open_gap_min, pp.matches.relist_min) == (6, 2, 30)


def test_parsers_are_modules_and_unwritten_ones_say_so():
    from scraper.parsers import PARSERS
    assert set(PARSERS) == {"pp_most", "bb_saves"}
    # pp_most is still unwritten and says so; bb_saves drives a page, so with
    # no page it fails like any parser would (test_bb_saves.py drives it).
    with pytest.raises(NotImplementedError):
        PARSERS["pp_most"](None, None)
    with pytest.raises(Exception) as exc:
        PARSERS["bb_saves"](None, None)
    assert not isinstance(exc.value, NotImplementedError)
