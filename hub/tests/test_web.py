"""The Hub Console: collectors against a fixture Scraper data dir, routes, and the action guards.

The fixture files are in the Scraper's real shapes (scraper/runner.py): `heartbeat` is epoch seconds
as text, `status.json` is `Runner.status()`, `latest/<target>.json` is `{at, url, rows}`, `alerts.log`
is `scraper/alerts.py` lines, the hub drops are `_drop()`'s `<ms>-<name>.json`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from puntmore_hub import killswitch  # noqa: E402
from puntmore_hub.store import Store  # noqa: E402
from puntmore_hub.web import actions, collect  # noqa: E402
from puntmore_hub.web.app import STATIC, create_app  # noqa: E402

XSS = '<script>alert("x")</script><img src=x onerror=alert(1)>'
TOKEN = "s3cret-token"


def no_systemd(cmd, timeout=5.0):
    return None


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """No test touches the real systemd, journal, ~/.hermes, llama-server or Render."""
    monkeypatch.setattr(collect, "_run", no_systemd)
    monkeypatch.setattr(collect, "HERMES_HOME", tmp_path / "no-hermes")
    monkeypatch.setattr(collect, "_get_json", lambda url, timeout: (None, None, "ConnectionRefusedError: test"))


def write_scraper(d: Path, *, heartbeat_age: float = 5.0, detail: str = "1 tab open") -> None:
    now = time.time()
    (d / "latest").mkdir(parents=True)
    (d / "hub" / "tips").mkdir(parents=True)
    (d / "hub" / "events").mkdir(parents=True)
    (d / "heartbeat").write_text(str(int(now - heartbeat_age)))
    status = {"at": now - 30, "source": "toml",
              "targets": [
                  {"name": "bb-saves", "site": "bookiebashing", "url": "https://www.bookiebashing.net/saves",
                   "state": "running", "mode": "page", "detail": detail, "last_load": now - 600,
                   "last_parse": now - 60, "rows_posted": 12},
                  {"name": "tipster one", "site": "generic", "url": "https://tips.example/today",
                   "state": "blocked", "mode": "fetch", "detail": "cooling off: 403", "last_load": now - 900,
                   "last_parse": None, "rows_posted": 0},
              ],
              "sites": [{"site": "generic", "loads_hour": 3, "per_hour": 60, "loads_day": 20, "per_day": 400,
                         "cooloff": "403", "cooloff_left_h": 20.5, "needs_resume": True}]}
    (d / "status.json").write_text(json.dumps(status, indent=1))
    (d / "latest" / "bb-saves.json").write_text(json.dumps(
        {"at": now - 60, "url": "https://www.bookiebashing.net/saves",
         "rows": [{"event": XSS, "market": "Saves", "price": 2.5}, {"event": "B v C", "market": "Saves", "price": 3.1}]},
        indent=1))
    (d / "alerts.log").write_text("".join(f"2026-11-05 09:{i % 60:02d}  line {i}\n" for i in range(150))
                                  + f"2026-11-05 10:00  {XSS}\n")
    (d / "hub" / "tips" / f"{int(now * 1000)}-tipster_one.json").write_text(json.dumps(
        {"source": "tipster one", "at": now, "url": "https://tips.example/today", "channel": "tips",
         "trust_weight": 1.0, "rows": [{"event": "Arsenal v Spurs", "market": "Match Odds", "selection": XSS,
                                        "advised_price": 2.1}]}))


@pytest.fixture
def wcfg(cfg):
    write_scraper(cfg.scraper_data_dir)
    Store(cfg.db_path).close()          # `puntmore init`
    return cfg


def client(cfg, token: str = "") -> TestClient:
    return TestClient(create_app(cfg.with_overrides(web_token=token), allowed_hosts=["testserver"]))


# ---- collectors -------------------------------------------------------------------------------

def test_scraper_collector_reads_the_real_shapes(wcfg):
    w = collect.scraper(wcfg)
    assert w["state"] == "warn"                       # one target blocked
    assert "blocked: tipster one" in w["detail"]
    assert 0 <= w["heartbeat_age_s"] < 60
    names = [t["name"] for t in w["targets"]]
    assert names == ["bb-saves", "tipster one"]
    bb = w["targets"][0]
    assert bb["rows_last_parsed"] == 2 and bb["rows_posted"] == 12 and bb["last_ok"].endswith("+00:00")
    assert w["targets"][1]["rows_last_parsed"] is None and w["targets"][1]["last_ok"] is None
    assert len(w["alerts"]) == 100 and w["alerts"][-1].endswith(XSS)
    assert w["sites"][0]["needs_resume"] is True
    lr = collect.latest_rows(wcfg, "bb-saves")
    assert lr["count"] == 2 and lr["rows"][0]["event"] == XSS


def test_scraper_states(cfg):
    assert collect.scraper(cfg)["state"] == "unknown"   # no data dir at all
    write_scraper(cfg.scraper_data_dir, heartbeat_age=3600)
    assert collect.scraper(cfg)["state"] == "down"
    (cfg.scraper_data_dir / "heartbeat").write_text(str(int(time.time())))
    st = json.loads((cfg.scraper_data_dir / "status.json").read_text())
    st["targets"] = st["targets"][:1]
    (cfg.scraper_data_dir / "status.json").write_text(json.dumps(st))
    assert collect.scraper(cfg)["state"] == "up"


def test_latest_rows_cannot_leave_latest(wcfg):
    (wcfg.scraper_data_dir / "secret.json").write_text('{"rows": [1]}')
    assert collect.latest_rows(wcfg, "../secret") is None
    assert collect.latest_rows(wcfg, "/etc/passwd") is None


def test_daemon_collector(wcfg):
    from puntmore_hub import daemon
    st = Store(wcfg.db_path)
    out = collect.daemon(wcfg, st)
    assert out["state"] == "unknown" and out["inbox_drops"] == {"tips": 1, "events": 0, "prices": None}   # no systemctl
    assert out["last_inbox"] is None and "no inbox pass recorded" in out["detail"]
    daemon.record_inbox(wcfg, {"tip_files": 1, "events": 0})
    out = collect.daemon(wcfg, st, run=lambda c, timeout=5: (0, "active\n"))
    assert out["state"] == "up" and out["last_inbox"]["counts"] == {"tip_files": 1, "events": 0}
    assert out["last_mail"]["detail"]                                 # mail.status(): not configured
    assert collect.daemon(wcfg, st, run=lambda c, timeout=5: (3, "inactive\n"))["state"] == "down"
    assert "outbox" not in out and "configured" not in out


def test_the_daemon_card_warns_while_prices_wait_for_render(wcfg):
    from puntmore_hub import daemon
    st = Store(wcfg.db_path)
    daemon.record_inbox(wcfg, {"tip_files": 0, "events": 0, "price_files": 0, "price_rows": 0,
                               "price_dropped": 0, "price_waiting": 3})
    out = collect.daemon(wcfg, st, run=lambda c, timeout=5: (0, "active\n"))
    assert out["state"] == "warn" and "3 price drop(s) waiting to go to Render" in out["detail"]


def test_placement_and_escalation_collectors(cfg):
    st = Store(cfg.db_path)
    st.upsert_bet("b1", "acc1", {"stake": 5.0, "expected_price": 2.0}, "placed")
    st.upsert_bet("b2", "acc1", {"stake": 3.0, "expected_price": 4.0}, "failed")
    st.add_balance("acc1", 100.0)
    p = collect.placement(cfg, st)
    assert p["dry_run"] is True and p["kill_switch"] is False
    assert p["state"] == "warn" and "1 failed" in p["detail"]
    assert {b["bet_id"] for b in p["bets"]} == {"b1", "b2"}
    assert p["accounts"] == [{"account": "acc1", "open_exposure": 5.0, "balance": 100.0}]
    killswitch.turn_on(st, cfg, by="test")
    assert "KILL SWITCH ON" in collect.placement(cfg, st)["detail"]
    e = collect.escalations(cfg, st)
    assert e["state"] == "warn" and e["open"][0]["kind"] == "kill_switch"
    st.resolve_escalation(e["open"][0]["id"], "done")
    e = collect.escalations(cfg, st)
    assert e["state"] == "up" and e["resolved"][0]["outcome"] == "done"


def test_tips_collector(wcfg):
    st = Store(wcfg.db_path)
    assert collect.tips(wcfg, st)["state"] == "off"
    from puntmore_hub import inbox
    inbox.ingest(st, wcfg, notify=False)
    t = collect.tips(wcfg, st)
    assert t["state"] == "up" and t["tips"][0]["selection"] == XSS
    assert t["sources"][0]["source"] == "tipster one" and t["sources"][0]["settled"] == 0


def test_model_collector(cfg, monkeypatch):
    assert collect.model(cfg)["state"] == "off"
    answers = {
        "/health": (200, {"status": "ok"}, None),
        "/props": (200, {"model_alias": "qwen", "model_path": "/m/q.gguf", "total_slots": 2,
                         "default_generation_settings": {"n_ctx": 131072}}, None),
        "/slots": (501, {"error": {"message": "This server does not support slots endpoint."}}, None),
    }
    monkeypatch.setattr(collect, "_get_json", lambda url, timeout: answers[url[url.rindex("/"):]])
    m = collect.model(cfg)
    assert m["state"] == "up" and m["props"]["model_alias"] == "qwen" and m["props"]["n_ctx"] == 131072
    assert m["slots"] == {"unavailable": "HTTP 501"}
    answers["/slots"] = (200, [{"id": 0, "n_ctx": 131072, "is_processing": True}, {"id": 1, "state": 0}], None)
    m = collect.model(cfg)
    assert [s["busy"] for s in m["slots"]] == [True, False] and "1/2 slots busy" in m["detail"]


def test_hermes_collector(cfg, tmp_path):
    h = collect.hermes(cfg)
    assert h["state"] == "unknown" and h["journal"] is None and h["profiles"] == []
    home = tmp_path / "hermes"
    (home / "profiles" / "scraper-manager").mkdir(parents=True)
    (home / ".env").write_text("TELEGRAM_TOKEN=do-not-read\n")

    def fake(cmd, timeout=5.0):
        return (0, "active\n") if cmd[0] == "systemctl" else (0, f"started\n{XSS}\n")
    h = collect.hermes(cfg, run=fake, home=home)
    assert h["state"] == "up" and h["profiles"] == ["scraper-manager"] and h["journal"][-1] == XSS
    assert "do-not-read" not in json.dumps(h)


def test_render_collector(cfg, monkeypatch):
    assert collect.render(cfg)["state"] == "off"
    c = cfg.with_overrides(render_http="https://example.invalid")
    assert collect.render(c)["state"] == "down"
    monkeypatch.setattr(collect, "_get_json", lambda url, timeout: (200, {"ok": True, "db": "ok"}, None))
    r = collect.render(c)
    assert r["state"] == "up" and r["ready"]["body"] == {"ok": True, "db": "ok"}


# ---- routes -----------------------------------------------------------------------------------

def test_every_route_answers_with_nothing_present(cfg):
    c = client(cfg)
    ov = c.get("/api/overview")
    assert ov.status_code == 200
    comps = ov.json()["components"]
    assert set(comps) == set(collect.COMPONENTS)
    for name, comp in comps.items():
        assert comp["state"] in ("unknown", "off", "up"), (name, comp)
        assert set(comp) == {"state", "checked_at", "detail"}
    assert comps["scraper"]["state"] == "unknown" and comps["model"]["state"] == "off"
    assert ov.json()["actions_enabled"] is False
    for name in collect.COMPONENTS:
        r = c.get(f"/api/{name}")
        assert r.status_code == 200, name
        assert {"state", "checked_at", "detail"} <= set(r.json())
    assert c.get("/api/scraper/latest/nope").status_code == 404
    assert not cfg.db_path.exists()                     # reads never create the hub database
    assert comps["daemon"]["state"] == "unknown"


def test_tab_routes_with_the_fixture(wcfg):
    c = client(wcfg)
    w = c.get("/api/scraper").json()
    assert {"heartbeat_age_s", "targets", "alerts", "sites"} <= set(w)
    assert {"service", "last_inbox", "last_mail", "inbox_drops"} <= set(c.get("/api/daemon").json())
    assert {"dry_run", "kill_switch", "bets", "accounts"} <= set(c.get("/api/placement").json())
    assert {"open", "resolved"} <= set(c.get("/api/escalations").json())
    assert {"tips", "sources"} <= set(c.get("/api/tips").json())
    assert {"health", "props", "slots"} <= set(c.get("/api/model").json())
    assert {"service", "journal", "profiles"} <= set(c.get("/api/hermes").json())
    assert {"ready"} <= set(c.get("/api/render").json()) and "last_link_check" not in c.get("/api/render").json()
    lr = c.get("/api/scraper/latest/bb-saves")
    assert lr.status_code == 200 and lr.headers["content-type"].startswith("application/json")
    assert lr.json()["rows"][0]["event"] == XSS        # untrusted text comes back as JSON data


def test_untrusted_text_is_never_rendered_as_html(wcfg):
    c = client(wcfg)
    page = c.get("/")
    assert page.status_code == 200 and "<title>Hub Console</title>" in page.text
    assert "<script>alert" not in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    # the page has no inline script at all, only the one shipped file
    assert page.text.count("<script") == 1 and 'src="/static/app.js"' in page.text
    js = c.get("/static/app.js")
    assert js.status_code == 200
    assert "innerHTML" not in js.text.replace("never innerHTML", "")
    assert "insertAdjacentHTML" not in js.text and "document.write" not in js.text
    assert c.get("/static/style.css").status_code == 200
    raw = c.get("/api/scraper").text
    assert json.loads(raw)["alerts"][-1].endswith(XSS)


def test_unknown_host_is_refused(cfg):
    c = TestClient(create_app(cfg, allowed_hosts=["127.0.0.1"]))
    assert c.get("/api/overview", headers={"host": "evil.example"}).status_code == 400


def test_static_files_are_shipped_in_the_package():
    assert {"index.html", "app.js", "style.css"} <= {p.name for p in STATIC.iterdir()}


# ---- actions ----------------------------------------------------------------------------------

ACTIONS = ["kill-on", "kill-off", "escalation-resolve", "inbox-now", "heartbeat-now"]


@pytest.mark.parametrize("path", ACTIONS)
def test_actions_refused_without_a_token(cfg, path):
    assert client(cfg).post(f"/api/actions/{path}", json={}).status_code == 403          # token blank: disabled
    c = client(cfg, TOKEN)
    assert c.post(f"/api/actions/{path}", json={}).status_code == 403                    # no header
    assert c.post(f"/api/actions/{path}", json={}, headers={"X-Puntmore-Token": "wrong"}).status_code == 403
    assert not cfg.kill_switch_file.exists()
    log = (cfg.data_dir / actions.ACTIONS_LOG).read_text()
    assert log.count("refused") == 3


def test_kill_on_and_off(cfg):
    c = client(cfg, TOKEN)
    h = {"X-Puntmore-Token": TOKEN}
    r = c.post("/api/actions/kill-on", headers=h)
    assert r.status_code == 200 and cfg.kill_switch_file.exists()
    assert c.get("/api/overview").json()["kill_switch"] is True
    assert c.post("/api/actions/kill-off", headers=h).status_code == 400
    assert c.post("/api/actions/kill-off", headers=h, json={"confirm": "off"}).status_code == 400
    assert cfg.kill_switch_file.exists()
    assert c.post("/api/actions/kill-off", headers=h, json={"confirm": "OFF"}).status_code == 200
    assert not cfg.kill_switch_file.exists()
    log = (cfg.data_dir / actions.ACTIONS_LOG).read_text().splitlines()
    assert any("\tkill on\tok" in line for line in log) and any("\tkill off\tok" in line for line in log)
    assert sum("kill off\trefused" in line for line in log) == 2
    st = Store(cfg.db_path)
    assert st.open_escalations()[0]["kind"] == "kill_switch"          # same as `puntmore kill on`


def test_escalation_resolve(cfg):
    st = Store(cfg.db_path)
    esc = st.add_escalation("other", "scraper", "something")
    c = client(cfg, TOKEN)
    h = {"X-Puntmore-Token": TOKEN}
    assert c.post("/api/actions/escalation-resolve", headers=h, json={"id": esc}).status_code == 400
    assert c.post("/api/actions/escalation-resolve", headers=h, json={"id": 999, "outcome": "x"}).status_code == 404
    r = c.post("/api/actions/escalation-resolve", headers=h, json={"id": esc, "outcome": "looked, fine"})
    assert r.status_code == 200
    assert st.escalation(esc)["outcome"] == "looked, fine"
    assert "Render" not in r.json()["message"]                        # nothing is queued anywhere
    assert c.post("/api/actions/escalation-resolve", headers=h, json={"id": esc, "outcome": "again"}).status_code == 409


def test_inbox_and_heartbeat(wcfg):
    c = client(wcfg, TOKEN)
    h = {"X-Puntmore-Token": TOKEN}
    r = c.post("/api/actions/inbox-now", headers=h)
    assert r.status_code == 200 and r.json()["counts"]["new_tips"] == 1
    r = c.post("/api/actions/heartbeat-now", headers=h)
    assert r.status_code == 200 and "heartbeat 24h" in r.json()["message"]
    assert c.post("/api/actions/link-check", headers=h).status_code in (404, 405)
