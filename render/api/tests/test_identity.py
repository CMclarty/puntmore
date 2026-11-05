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
        "title": "Shalgo — Paddy", "svg": "/icons/paddy.svg", "png": "/icons/paddy-180.png",
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
    assert '<meta name="apple-mobile-web-app-title" content="Shalgo — Paddy" />' in html
    assert "<title>Shalgo — Paddy</title>" in html
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
                 "/scraper", "/login", "/nope"):
        ident = identity_for(path)
        # Every file the server names exists in the built public set.
        for key in ("svg", "png", "manifest"):
            assert (ROOT / "frontend" / "public" / ident[key].lstrip("/")).is_file(), (path, ident[key])


def test_shalgo_has_its_own_default_and_landing():
    assert identity_for("/login", "shalgo") == {
        "title": "Shalgo", "svg": "/icons/shalgo.svg", "png": "/icons/shalgo-180.png",
        "manifest": "/manifests/shalgo.webmanifest",
    }
    assert identity_for("/", "shalgo")["title"] == "Shalgo"           # no tracker there
    assert identity_for("/", "puntmore")["title"] == "Puntmore — Coupon"
    html = render_index(INDEX, "/login", "shalgo")
    assert "<title>Shalgo</title>" in html and "/manifests/shalgo.webmanifest" in html
    for key in ("svg", "png", "manifest"):
        assert (ROOT / "frontend" / "public" / identity_for("/", "shalgo")[key].lstrip("/")).is_file()


def test_the_host_names_the_site():
    from app.site import site_for
    assert site_for("shalgo.puntmore.com") == "shalgo"
    assert site_for("shalgo.localhost:5173") == "shalgo"
    assert site_for("puntmore.com") == site_for("www.puntmore.com") == "puntmore"
    assert site_for("shalgo") == site_for(None) == site_for("notshalgo.puntmore.com") == "puntmore"


def test_the_site_lists_agree_with_the_frontend():
    from app.site import SHALGO_TOOLS
    site_js = (ROOT / "frontend" / "src" / "site.js").read_text()
    js = re.search(r"SHALGO_TOOLS = \[([^\]]*)\]", site_js).group(1)
    assert set(re.findall(r"'([\w-]+)'", js)) == set(SHALGO_TOOLS)
