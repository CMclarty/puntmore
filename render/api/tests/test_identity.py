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
    for path in ("/", "/t/abc", "/upload", "/pnl/dogs", "/virtuals", "/kelly", "/shop",
                 "/365/scorers", "/365/acca", "/accabonus", "/paddy", "/paddy/acca", "/cards",
                 "/login", "/nope"):
        ident = identity_for(path)
        # Every file the server names exists in the built public set.
        for key in ("svg", "png", "manifest"):
            assert (ROOT / "frontend" / "public" / ident[key].lstrip("/")).is_file(), (path, ident[key])


def test_the_host_names_the_site():
    from app.site import site_for
    assert site_for("puntmore.com") == site_for("www.puntmore.com") == "puntmore"
    assert site_for("me.puntmore.com") == "me" and site_for("me.localhost:5173") == "me"
    assert site_for("me") == site_for(None) == site_for("notme.puntmore.com") == "puntmore"
    # Shalgo is retired: its host is the main site (asgi redirects it first).
    assert site_for("shalgo.puntmore.com") == "puntmore"


def test_the_site_lists_agree_with_the_frontend():
    from app.site import ME_TOOLS
    site_js = (ROOT / "frontend" / "src" / "site.js").read_text()
    assert "SHALGO" not in site_js
    js = re.search(r"ME_TOOLS = \[([^\]]*)\]", site_js).group(1)
    assert set(re.findall(r"'([\w-]+)'", js)) == set(ME_TOOLS)


def test_every_tool_belongs_to_the_main_site_or_me():
    from app import site
    from app.security import ALL_TOOLS
    assert not hasattr(site, "SHALGO") and not hasattr(site, "SHALGO_TOOLS")
    assert site.ME_TOOLS == ("finder", "pnl", "kelly", "boost", "shop")
    for tool in ALL_TOOLS:
        assert site.tool_site(tool) == (site.ME if tool in site.ME_TOOLS else site.PUNTMORE), tool
    assert {site.tool_site(t) for t in ("coupons", "cards", "hills", "accabonus", "goalscorers")} == {site.PUNTMORE}


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


def test_me_has_its_own_default_and_no_tracker():
    assert identity_for("/login", "me") == {
        "title": "Me", "svg": "/icons/me.svg", "png": "/icons/me-180.png",
        "manifest": "/manifests/me.webmanifest",
    }
    assert identity_for("/", "me")["title"] == "Me"
    for key in ("svg", "png", "manifest"):
        assert (ROOT / "frontend" / "public" / identity_for("/", "me")[key].lstrip("/")).is_file()
