"""The Scraper's drop folders -> stored tips for HubAI, and escalations.

Every file the Scraper writes is one protocol envelope (schemas/protocol.schema.json, types
scraper_tips / scraper_event); `_drop` here writes exactly that and checks it against the schema,
so a drop the Hub's tests ingest is one the Scraper's sinks would write."""
import fcntl
import http.client
import json
import logging
import time
import urllib.error
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from puntmore_hub import finder, inbox
from puntmore_hub.schemas import utcnow_iso

SCHEMA = Draft202012Validator(json.loads((Path(__file__).resolve().parent.parent / "schemas" / "protocol.schema.json").read_text()))
TYPE_OF = {"tips": "scraper_tips", "events": "scraper_event", "prices": "scraper_prices"}


def _drop(cfg, sub, name, body, *, bare=False, check=True):
    d = cfg.inbox_dir / sub
    d.mkdir(parents=True, exist_ok=True)
    env = body if bare else {"type": TYPE_OF[sub], "client_id": "scraper", "msg_id": uuid.uuid4().hex,
                             "ts": utcnow_iso(), "body": body}
    if check and not bare:
        SCHEMA.validate(env)
    (d / f"{int(time.time() * 1000)}-{name}.json").write_text(json.dumps(env))


def test_tips_are_stored_once_and_not_queued(cfg, store):
    body = {"source": "tips-a", "at": time.time(), "url": "https://a.example.com", "channel": "tips",
            "trust_weight": 0.5, "rows": [
                {"event": "A v B", "market": "Match Result", "selection": "A", "advised_price": 2.1, "origin": "tipster"},
                {"event": "C v D", "market": "Over/Under 2.5", "selection": "Over 2.5", "advised_price": 1.9},
                {"event": "", "market": "x", "selection": "y"},                       # rejected: no event
                {"event": "E v F", "market": "m", "selection": "s", "advised_price": 5000},  # rejected: price
            ]}
    _drop(cfg, "tips", "tips-a", body, check=False)   # the Scraper's sink keeps such rows out; the Hub still checks
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts == {"tip_files": 1, "tips": 2, "new_tips": 2, "rejected": 2, "events": 0,
                      "price_files": 0, "price_rows": 0, "price_dropped": 0, "price_waiting": 0}
    # Tips go to HubAI (`puntmore finder new`).
    new = finder.new_tips(store)
    assert [t["source_id"] for t in new] == ["tips-a", "tips-a"] and new[0]["origin"] == "tipster"
    extra = json.loads(store.conn.execute("SELECT extra FROM tips WHERE tip_id=?", (new[0]["tip_id"],)).fetchone()[0])
    assert extra["trust_weight"] == 0.5
    assert not list((cfg.inbox_dir / "tips").glob("*.json")) and len(list((cfg.inbox_dir / "done").glob("*.json"))) == 1
    # the same rows dropped again (a second sweep) add nothing
    _drop(cfg, "tips", "tips-a", body, check=False)
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["new_tips"] == 0 and store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 2


def test_events_become_escalations(cfg, store):
    _drop(cfg, "events", "tips-a", {"kind": "unhealthy_source", "ref": "tips-a", "detail": "only 0 row(s) - layout change?", "at": time.time()})
    _drop(cfg, "events", "generic", {"kind": "blocked_source", "ref": "generic", "detail": "HTTP 403", "at": time.time()})
    _drop(cfg, "events", "odd", {"kind": "something_new", "ref": "x", "detail": "?", "at": time.time()}, bare=True)
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["events"] == 3
    kinds = sorted(e["kind"] for e in store.open_escalations())
    assert kinds == ["blocked_source", "other", "unhealthy_source"]
    assert not list((cfg.inbox_dir / "events").glob("*.json"))


def test_unreadable_files_are_moved_aside_not_retried_forever(cfg, store):
    d = cfg.inbox_dir / "tips"
    d.mkdir(parents=True)
    (d / "1-bad.json").write_text("{not json")
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["tip_files"] == 0 and not list(d.glob("*.json"))


def test_scraper_status_reads_heartbeat_and_status_file(cfg):
    cfg.scraper_data_dir.mkdir(parents=True)
    assert inbox.scraper_status(cfg)["running"] is False
    (cfg.scraper_data_dir / "heartbeat").write_text(str(int(time.time())))
    (cfg.scraper_data_dir / "status.json").write_text(json.dumps({"targets": [
        {"name": "pp-epl", "site": "paddypower", "mode": "keep", "state": "running", "detail": "3 tabs open", "rows_posted": 10},
        {"name": "tips-a", "site": "generic", "mode": "fetch", "state": "running", "detail": "only 0 row(s), expected at least 2 - layout change?", "rows_posted": 0},
        {"name": "bb-saves", "site": "bookiebashing", "mode": "keep", "state": "blocked", "detail": "backing off", "rows_posted": 0},
    ], "sites": []}))
    w = inbox.scraper_status(cfg)
    assert w["running"] and w["unhealthy"] == ["tips-a"] and w["blocked"] == ["bb-saves"]
    (cfg.scraper_data_dir / "heartbeat").write_text(str(int(time.time()) - 3600))
    assert inbox.scraper_status(cfg)["running"] is False


def test_scraper_data_env_prefers_the_new_name_and_falls_back_to_the_old(monkeypatch):
    from puntmore_hub.config import _env_first
    monkeypatch.delenv("PUNTMORE_SCRAPER_DATA", raising=False)
    monkeypatch.delenv("PUNTMORE_WORKER_DATA", raising=False)
    assert _env_first("PUNTMORE_SCRAPER_DATA", "PUNTMORE_WORKER_DATA", default="/d") == "/d"
    monkeypatch.setenv("PUNTMORE_WORKER_DATA", "/old")
    assert _env_first("PUNTMORE_SCRAPER_DATA", "PUNTMORE_WORKER_DATA", default="/d") == "/old"
    monkeypatch.setenv("PUNTMORE_SCRAPER_DATA", "/new")
    assert _env_first("PUNTMORE_SCRAPER_DATA", "PUNTMORE_WORKER_DATA", default="/d") == "/new"


def test_a_drop_from_before_the_envelope_still_reads(cfg, store):
    """Files already on disk when the Hub updates are bare bodies; they are not stranded."""
    _drop(cfg, "tips", "tips-a", {"source": "tips-a", "at": time.time(), "url": "https://a.example.com",
                                  "channel": "tips", "trust_weight": 0.5,
                                  "rows": [{"event": "A v B", "market": "Match Result", "selection": "A"}]}, bare=True)
    _drop(cfg, "events", "tips-a", {"kind": "blocked_source", "ref": "tips-a", "detail": "HTTP 403", "at": time.time()},
          bare=True)
    counts = inbox.ingest(store, cfg, notify=False)
    assert counts["new_tips"] == 1 and counts["events"] == 1


# The Scraper's price drops (scraper_prices) -> Render, posted by the inbox.

MOST_ROW = {"home": "A", "away": "B", "kickoff": "2026-11-08T16:30:00Z", "market": "most_corners",
            "home_price": 1.5333, "tie_price": 9.0, "away_price": 2.375}
SAVES_ROW = {"home": "Arsenal", "away": "Spurs", "kickoff": "2026-11-08T16:30:00Z", "line": 6.5, "over": 1.952}


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body if isinstance(body, str) else json.dumps(body)

    def read(self):
        return self._body.encode()


class _Render:
    """Stands in for Render: answers each post with the next code (200 when none are left)."""
    def __init__(self, *codes):
        self.codes, self.sent = list(codes), []

    def __call__(self, req, timeout):
        self.sent.append((req.full_url, req.get_header("Authorization"), json.loads(req.data)))
        code = self.codes.pop(0) if self.codes else 200
        return _Resp(code, {"stored": 1, "rejected": []} if code == 200 else "nope")


@pytest.fixture
def pcfg(cfg):
    return cfg.with_overrides(render_http="https://puntmore.example/", render_api_key="k-ingest")


def _price_drop(cfg, at, *, route="/api/paddy/most-prices", rows=None, check=True):
    body = {"source": "pp-epl-most", "at": at, "url": "https://www.paddypower.com/x", "route": route,
            "rows": rows if rows is not None else [MOST_ROW]}
    env = {"type": "scraper_prices", "client_id": "scraper", "msg_id": uuid.uuid4().hex, "ts": utcnow_iso(), "body": body}
    if check:
        SCHEMA.validate(env)
    d = cfg.inbox_dir / "prices"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{int(at * 1000)}-pp-epl-most-{env['msg_id'][:8]}.json"
    path.write_text(json.dumps(env))
    return path


def _left(cfg):
    return sorted(p.name for p in (cfg.inbox_dir / "prices").glob("*.json"))


def _done(cfg):
    return sorted((cfg.inbox_dir / "done" / "prices").glob("*.json"))


def test_a_price_drop_is_posted_to_its_route_with_todays_body_and_moved_aside(pcfg, store):
    _price_drop(pcfg, time.time())
    render = _Render(200)
    counts = inbox.ingest(store, pcfg, notify=False, opener=render)
    assert render.sent == [("https://puntmore.example/api/paddy/most-prices", "Bearer k-ingest", {"prices": [MOST_ROW]})]
    assert {k: counts[k] for k in inbox.PRICE_COUNT_KEYS} == {"price_files": 1, "price_rows": 1, "price_dropped": 0, "price_waiting": 0}
    assert _left(pcfg) == [] and len(_done(pcfg)) == 1
    assert not list((pcfg.inbox_dir / "done").glob("*.json"))      # the tips' done/ is not flooded


def test_bb_saves_rows_go_to_the_saves_route(pcfg, store):
    _price_drop(pcfg, time.time(), route="/api/paddy/saves-fair", rows=[SAVES_ROW])
    render = _Render(200)
    inbox.ingest(store, pcfg, notify=False, opener=render)
    assert render.sent[0][0].endswith("/api/paddy/saves-fair") and render.sent[0][2] == {"prices": [SAVES_ROW]}


def test_files_are_posted_oldest_first(pcfg, store):
    now = time.time()
    for i, home in enumerate(["first", "second", "third"]):
        _price_drop(pcfg, now - 120 + 60 * i, rows=[{**MOST_ROW, "home": home}])
    render = _Render()
    inbox.ingest(store, pcfg, notify=False, opener=render)
    assert [s[2]["prices"][0]["home"] for s in render.sent] == ["first", "second", "third"]


@pytest.mark.parametrize("failure", [500, 502, 503, 408, 429, urllib.error.URLError("refused"),
                                     TimeoutError("timed out"), http.client.RemoteDisconnected("closed")])
def test_render_down_leaves_the_files_and_stops_the_pass_so_order_is_kept(pcfg, store, failure):
    now = time.time()
    _price_drop(pcfg, now - 60, rows=[{**MOST_ROW, "home": "older"}])
    _price_drop(pcfg, now, rows=[{**MOST_ROW, "home": "newer"}])

    def down(req, timeout):
        down.calls += 1
        if isinstance(failure, int):
            return _Resp(failure, "down")
        raise failure
    down.calls = 0
    counts = inbox.ingest(store, pcfg, notify=False, opener=down)
    assert down.calls == 1 and counts["price_waiting"] == 2 and counts["price_files"] == 0
    assert len(_left(pcfg)) == 2 and _done(pcfg) == []
    render = _Render()
    inbox.ingest(store, pcfg, notify=False, opener=render)               # Render is back
    assert [s[2]["prices"][0]["home"] for s in render.sent] == ["older", "newer"] and _left(pcfg) == []


@pytest.mark.parametrize("code", [301, 302, 100])
def test_a_redirect_or_informational_answer_is_a_setup_fault_so_the_files_wait(pcfg, store, code, caplog):
    now = time.time()
    _price_drop(pcfg, now - 60, rows=[{**MOST_ROW, "home": "older"}])
    _price_drop(pcfg, now, rows=[{**MOST_ROW, "home": "newer"}])
    render = _Render(code)
    counts = inbox.ingest(store, pcfg, notify=False, opener=render)
    assert len(render.sent) == 1 and counts["price_waiting"] == 2 and counts["price_dropped"] == 0
    assert len(_left(pcfg)) == 2 and _done(pcfg) == []
    assert f"HTTP {code}; 2 file(s) wait for the next pass" in caplog.text


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_a_refused_body_is_moved_aside_not_sent_again(pcfg, store, code):
    _price_drop(pcfg, time.time())
    render = _Render(code)
    counts = inbox.ingest(store, pcfg, notify=False, opener=render)
    assert len(render.sent) == 1 and counts["price_dropped"] == 1 and _left(pcfg) == [] and len(_done(pcfg)) == 1
    inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent again"))


def test_a_price_file_past_the_cut_off_is_moved_aside_unsent(pcfg, store):
    assert inbox.PRICE_MAX_AGE_S == 15 * 60
    _price_drop(pcfg, time.time() - inbox.PRICE_MAX_AGE_S - 1)
    counts = inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_dropped"] == 1 and _left(pcfg) == [] and len(_done(pcfg)) == 1


def test_a_route_this_inbox_does_not_know_waits_until_it_is_stale(pcfg, store):
    never = lambda req, timeout: pytest.fail("sent")
    _price_drop(pcfg, time.time(), route="/api/paddy/new-kind", check=False)
    counts = inbox.ingest(store, pcfg, notify=False, opener=never)
    assert counts["price_waiting"] == 1 and len(_left(pcfg)) == 1
    _price_drop(pcfg, time.time() - inbox.PRICE_MAX_AGE_S - 1, route="/api/paddy/new-kind", check=False)
    counts = inbox.ingest(store, pcfg, notify=False, opener=never)
    assert counts["price_dropped"] == 1 and counts["price_waiting"] == 1


def test_a_malformed_price_file_is_moved_aside(pcfg, store):
    good = {"source": "pp-epl-most", "at": time.time(), "url": "u", "route": "/api/paddy/most-prices", "rows": [MOST_ROW]}
    bad = ["{not json", "[]", json.dumps({**good, "rows": []}), json.dumps({k: v for k, v in good.items() if k != "at"}),
           json.dumps({**good, "rows": "x"}), json.dumps({**good, "rows": [1]}), json.dumps({**good, "route": 7})]
    d = pcfg.inbox_dir / "prices"
    d.mkdir(parents=True)
    for i, text in enumerate(bad):
        (d / f"{i}-bad.json").write_text(text)
    counts = inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_dropped"] == len(bad) and _left(pcfg) == []


def test_no_render_configured_leaves_prices_waiting(cfg, store):
    _price_drop(cfg, time.time())                                       # cfg: render_http and key blank
    counts = inbox.ingest(store, cfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_waiting"] == 1 and len(_left(cfg)) == 1


def test_render_hanging_never_holds_up_tips_or_events(pcfg, store):
    _drop(pcfg, "tips", "tips-a", {"source": "tips-a", "at": time.time(), "url": "https://a.example.com",
                                   "channel": "tips", "trust_weight": 0.5,
                                   "rows": [{"event": "A v B", "market": "Match Result", "selection": "A"}]})
    _price_drop(pcfg, time.time())

    def hang(req, timeout):
        raise TimeoutError("timed out")
    counts = inbox.ingest(store, pcfg, notify=False, opener=hang)
    assert counts["new_tips"] == 1 and counts["price_waiting"] == 1


def test_a_half_written_price_drop_is_never_read(pcfg, store):
    d = pcfg.inbox_dir / "prices"
    d.mkdir(parents=True)
    (d / "1-pp-epl-most-abcd1234.json.tmp").write_text("{")
    counts = inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert (d / "1-pp-epl-most-abcd1234.json.tmp").exists() and counts["price_dropped"] == 0


def test_a_second_pass_while_one_holds_the_lock_posts_nothing(pcfg, store):
    _price_drop(pcfg, time.time())
    with open(pcfg.inbox_dir / "prices" / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        counts = inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_waiting"] == 1 and len(_left(pcfg)) == 1


def test_the_hubs_routes_are_the_schemas():
    assert set(inbox.PRICE_ROUTES) == set(SCHEMA.schema["$defs"]["scraper_prices"]["properties"]["route"]["enum"])


@pytest.mark.parametrize("at", ["NaN", "Infinity", "-Infinity", "true", '"1731000000"', "1" + "0" * 400])
def test_at_must_be_a_finite_number_and_a_bad_one_does_not_stop_the_pass(pcfg, store, at):
    d = pcfg.inbox_dir / "prices"
    d.mkdir(parents=True)
    (d / "0-bad.json").write_text('{"source": "pp-epl-most", "at": %s, "url": "u", '
                                  '"route": "/api/paddy/most-prices", "rows": [{"home": "A"}]}' % at)
    _price_drop(pcfg, time.time())
    render = _Render()
    counts = inbox.ingest(store, pcfg, notify=False, opener=render)
    assert counts["price_dropped"] == 1 and counts["price_files"] == 1 and len(render.sent) == 1
    assert _left(pcfg) == [] and "0-bad.json" in [p.name for p in _done(pcfg)]


def test_an_unexpected_error_propagates_and_leaves_the_lock_free(pcfg, store):
    """Only OSError, http.client.HTTPException and RuntimeError mean "Render down"; anything
    else is a bug and propagates (the daemon records it as a failed pass). The lock is released
    with the file, so the next pass posts."""
    _price_drop(pcfg, time.time())

    def broken(req, timeout):
        raise ValueError("unexpected")
    with pytest.raises(ValueError, match="unexpected"):
        inbox.post_prices(pcfg, opener=broken)
    assert len(_left(pcfg)) == 1
    render = _Render()
    counts = inbox.post_prices(pcfg, opener=render)
    assert len(render.sent) == 1 and counts["price_files"] == 1 and _left(pcfg) == []


def test_no_render_key_says_the_hub_needs_one(cfg, store, caplog):
    c = cfg.with_overrides(render_http="https://puntmore.example/", render_api_key="")
    _price_drop(c, time.time())
    counts = inbox.ingest(store, c, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_waiting"] == 1
    assert "PUNTMORE_RENDER_API_KEY is not set: the Hub needs a key with the ingest grant" in caplog.text


def test_the_render_key_is_never_logged_or_written(cfg, store, caplog):
    key = "fake-render-key-7c1e"
    c = cfg.with_overrides(render_http="https://puntmore.example/", render_api_key=key)
    now = time.time()
    for i, home in enumerate(["ok", "refused", "network"]):
        _price_drop(c, now - 60 + 10 * i, rows=[{**MOST_ROW, "home": home}])
    answers = [_Resp(200, {"stored": 1}), _Resp(403, "forbidden")]
    seen = []

    def render(req, timeout):
        seen.append(req.get_header("Authorization"))
        if answers:
            return answers.pop(0)
        raise urllib.error.URLError("refused")
    caplog.set_level(logging.DEBUG)
    counts = inbox.ingest(store, c, notify=False, opener=render)
    assert seen == [f"Bearer {key}"] * 3                                  # it was sent, so it was there to leak
    assert (counts["price_files"], counts["price_dropped"], counts["price_waiting"]) == (1, 1, 1)
    assert caplog.records and not any(key in r.getMessage() for r in caplog.records) and key not in caplog.text
    files = [p for p in c.inbox_dir.rglob("*") if p.is_file()]
    assert len(files) >= 3 and not any(key in p.read_text(errors="replace") for p in files)
