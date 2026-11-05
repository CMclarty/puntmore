"""The scraper's control plane (routers/scraper.py): the box reads its config
and reports status with the ingest key; the page edits with a session and the
`scraper` grant; timings are clamped to the scraper's own floors; the config
is in the shape the scraper's config._target reads."""
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.security import user_permissions

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(bind=engine)
KEY = "test-ingest-key"
H = {"X-API-Key": KEY}


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(settings, "saves_ingest_key", KEY)
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


def _scraper_config():
    """The scraper's own config module (stdlib only), imported from the repo."""
    root = str(pathlib.Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from scraper import config
    return config


# --- guards ------------------------------------------------------------------

def test_worker_routes_need_the_key_not_a_session(client):
    assert client.get("/scraper/config").status_code == 401
    assert client.get("/scraper/config", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/scraper/status", json={"targets": [], "sites": []}).status_code == 401
    assert client.get("/scraper/config", headers=H).status_code == 200


def test_worker_routes_503_when_no_key_is_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "saves_ingest_key", "")
    assert client.get("/scraper/config", headers=H).status_code == 503


def test_page_routes_need_the_scraper_grant(client):
    app.dependency_overrides[user_permissions] = lambda: "coupons+shop"
    try:
        assert client.get("/scraper").status_code == 403
        assert client.put("/scraper/timings/paddypower", json={"stale_min": 30}).status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
    app.dependency_overrides[user_permissions] = lambda: "scraper"
    try:
        assert client.get("/scraper").status_code == 200
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


def test_page_routes_401_without_a_session(client):
    from app.security import require_user
    app.dependency_overrides.pop(user_permissions, None)
    app.dependency_overrides.pop(require_user, None)
    assert client.get("/scraper").status_code == 401
    # ...and the key is not a way in to the page's routes. Since API clients
    # (Oct 2026) the ingest key is a real identity holding only the `ingest`
    # grant, so it is refused as unauthorised for the page: 403, not 401.
    assert client.get("/scraper", headers=H).status_code == 403


# --- config ------------------------------------------------------------------

def test_seeded_config_is_epl_most_and_the_bb_builder(client):
    cfg = client.get("/scraper/config", headers=H).json()
    by = {t["name"]: t for t in cfg["targets"]}
    assert set(by) == {"pp-epl-most", "bb-saves"}
    t, bb = by["pp-epl-most"], by["bb-saves"]
    # The Bookie Bashing bet builder is seeded as the saves page (its driver
    # is scraper/parsers/bb_saves.py), and it is never reloaded on a timer.
    assert bb["name"] == "bb-saves" and bb["url"].endswith("/tools/betbuilder/")
    assert bb["every_min"] is None and "matches" not in bb
    assert t["site"] == "paddypower"
    assert t["url"] == "https://www.paddypower.com/football/english-premier-league"
    assert t["parser"] == "pp_most" and t["post"] == "/api/paddy/most-prices"
    assert t["matches"]["market_tabs"] == ["Corners", "Cards"]
    assert set(cfg["timings"]) == {"paddypower", "bookiebashing"}

    url = "https://www.bookiebashing.net/saves-tool"
    client.put("/scraper/markets/bb_saves", json={"url": url})
    names = {t["name"]: t for t in client.get("/scraper/config", headers=H).json()["targets"]}
    assert names["bb-saves"]["url"] == url


def test_config_is_what_the_scrapers_target_reader_reads_unchanged(client):
    """Every served target goes through config._target, and _target changes
    nothing — the server's clamps are at least as strict as the scraper's."""
    config = _scraper_config()
    client.put("/scraper/markets/bb_saves", json={"url": "https://www.bookiebashing.net/x"})
    # Push every timing below the floors (and max_tabs above its ceiling).
    for site in ("paddypower", "bookiebashing"):
        client.put(f"/scraper/timings/{site}", json={
            "every_min": 1, "stale_min": 1, "max_tabs": 50, "open_gap_min": 0,
            "relist_min": 1, "tab_max_hours": 100})
    served = client.get("/scraper/config", headers=H).json()["targets"]
    assert len(served) == 2
    for raw in served:
        t = config._target(raw)
        assert (t.name, t.site, t.url, t.parser, t.post) == (
            raw["name"], raw["site"], raw["url"], raw["parser"], raw["post"])
        assert t.every_min == raw["every_min"] and t.stale_min == raw["stale_min"]
        if raw.get("matches"):
            m, rm = t.matches, raw["matches"]
            assert (m.link_re, m.max_tabs, list(m.market_tabs), m.open_gap_min,
                    m.tab_max_hours, m.relist_min) == (
                rm["link_re"], rm["max_tabs"], rm["market_tabs"], rm["open_gap_min"],
                rm["tab_max_hours"], rm["relist_min"])


def test_disabled_league_or_market_drops_its_targets(client):
    league = client.get("/scraper").json()["leagues"][0]
    client.put(f"/scraper/leagues/{league['id']}", json={"enabled": False})
    names = [t["name"] for t in client.get("/scraper/config", headers=H).json()["targets"]]
    assert names == ["bb-saves"]                 # a page target has no league
    client.put(f"/scraper/leagues/{league['id']}", json={"enabled": True})
    client.put("/scraper/markets/most", json={"enabled": False})
    names = [t["name"] for t in client.get("/scraper/config", headers=H).json()["targets"]]
    assert names == ["bb-saves"]
    client.put("/scraper/markets/bb_saves", json={"enabled": False})
    assert client.get("/scraper/config", headers=H).json()["targets"] == []


# --- edits ---------------------------------------------------------------------

def test_timings_are_clamped_to_the_scrapers_floors(client):
    body = client.put("/scraper/timings/paddypower", json={
        "every_min": 1, "stale_min": 2, "max_tabs": 9, "open_gap_min": 0.5,
        "relist_min": 5, "tab_max_hours": 99}).json()
    (pp,) = [t for t in body["timings"] if t["site"] == "paddypower"]
    assert pp == {"site": "paddypower", "every_min": 5.0, "stale_min": 10.0, "max_tabs": 6,
                  "open_gap_min": 2.0, "relist_min": 30.0, "tab_max_hours": 24.0,
                  "listings": True}
    # every_min may be blanked (reload only when stale); the others may not.
    body = client.put("/scraper/timings/paddypower",
                      json={"every_min": None, "stale_min": None}).json()
    (pp,) = [t for t in body["timings"] if t["site"] == "paddypower"]
    assert pp["every_min"] is None and pp["stale_min"] == 10.0
    assert client.put("/scraper/timings/betfair", json={"stale_min": 30}).status_code == 404


def test_urls_must_be_on_the_sites_own_host(client):
    lid = client.get("/scraper").json()["leagues"][0]["id"]
    assert client.put(f"/scraper/leagues/{lid}",
                      json={"url": "https://evil.example/football"}).status_code == 422
    assert client.put(f"/scraper/leagues/{lid}",
                      json={"url": "http://www.paddypower.com/football/x"}).status_code == 422
    ok = client.put(f"/scraper/leagues/{lid}",
                    json={"url": "https://www.paddypower.com/football/spanish-la-liga"})
    assert ok.status_code == 200
    assert client.put("/scraper/markets/bb_saves",
                      json={"url": "https://www.paddypower.com/x"}).status_code == 422
    # A league market has no url of its own.
    assert client.put("/scraper/markets/most",
                      json={"url": "https://www.paddypower.com/x"}).status_code == 422


def test_a_league_can_be_added_and_removed(client):
    """Watching the EFL Cup is one row on the page; the box gets a new target
    on its next poll, named from the league."""
    r = client.post("/scraper/leagues", json={"name": "EFL Cup",
                                              "url": "https://www.paddypower.com/football/english-league-cup"})
    assert r.status_code == 201
    (lg,) = [x for x in r.json()["leagues"] if x["name"] == "EFL Cup"]
    assert lg["enabled"] and lg["site"] == "paddypower"
    names = {t["name"] for t in client.get("/scraper/config", headers=H).json()["targets"]}
    assert "pp-efl-cup-most" in names
    # Same name again is a conflict; a url off the site is refused.
    assert client.post("/scraper/leagues", json={"name": "efl cup", "url": lg["url"]}).status_code == 409
    assert client.post("/scraper/leagues", json={"name": "X", "url": "https://example.com/x"}).status_code == 422
    r = client.delete(f"/scraper/leagues/{lg['id']}")
    assert r.status_code == 200 and not [x for x in r.json()["leagues"] if x["name"] == "EFL Cup"]


def test_market_tabs_are_edited_and_trimmed(client):
    body = client.put("/scraper/markets/most", json={"tabs": [" Corners ", "", "Cards & Fouls"]})
    (most,) = [m for m in body.json()["markets"] if m["key"] == "most"]
    assert most["tabs"] == ["Corners", "Cards & Fouls"]
    cfg = client.get("/scraper/config", headers=H).json()
    (pp,) = [t for t in cfg["targets"] if t["name"] == "pp-epl-most"]
    assert pp["matches"]["market_tabs"] == ["Corners", "Cards & Fouls"]


# --- status --------------------------------------------------------------------

def _report(**over):
    return {
        "targets": [{"name": "pp-epl-most", "site": "paddypower",
                     "url": "https://www.paddypower.com/football/english-premier-league",
                     "state": "running", "detail": "3 tabs", "last_load": 1792341355.0,
                     "last_parse": None, "rows_posted": 0, **over}],
        "sites": [{"site": "paddypower", "loads_hour": 4, "per_hour": 15, "loads_day": 30,
                   "per_day": 120, "cooloff": None, "cooloff_left_h": None,
                   "needs_resume": False}],
    }


def test_status_is_stored_and_replaced_whole(client):
    assert client.post("/scraper/status", headers=H, json=_report()).status_code == 200
    st = client.get("/scraper").json()["status"]
    (t,) = st["targets"]
    assert t["state"] == "running" and t["last_load"].startswith("2026-10-18T")
    assert st["sites"][0]["loads_hour"] == 4 and st["reported_at"]

    client.post("/scraper/status", headers=H, json={"targets": [], "sites": []})
    st = client.get("/scraper").json()["status"]
    assert st["targets"] == [] and st["sites"] == []


def test_status_state_must_be_one_of_four(client):
    r = client.post("/scraper/status", headers=H, json=_report(state="exploded"))
    assert r.status_code == 422
