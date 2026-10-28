"""Home-screen identity per tool, applied to index.html AS IT IS SERVED.

iOS reads the manifest (start_url, id) when the page LOADS, not when "Add to
Home Screen" is tapped — so the frontend's PageIdentity swapping the
<link rel="manifest"> after boot was too late: every shortcut was pinned with
the default manifest and opened "/" (Oct 2026). The icon and title are read
later, which is why those did change per tool and the start page did not.

So the SPA fallback (asgi.py) rewrites the shell's head for the path being
served: manifest, apple-touch-icon, favicon and title. PageIdentity still runs
in the page for client-side navigation. The table below MIRRORS
frontend/src/identity.js (tests/test_identity.py checks they agree).
"""
import re

# (path matcher, title, icon name, manifest name or None when it is the icon's)
IDENTITIES = [
    (lambda p: p == "/" or p.startswith("/t/"), "Puntmore — Coupon", "coupons", None),
    (lambda p: p == "/upload", "Puntmore — Upload", "upload", None),
    (lambda p: p == "/pnl/dogs", "P&L — Dogs", "pnl-dogs", None),
    (lambda p: p == "/virtuals", "Puntmore — Virtuals", "boost", "virtuals"),
    (lambda p: p == "/kelly", "Puntmore — Kelly", "kelly", None),
    (lambda p: p == "/shop", "Puntmore — Shop Visits", "shop", None),
    (lambda p: p == "/365/scorers", "Puntmore — Goalscorers", "goalscorers", None),
    (lambda p: p.startswith("/365") or p == "/accabonus", "Puntmore — 365", "accabonus", None),
    (lambda p: p.startswith("/paddy") or p == "/cards", "Puntmore — Paddy", "paddy", None),
    (lambda p: p.startswith("/hills"), "Puntmore — Hills", "hills", None),
    (lambda p: p == "/scraper", "Puntmore — Scraper", "scraper", None),
]
DEFAULT = ("Puntmore", "puntmore", None)


def identity_for(path: str) -> dict:
    """title, svg, png and manifest paths for a page path — the same answer
    frontend/src/identity.js gives."""
    path = "/" + path.strip("/") if path.strip("/") else "/"
    for match, title, icon, manifest in IDENTITIES:
        if match(path):
            break
    else:
        title, icon, manifest = DEFAULT
    manifest = manifest or icon
    return {
        "title": title,
        "svg": "/icon.svg" if icon == "puntmore" else f"/icons/{icon}.svg",
        "png": f"/icons/{icon}-180.png",
        "manifest": "/manifest.webmanifest" if icon == "puntmore" else f"/manifests/{manifest}.webmanifest",
    }


_TAGS = (
    (re.compile(r'(<link[^>]*rel="manifest"[^>]*href=")[^"]*(")'), "manifest"),
    (re.compile(r'(<link[^>]*rel="apple-touch-icon"[^>]*href=")[^"]*(")'), "png"),
    (re.compile(r'(<link[^>]*rel="icon"[^>]*href=")[^"]*(")'), "svg"),
    (re.compile(r'(<meta[^>]*name="apple-mobile-web-app-title"[^>]*content=")[^"]*(")'), "title"),
    (re.compile(r"(<title>)[^<]*(</title>)"), "title"),
)


def render_index(html: str, path: str) -> str:
    """The shell with its head pointed at the identity for `path`."""
    ident = identity_for(path)
    for pattern, key in _TAGS:
        html = pattern.sub(lambda m, v=ident[key]: m.group(1) + v + m.group(2), html, count=1)
    return html
