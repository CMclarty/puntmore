"""The console's Scraper tab: targets.toml through scraper/targets.py, against fixture files.

The fixture targets.toml is in the library's own shape; status.json is the Scraper's
(`Runner.status()`, as in test_web.py). Every write goes through `scraper.targets`, which the
console imports from the repo (scraper_targets.library); nothing here writes the TOML itself.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from puntmore_hub.web import actions, collect, scraper_targets  # noqa: E402
from puntmore_hub.web.app import create_app  # noqa: E402

TOKEN = "s3cret-token"
H = {"X-Puntmore-Token": TOKEN}
XSS = '<script>alert("x")</script>'

TOML = """\
[[target]]
name = "bb-saves"
site = "bookiebashing"
url = "https://www.bookiebashing.net/tools/betbuilder/"
every_min = 10
parser = "bb_saves"
post = "/api/paddy/saves-fair"

[[target]]
name = "tipster one"
site = "generic"
url = "https://tips.example/today"
every_min = 360
parser = "example_tipster"
post = "hub:tips"
min_rows = 2
trust_weight = 0.5
enabled = false
"""


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(collect, "_run", lambda cmd, timeout=5.0: None)
    monkeypatch.setattr(collect, "HERMES_HOME", tmp_path / "no-hermes")
    monkeypatch.setattr(collect, "_get_json", lambda url, timeout: (None, None, "ConnectionRefusedError: test"))
    monkeypatch.setattr(scraper_targets, "LOCK_WAIT_S", 0.2)


@pytest.fixture
def scfg(cfg):
    d = cfg.scraper_data_dir
    (d / "latest").mkdir(parents=True)
    now = time.time()
    (d / "heartbeat").write_text(str(int(now - 5)))
    (d / "targets.toml").write_text(TOML)
    (d / "status.json").write_text(json.dumps({
        "at": now - 30, "targets_file": str(d / "targets.toml"),
        "targets": [
            {"name": "bb-saves", "site": "bookiebashing", "url": "https://www.bookiebashing.net/tools/betbuilder/",
             "state": "running", "mode": "keep", "detail": XSS, "last_load": now - 120, "last_parse": now - 60,
             "rows_posted": 12},
            {"name": "gone", "site": "generic", "url": "https://x.example/", "state": "retired", "mode": "fetch",
             "detail": "", "last_load": None, "last_parse": None, "rows_posted": 0},
        ],
        "sites": [{"site": "generic", "loads_hour": 3, "per_hour": 60, "loads_day": 20, "per_day": 400,
                   "cooloff": "403", "cooloff_left_h": 20.5, "needs_resume": True}]}))
    (d / "latest" / "bb-saves.json").write_text(json.dumps({"at": now - 60, "url": "u", "rows": [{"a": 1}] * 3}))
    return cfg


def client(cfg, token: str = TOKEN) -> TestClient:
    return TestClient(create_app(cfg.with_overrides(web_token=token), allowed_hosts=["testserver"]))


def toml(cfg) -> str:
    return (cfg.scraper_data_dir / "targets.toml").read_text()


def log(cfg) -> list[str]:
    p = cfg.data_dir / actions.ACTIONS_LOG
    return p.read_text().splitlines() if p.exists() else []


# ---- the tab ----------------------------------------------------------------------------------

def test_the_tab_is_the_library_summary(scfg):
    d = client(scfg).get("/api/scraper").json()
    c = d["config"]
    assert c["error"] is None and c["exists"] is True and c["file"].endswith("targets.toml")
    assert c["sites"] == ["paddypower", "bookiebashing", "generic"]
    bb, tip = c["targets"]
    assert (bb["name"], bb["site"], bb["mode"], bb["enabled"], bb["every_min"], bb["stale_min"]) == \
        ("bb-saves", "bookiebashing", "keep", True, 10.0, 20.0)
    assert bb["parser"] == "bb_saves" and bb["post"] == "/api/paddy/saves-fair" and bb["priority"] == 0
    v = bb["view"]
    assert v["state"] == "running" and v["detail"] == XSS and v["rows_last_parsed"] == 3 and v["rows_posted"] == 12
    assert v["last_ok"].endswith("+00:00") and v["next_due"].endswith("+00:00")
    assert tip["enabled"] is False and tip["mode"] == "fetch" and tip["status"] is None
    assert tip["view"]["next_due"] is None and tip["trust_weight"] == 0.5
    # what the Scraper still reports but the file no longer has stays in `targets` (status.json)
    assert [t["name"] for t in d["targets"]] == ["bb-saves", "gone"]
    s = d["sites"][0]
    assert s["needs_resume"] is True and s["loads_hour"] == 3 and s["per_hour"] == 60
    assert s["resume_command"].endswith("-m scraper resume generic")
    assert f"SCRAPER_DATA_DIR={scfg.scraper_data_dir}" in s["resume_command"]


def test_empty_state_without_a_targets_file(cfg):
    c = client(cfg).get("/api/scraper").json()["config"]
    assert c["exists"] is False and c["targets"] == [] and c["error"] is None
    cfg.scraper_data_dir.mkdir(parents=True)
    c = client(cfg).get("/api/scraper").json()["config"]
    assert c["exists"] is False and c["targets"] == []
    # the add form works on an empty dir: the library creates the file
    r = client(cfg).post("/api/actions/scraper/targets/add", headers=H, json={
        "name": "first", "site": "generic", "url": "https://tips.example/a", "enabled": False})
    assert r.status_code == 200, r.text
    assert [t["name"] for t in client(cfg).get("/api/scraper").json()["config"]["targets"]] == ["first"]


def test_a_file_that_does_not_load_is_shown_not_raised(scfg):
    (scfg.scraper_data_dir / "targets.toml").write_text('[[target]]\nname = "x"\nsite = "nope"\nurl = "https://a.b/"\n')
    r = client(scfg).get("/api/scraper")
    assert r.status_code == 200
    c = r.json()["config"]
    assert c["targets"] == [] and "unknown site 'nope'" in c["error"]


# ---- the token ---------------------------------------------------------------------------------

WRITES = [("add", {"name": "n", "site": "generic", "url": "https://tips.example/n"}),
          ("bb-saves/set", {"every_min": 15}), ("bb-saves/enable", {}), ("bb-saves/disable", {}),
          ("bb-saves/remove", {"confirm": "bb-saves"})]


@pytest.mark.parametrize("path,body", WRITES)
def test_every_write_needs_the_token(scfg, path, body):
    before = toml(scfg)
    assert client(scfg, "").post(f"/api/actions/scraper/targets/{path}", json=body).status_code == 403
    c = client(scfg)
    assert c.post(f"/api/actions/scraper/targets/{path}", json=body).status_code == 403
    assert c.post(f"/api/actions/scraper/targets/{path}", json=body, headers={"X-Puntmore-Token": "x"}).status_code == 403
    assert toml(scfg) == before
    lines = log(scfg)
    assert len(lines) == 3 and all("\tscraper targets " in ln and "\trefused" in ln for ln in lines)


# ---- each write ----------------------------------------------------------------------------------

def test_add(scfg):
    c = client(scfg)
    r = c.post("/api/actions/scraper/targets/add", headers=H, json={
        "name": "racing", "site": "generic", "url": "https://tips.example/racing", "parser": "racing_tips",
        "post": "hub:tips", "every_min": 120, "min_rows": 2, "trust_weight": 0.5, "window": "07:00-23:00",
        "priority": 1, "wait_for": "", "mode": "", "enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and "within 60 s" in r.json()["message"]
    names = [t["name"] for t in c.get("/api/scraper").json()["config"]["targets"]]
    assert names == ["bb-saves", "tipster one", "racing"]           # re-read at once: the cache was dropped
    assert 'name = "racing"' in toml(scfg) and 'window = "07:00-23:00"' in toml(scfg)
    assert any("\tscraper targets add racing\tok: added racing (disabled)" in ln for ln in log(scfg))
    r = c.post("/api/actions/scraper/targets/add", headers=H, json={
        "name": "racing", "site": "generic", "url": "https://tips.example/racing"})
    assert r.status_code == 409 and r.json()["field"] == "name" and "already exists" in r.json()["message"]


def test_set_enable_disable(scfg):
    c = client(scfg)
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H,
               json={"every_min": 15, "priority": 2, "window": "07:00-23:30"})
    assert r.status_code == 200, r.text
    t = r.json()["target"]
    assert (t["every_min"], t["priority"], t["window"]) == (15.0, 2, "07:00-23:30")
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"window": ""})   # blank clears
    assert r.status_code == 200 and r.json()["target"]["window"] is None
    assert c.post("/api/actions/scraper/targets/tipster%20one/enable", headers=H).status_code == 200
    assert c.post("/api/actions/scraper/targets/bb-saves/disable", headers=H).status_code == 200
    ts = {t["name"]: t["enabled"] for t in c.get("/api/scraper").json()["config"]["targets"]}
    assert ts == {"bb-saves": False, "tipster one": True}
    assert c.post("/api/actions/scraper/targets/nope/enable", headers=H).status_code == 404
    # the console edits only its fields; the rest is remove + add (or the CLI)
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"site": "generic"})
    assert r.status_code == 400 and r.json()["field"] == "site"
    assert c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={}).status_code == 400
    lines = log(scfg)
    assert any("scraper targets set bb-saves every_min=15 priority=2 window=07:00-23:30\tok:" in ln for ln in lines)
    assert any("scraper targets enable tipster one\tok:" in ln for ln in lines)


def test_a_frequency_below_the_floor_is_the_librarys_refusal_and_writes_nothing(scfg):
    before = toml(scfg)
    lib = scraper_targets.library(scfg)
    with pytest.raises(lib.TargetsError) as want:
        lib.validate({"name": "bb-saves", "site": "bookiebashing", "url": "https://www.bookiebashing.net/x",
                      "every_min": 2})
    r = client(scfg).post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"every_min": 2})
    assert r.status_code == 400
    assert r.json() == {"ok": False, "message": str(want.value), "field": "every_min"}
    assert "every_min = 2 is outside what the Scraper allows" in r.json()["message"]
    assert toml(scfg) == before                                     # nothing written
    r = client(scfg).post("/api/actions/scraper/targets/add", headers=H, json={
        "name": "fast", "site": "generic", "url": "https://tips.example/f", "every_min": 1})
    assert r.status_code == 400 and r.json()["field"] == "every_min"
    r = client(scfg).post("/api/actions/scraper/targets/add", headers=H, json={
        "name": "pp", "site": "generic", "url": "https://www.paddypower.com/football"})
    assert r.status_code == 400 and r.json()["field"] == "url" and "allowed host" in r.json()["message"]
    assert toml(scfg) == before
    assert any("scraper targets set bb-saves every_min=2\trefused: target bb-saves: every_min" in ln for ln in log(scfg))


def test_the_lock_held_elsewhere_is_a_409_not_a_500(scfg):
    lib = scraper_targets.library(scfg)
    before = toml(scfg)
    held, release = threading.Event(), threading.Event()

    def hold():                        # HubAI or the CLI, mid-change
        with lib.locked(scfg.scraper_targets_file):
            held.set()
            release.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        r = client(scfg).post("/api/actions/scraper/targets/bb-saves/disable", headers=H)
        assert r.status_code == 409
        assert "being changed by something else" in r.json()["message"] and r.json()["retry"] is True
        r = client(scfg).post("/api/actions/scraper/targets/add", headers=H,
                              json={"name": "n", "site": "generic", "url": "https://tips.example/n"})
        assert r.status_code == 409
    finally:
        release.set()
        t.join()
    assert toml(scfg) == before
    assert client(scfg).post("/api/actions/scraper/targets/bb-saves/disable", headers=H).status_code == 200


def test_remove_needs_the_name_typed(scfg):
    c = client(scfg)
    before = toml(scfg)
    for body in ({}, {"confirm": "yes"}, {"confirm": "BB-SAVES"}):
        r = c.post("/api/actions/scraper/targets/bb-saves/remove", headers=H, json=body)
        assert r.status_code == 400 and r.json()["field"] == "confirm"
    assert toml(scfg) == before
    r = c.post("/api/actions/scraper/targets/bb-saves/remove", headers=H, json={"confirm": "bb-saves"})
    assert r.status_code == 200 and r.json()["targets"] == ["tipster one"]
    assert [t["name"] for t in c.get("/api/scraper").json()["config"]["targets"]] == ["tipster one"]
    assert c.post("/api/actions/scraper/targets/bb-saves/remove", headers=H,
                  json={"confirm": "bb-saves"}).status_code == 404


def test_field_of():
    f = scraper_targets.field_of
    assert f("target x: every_min = 2 is outside what the Scraper allows") == "every_min"
    assert f("target x: window must be \"HH:MM-HH:MM\" (24 h, local time), not 'x'") == "window"
    assert f("target x: priority must be a whole number, not 2.5") == "priority"
    assert f("target x: https://www.paddypower.com/ is not on an allowed host for site 'generic'") == "url"
    assert f("target x: unknown site 'nope' (have paddypower, bookiebashing, generic)") == "site"
    assert f("url is required") == "url"
    assert f("target x: matches.max_tabs = 9 is outside") == "matches"
    assert f("target names must be unique") is None


def test_the_page_has_the_tab_and_no_inline_script(scfg):
    page = client(scfg).get("/").text
    for needle in ('id="target-add-form"', 'id="target-edit-form"', 'id="target-remove-form"',
                   '<option selected>generic</option>', "<option>paddypower</option>", "<option>bookiebashing</option>",
                   "the Scraper applies changes within 60 s"):
        assert needle in page
    assert page.count("<script") == 1 and "onclick=" not in page and "Worker" not in page
    css = client(scfg).get("/static/style.css").text
    assert "@media (max-width: 700px)" in css and "table.cards" in css
    assert page.count('name="until" type="date"') == 2
    for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        assert page.count(f'name="days" value="{day}"') == 2
    js = client(scfg).get("/static/app.js").text
    assert "/api/scraper/log/" in js and 'finished: "off"' in js
    assert '"all lines"' in js and '"fewer lines"' in js        # the full-log toggle
    assert ".job" in client(scfg).get("/static/style.css").text


def test_days_and_until_through_the_console(scfg):
    c = client(scfg)
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H,
               json={"days": ["fri", "sat"], "until": "2026-11-30"})
    assert r.status_code == 200, r.text
    assert (r.json()["target"]["days"], r.json()["target"]["until"]) == (["fri", "sat"], "2026-11-30")
    v = {t["name"]: t for t in c.get("/api/scraper").json()["config"]["targets"]}["bb-saves"]["view"]
    assert v["schedule"] == "every 10 min · Fri, Sat · all day · until 30 Nov 2026"
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"days": ["funday"]})
    assert r.status_code == 400 and r.json()["field"] == "days"
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"until": "next week"})
    assert r.status_code == 400 and r.json()["field"] == "until"
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"days": None})
    assert r.status_code == 200 and r.json()["target"]["days"] == []
    r = c.post("/api/actions/scraper/targets/add", headers=H, json={
        "name": "weekend", "site": "generic", "url": "https://tips.example/w", "days": ["sat"], "enabled": False})
    assert r.status_code == 200 and r.json()["target"]["days"] == ["sat"]


def test_the_log_route_and_the_tile_tail(scfg):
    jl = scraper_targets.joblog(scfg)
    for i in range(8):
        jl.write(scfg.scraper_data_dir, "bb-saves", f"line {i}", now=1_800_000_000.0 + i)
    c = client(scfg)
    v = {t["name"]: t for t in c.get("/api/scraper").json()["config"]["targets"]}["bb-saves"]["view"]
    assert [x["msg"] for x in v["log"]] == [f"line {i}" for i in range(3, 8)]
    assert v["log"][0]["at"].endswith("+00:00")
    d = c.get("/api/scraper/log/bb-saves?n=3").json()
    assert d["target"] == "bb-saves" and [x["msg"] for x in d["lines"]] == ["line 5", "line 6", "line 7"]
    assert c.get("/api/scraper/log/bb-saves?n=0").json()["n"] == 1
    assert c.get("/api/scraper/log/bb-saves?n=99999").json()["n"] == 1000
    assert c.get("/api/scraper/log/nobody").json()["lines"] == []
    (scfg.scraper_data_dir / "secret.log").write_text('{"ts": 1, "msg": "outside logs/"}\n')
    assert scraper_targets.log(scfg, "../secret", 5)["lines"] == []        # never read outside logs/
    r = c.post("/api/actions/scraper/targets/bb-saves/remove", headers=H, json={"confirm": "bb-saves"})
    assert r.status_code == 200
    assert not (scfg.scraper_data_dir / "logs" / "bb-saves.log").exists()


@pytest.mark.parametrize("name", ["../secret", "../../secret", "..", ".", "", "/secret", "a/b", "a b", "../secret.log"])
def test_the_log_route_never_reads_outside_logs(scfg, name):
    # a log file at every place a hostile name could reach: <data>/, its parent, logs/../ and a sub-dir
    line = '{"ts": 1, "msg": "outside logs/"}\n'
    for p in (scfg.scraper_data_dir / "secret.log", scfg.scraper_data_dir.parent / "secret.log",
              scfg.scraper_data_dir / "..log", scfg.scraper_data_dir / "a" / "b.log",
              scfg.scraper_data_dir / "logs" / "a" / "b.log"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(line)
    assert scraper_targets.log(scfg, name, 50)["lines"] == []
    # through the route: a "/" or a dot segment never reaches {target} (404, or another route), and
    # whatever does reach it reads nothing outside logs/
    r = client(scfg).get("/api/scraper/log/" + urllib.parse.quote(name, safe=""))
    assert r.status_code in (200, 404) and "outside logs/" not in r.text


def test_a_log_that_fails_never_breaks_the_view(scfg, monkeypatch):
    jl = scraper_targets.joblog(scfg)

    def boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(jl, "read", boom)
    rows = client(scfg).get("/api/scraper").json()["config"]["targets"]
    assert [t["view"]["log"] for t in rows] == [[], []]


def test_the_log_route_without_the_library_is_a_503(scfg, monkeypatch):
    def missing(cfg):
        raise scraper_targets.LibraryMissing("the Scraper's code is not at /nowhere")

    monkeypatch.setattr(scraper_targets, "joblog", missing)
    assert client(scfg).get("/api/scraper/log/bb-saves").status_code == 503


def test_remove_takes_the_log_from_the_scraper_data_dir(scfg, monkeypatch):
    lib = scraper_targets.library(scfg)
    seen = {}
    real = lib.remove

    def spy(path, name, timeout=None, data_dir=None):
        seen["data_dir"] = data_dir
        return real(path, name, timeout=timeout, data_dir=data_dir)

    monkeypatch.setattr(lib, "remove", spy)
    r = client(scfg).post("/api/actions/scraper/targets/bb-saves/remove", headers=H, json={"confirm": "bb-saves"})
    assert r.status_code == 200 and seen["data_dir"] == scfg.scraper_data_dir
