"""The served shell carries each tool's own manifest, icon and title, and the
server's identity table agrees with the frontend's (frontend/src/identity.js)."""
import re
from pathlib import Path

from app.identity import identity_for, render_index

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "frontend" / "index.html").read_text()
JS = (ROOT / "frontend" / "src" / "identity.js").read_text()


def test_each_tool_gets_its_own_manifest_icon_and_title():
    assert identity_for("/paddy") == {
        "title": "Puntmore — Paddy", "svg": "/icons/paddy.svg", "png": "/icons/paddy-180.png",
        "manifest": "/manifests/paddy.webmanifest",
    }
    assert identity_for("/")["manifest"] == "/manifests/coupons.webmanifest"
    assert identity_for("/virtuals")["manifest"] == "/manifests/virtuals.webmanifest"
    assert identity_for("/365/scorers")["png"] == "/icons/goalscorers-180.png"
    assert identity_for("/login") == {
        "title": "Puntmore", "svg": "/icon.svg", "png": "/icons/puntmore-180.png",
        "manifest": "/manifest.webmanifest",
    }


def test_the_shell_is_rewritten_for_the_path_served():
    html = render_index(INDEX, "/paddy")
    assert '<link rel="manifest" href="/manifests/paddy.webmanifest" />' in html
    assert 'href="/icons/paddy-180.png"' in html and 'href="/icons/paddy.svg"' in html
    assert '<meta name="apple-mobile-web-app-title" content="Puntmore — Paddy" />' in html
    assert "<title>Puntmore — Paddy</title>" in html
    assert "/manifest.webmanifest" not in html
    # The default path is left exactly as built.
    assert render_index(INDEX, "/login") == INDEX


def test_server_table_mirrors_the_frontends():
    from app import identity
    js_icons = set(re.findall(r"icon: '([\w-]+)'", JS))
    py_icons = ({icon for _, _, icon, _ in identity.IDENTITIES}
                | {d[1] for d in identity.DEFAULTS.values()})
    assert js_icons == py_icons
    for path in ("/", "/t/abc", "/upload", "/pnl/dogs", "/virtuals", "/kelly", "/aw26", "/shop",
                 "/365/scorers", "/365/acca", "/accabonus", "/paddy", "/paddy/acca", "/cards",
                 "/login", "/nope"):
        ident = identity_for(path)
        # Every file the server names exists in the built public set.
        for key in ("svg", "png", "manifest"):
            assert (ROOT / "frontend" / "public" / ident[key].lstrip("/")).is_file(), (path, ident[key])


def test_the_host_names_the_site(monkeypatch):
    from app.config import settings
    from app.site import site_for
    monkeypatch.setattr(settings, "me_host", "me.puntmore.com")
    assert site_for("puntmore.com") == site_for("www.puntmore.com") == "puntmore"
    assert site_for("me.puntmore.com") == site_for("ME.puntmore.com:443") == "me"
    assert site_for("me") == site_for(None) == site_for("notme.puntmore.com") == "puntmore"
    # Me is the one host ME_HOST names: another me.* host is the main site.
    assert site_for("me.localhost:5173") == "puntmore"
    monkeypatch.setattr(settings, "me_host", "me.localhost")
    assert site_for("me.localhost:5173") == "me"
    # Shalgo is retired: its host is the main site (asgi redirects it first).
    assert site_for("shalgo.puntmore.com") == "puntmore"


def test_with_me_host_blank_there_is_one_site(monkeypatch):
    """ME_HOST blank (the default, until me.puntmore.com resolves): no host is Me."""
    from app.config import settings
    from app.site import site_for
    monkeypatch.setattr(settings, "me_host", "")
    assert site_for("me.puntmore.com") == site_for("puntmore.com") == "puntmore"


def test_the_site_lists_agree_with_the_frontend():
    from app.site import ME_TOOLS
    site_js = (ROOT / "frontend" / "src" / "site.js").read_text()
    assert "SHALGO" not in site_js
    js = re.search(r"ME_TOOLS = \[([^\]]*)\]", site_js).group(1)
    assert set(re.findall(r"'([\w-]+)'", js)) == set(ME_TOOLS)


def test_every_tool_belongs_to_the_main_site_or_me(monkeypatch):
    from app import site
    from app.config import settings
    from app.security import ALL_TOOLS
    monkeypatch.setattr(settings, "me_host", "me.puntmore.com")
    assert not hasattr(site, "SHALGO") and not hasattr(site, "SHALGO_TOOLS")
    assert site.ME_TOOLS == ("finder", "pnl", "kelly", "boost", "shop", "aw26")
    for tool in ALL_TOOLS:
        assert site.tool_site(tool) == (site.ME if tool in site.ME_TOOLS else site.PUNTMORE), tool
    assert {site.tool_site(t) for t in ("coupons", "cards", "hills", "accabonus", "goalscorers")} == {site.PUNTMORE}


def test_with_me_host_blank_every_tool_is_the_main_sites(monkeypatch):
    from app import site
    from app.config import settings
    from app.security import ALL_TOOLS
    monkeypatch.setattr(settings, "me_host", "")
    assert {site.tool_site(t) for t in ALL_TOOLS} == {site.PUNTMORE}


def test_me_host_is_a_setting_blank_by_default():
    from app.config import Settings
    assert Settings.model_fields["me_host"].default == ""


def test_the_frontend_is_told_the_me_host(monkeypatch):
    """Public: the shell needs it before anyone signs in (site.js)."""
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    monkeypatch.setattr(settings, "me_host", "")
    assert TestClient(app).get("/auth/site").json() == {"me_host": ""}
    monkeypatch.setattr(settings, "me_host", "Me.Puntmore.com")
    assert TestClient(app).get("/auth/site").json() == {"me_host": "me.puntmore.com"}


def test_shalgo_has_no_identity_left():
    from app import identity
    assert all(not t.startswith("Shalgo") for _, t, _, _ in identity.IDENTITIES)
    assert set(identity.DEFAULTS) == {"puntmore", "me"}
    assert not (ROOT / "frontend" / "public" / "manifests" / "shalgo.webmanifest").exists()


def test_a_shalgo_address_is_sent_to_the_same_path_on_the_main_domain():
    from fastapi.testclient import TestClient
    from app.asgi import root
    c = TestClient(root, base_url="http://shalgo.puntmore.com")
    r = c.get("/paddy?day=sat", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "http://puntmore.com/paddy?day=sat"
    r = c.get("/api/health", headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://puntmore.com/api/health"
    dev = TestClient(root, base_url="http://shalgo.localhost:5173")
    assert dev.get("/", follow_redirects=False).headers["location"] == "http://localhost:5173/"
    # Anything that is not a GET keeps its method and body (308, not 301).
    assert c.post("/api/auth/login", json={}, follow_redirects=False).status_code == 308
    # The main domain and me are served, not redirected.
    assert TestClient(root, base_url="http://puntmore.com").get("/api/health").status_code == 200
    assert TestClient(root, base_url="http://me.puntmore.com").get("/api/health").status_code == 200


def test_the_redirect_keeps_the_path_as_sent_and_trusts_only_http_or_https():
    from fastapi.testclient import TestClient
    from app.asgi import root
    c = TestClient(root, base_url="http://shalgo.puntmore.com")
    # The raw path: a non-latin-1 character is no 500, and an encoded ? or # stays encoded.
    r = c.get("/t/%E2%82%AC%3Fx%23y?day=sat", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "http://puntmore.com/t/%E2%82%AC%3Fx%23y?day=sat"
    r = c.get("/paddy", headers={"x-forwarded-proto": "javascript"}, follow_redirects=False)
    assert r.headers["location"] == "http://puntmore.com/paddy"
    r = c.get("/paddy", headers={"x-forwarded-proto": "HTTPS, http"}, follow_redirects=False)
    assert r.headers["location"] == "https://puntmore.com/paddy"


def test_shalgo_serves_a_worker_that_retires_itself_not_a_redirect():
    """An installed shalgo app's service worker can only be replaced by a
    worker served from shalgo: a browser refuses a redirected worker script.
    So /sw.js there is a small worker that unregisters itself and reloads every
    window on the main domain, same path (SR2)."""
    from fastapi.testclient import TestClient
    from app.asgi import root
    c = TestClient(root, base_url="http://shalgo.puntmore.com")
    r = c.get("/sw.js", headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    assert r.status_code == 200 and "location" not in r.headers
    assert r.headers["content-type"].startswith("application/javascript")
    assert "self.registration.unregister()" in r.text and "navigate(" in r.text
    assert 'const MAIN = "https://puntmore.com"' in r.text and "__MAIN__" not in r.text
    assert c.head("/sw.js", follow_redirects=False).status_code == 200
    # Only there: the main domain's /sw.js is the real worker, never this one.
    main = TestClient(root, base_url="http://puntmore.com").get("/sw.js", follow_redirects=False)
    assert "self.registration.unregister()" not in main.text


def test_me_has_its_own_default_and_no_tracker():
    assert identity_for("/login", "me") == {
        "title": "Me", "svg": "/icons/me.svg", "png": "/icons/me-180.png",
        "manifest": "/manifests/me.webmanifest",
    }
    assert identity_for("/", "me")["title"] == "Me"
    for key in ("svg", "png", "manifest"):
        assert (ROOT / "frontend" / "public" / identity_for("/", "me")[key].lstrip("/")).is_file()
