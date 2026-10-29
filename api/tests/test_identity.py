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
    py_icons = {icon for _, _, icon, _ in identity.IDENTITIES} | {identity.DEFAULT[1]}
    assert js_icons == py_icons
    for path in ("/", "/t/abc", "/upload", "/pnl/dogs", "/virtuals", "/kelly", "/shop",
                 "/365/scorers", "/365/acca", "/accabonus", "/paddy", "/paddy/acca", "/cards",
                 "/scraper", "/login", "/nope"):
        ident = identity_for(path)
        # Every file the server names exists in the built public set.
        for key in ("svg", "png", "manifest"):
            assert (ROOT / "frontend" / "public" / ident[key].lstrip("/")).is_file(), (path, ident[key])
