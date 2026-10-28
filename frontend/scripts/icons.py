#!/usr/bin/env python3
"""Home-screen icons, one per tool: writes public/icons/<name>.svg and renders
public/icons/<name>-180.png and -512.png with headless Chrome.

Why PNGs: iOS ignores an SVG apple-touch-icon and falls back to a screenshot
of the page, so every pinned tool looked the same. iOS also rounds the corners
itself, so the PNGs are full-bleed squares (the SVGs keep their own corners
for Android and the browser tab). The glyphs are the bottom nav's, so a pinned
tool and its nav entry read as one thing.

    python3 scripts/icons.py          # from frontend/; needs google-chrome
"""
import pathlib
import shutil
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "public" / "icons"
INK = "#FCFBF8"

# Nav glyphs are drawn in a 22-unit box; placed in the 512 box with a margin
# wide enough for the maskable safe zone.
_G = 'fill="none" stroke="{ink}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"'
NAV = '<g transform="translate(70 70) scale(16.9)" ' + _G + '>{body}</g>'
BIG = '<g ' + _G.replace('1.9', '30') + '>{body}</g>'


def nav(body):
    return NAV.format(ink=INK, body=body)


def big(body):
    return BIG.format(ink=INK, body=body)


def text(s, size=280, y=340):
    return (f'<text x="256" y="{y}" font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif" '
            f'font-size="{size}" font-weight="700" fill="{INK}" text-anchor="middle">{s}</text>')


# name -> (background, glyph)
ICONS = {
    "puntmore": ("#2F7D55", text("P")),
    # Coupon tracker: the ticket.
    "coupons": ("#2F7D55", nav('<rect x="4" y="2" width="14" height="18" rx="2"/>'
                               '<line x1="7" y1="7" x2="15" y2="7"/><line x1="7" y1="11" x2="15" y2="11"/>'
                               '<line x1="7" y1="15" x2="11" y2="15"/>')),
    # Upload: a tray with an arrow in.
    "upload": ("#3D6B99", nav('<polyline points="4,14 4,19 18,19 18,14"/><line x1="11" y1="3" x2="11" y2="14"/>'
                              '<polyline points="7,7 11,3 15,7"/>')),
    # Dogs ledger: the P&L trend. (The battle pages and the virtuals ledger
    # redirect into /pnl/dogs and /virtuals now, so they have no icon.)
    "pnl-dogs": ("#6B4E9E", nav('<polyline points="3,15 8,10 12,13 19,4"/><polyline points="14,4 19,4 19,9"/>')),
    # Virtuals boost: the double chevron.
    "boost": ("#B4532A", nav('<polyline points="5,12 11,6 17,12"/><polyline points="5,17 11,11 17,17"/>')),
    # Kelly: the calculator.
    "kelly": ("#A8792F", nav('<rect x="5" y="2.5" width="12" height="17" rx="2"/><line x1="8" y1="6" x2="14" y2="6"/>'
                             '<circle cx="8.5" cy="11" r="0.9" fill="{ink}" stroke="none"/><circle cx="13.5" cy="11" r="0.9" fill="{ink}" stroke="none"/>'
                             '<circle cx="8.5" cy="14.5" r="0.9" fill="{ink}" stroke="none"/><circle cx="13.5" cy="14.5" r="0.9" fill="{ink}" stroke="none"/>'
                             .replace("{ink}", INK))),
    # 365: the number — the nav's chevrons are the boost's twin at this size.
    "accabonus": ("#A13D3D", text("365", size=200, y=328)),
    # Goalscorers: a ball.
    "goalscorers": ("#B0522D", big('<circle cx="256" cy="256" r="150"/>'
                                   '<path d="M256 186l66 48-25 78h-82l-25-78z" fill="' + INK + '"/>'
                                   '<path d="M256 186V106M322 234l76-24M297 312l48 64M215 312l-48 64M190 234l-76-24"/>')),
    # Paddy: the steps (a line ladder).
    "paddy": ("#556270", nav('<polyline points="4,4 4,18 18,18"/><polyline points="4,4 10,4 10,10 16,10 16,18"/>')),
    # Hills (the Paddy tool against William Hill): two hills.
    "hills": ("#1F3A5F", nav('<polyline points="2,17 8,8 12,13 15,9 20,17"/><line x1="2" y1="17" x2="20" y2="17"/>')),
    # Shop visits: the bag.
    "shop": ("#2F7D55", big('<path d="M136 196h240l-20 176H156z"/><path d="M204 196v-44a52 52 0 0 1 104 0v44"/>')),
    # Scraper control: the sliders.
    "scraper": ("#7B3F61", nav('<line x1="4" y1="6" x2="18" y2="6"/><line x1="4" y1="11" x2="18" y2="11"/>'
                               '<line x1="4" y1="16" x2="18" y2="16"/><circle cx="8" cy="6" r="1.6"/>'
                               '<circle cx="14" cy="11" r="1.6"/><circle cx="10" cy="16" r="1.6"/>')),
}


def svg(bg, glyph, rounded=True):
    rx = ' rx="96"' if rounded else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">\n'
            f'  <rect width="512" height="512"{rx} fill="{bg}"/>\n  {glyph}\n</svg>\n')


def main():
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise SystemExit("google-chrome not found")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        for name, (bg, glyph) in ICONS.items():
            target = (HERE.parent / "public" / "icon.svg") if name == "puntmore" else OUT / f"{name}.svg"
            target.write_text(svg(bg, glyph))
            flat = tmp / f"{name}.svg"
            flat.write_text(svg(bg, glyph, rounded=False))
            for size in (180, 512):
                page = tmp / f"{name}-{size}.html"
                page.write_text(f'<!doctype html><html><body style="margin:0;background:{bg}">'
                                f'<img src="{flat.name}" style="display:block;width:{size}px;height:{size}px"></body></html>')
                subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                                "--force-device-scale-factor=1", f"--window-size={size},{size}",
                                f"--user-data-dir={tmp / 'profile'}", f"--screenshot={OUT / f'{name}-{size}.png'}",
                                page.as_uri()], check=True, capture_output=True)
            print(f"{name}: svg + 180 + 512")


if __name__ == "__main__":
    main()
