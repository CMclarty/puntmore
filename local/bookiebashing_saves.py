"""Fair goalkeeper-saves prices from Bookie Bashing -> puntmore.

Runs on YOUR PC, not the server: Bookie Bashing is behind your login, and the
login stays in a browser profile on this machine. The script only sends the
fair prices it reads to POST /api/paddy/saves-fair, where they are matched to
Paddy Power's saves lines (from OddsPapi) and valued like corners and cards.

SETUP (once)
    pip install playwright httpx
    playwright install chromium
    python bookiebashing_saves.py login        # log in, then close the window

COMMANDS
    login   Opens a visible browser on Bookie Bashing. Log in, close it. The
            session is kept in PROFILE_DIR for every later run.
    dump    Saves the saves page as HTML + a screenshot into ./bb-dump/.
            SEND THESE OVER: the reader (parse_page) is written from them.
    run     The loop: read the page, send the prices, wait, repeat.

SETTINGS (environment variables, or flags)
    BB_SAVES_URL      the Bookie Bashing page with the saves prices
    PUNTMORE_URL      default https://puntmore.com
    SAVES_INGEST_KEY  the same value as SAVES_INGEST_KEY in the server's .env
"""
import argparse
import os
import pathlib
import sys
import time
from datetime import datetime

PROFILE_DIR = pathlib.Path.home() / ".puntmore-bookiebashing"
BB_HOME = "https://www.bookiebashing.net/"


def _browser(p, headless):
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=headless, viewport={"width": 1400, "height": 1000},
    )


def login():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _browser(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BB_HOME)
        print("Log in to Bookie Bashing in the window, then close it.")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()


def _load(page, url):
    page.goto(url, wait_until="networkidle", timeout=60_000)
    # Tables on pages like this are often filled in by script after load.
    page.wait_for_timeout(3000)


def dump(url):
    from playwright.sync_api import sync_playwright
    out = pathlib.Path("bb-dump")
    out.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = _browser(p, headless=True)
        page = ctx.new_page()
        _load(page, url)
        (out / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / "page.png"), full_page=True)
        ctx.close()
    print(f"Saved {out / 'page.html'} and {out / 'page.png'} - send both over.")


def parse_page(page) -> list[dict]:
    """The saves prices on the page, as the server wants them:

        {"home": "Arsenal", "away": "Chelsea", "kickoff": "2026-10-18T15:00:00Z",
         "line": 7.5, "over": 1.91, "under": 2.10}   # fair decimal odds

    Either of over/under may be left out; the server takes the complement.
    NOT WRITTEN YET - it needs the page's layout. Run `dump` and send the files.
    """
    raise NotImplementedError(
        "parse_page needs the Bookie Bashing page's layout - run "
        "`python bookiebashing_saves.py dump` and send bb-dump/ over."
    )


def run(url, server, key, every_minutes):
    import httpx
    from playwright.sync_api import sync_playwright
    endpoint = server.rstrip("/") + "/api/paddy/saves-fair"
    with sync_playwright() as p:
        ctx = _browser(p, headless=True)
        page = ctx.new_page()
        while True:
            stamp = datetime.now().strftime("%H:%M")
            try:
                _load(page, url)
                prices = parse_page(page)
                r = httpx.post(endpoint, json={"prices": prices},
                               headers={"X-API-Key": key}, timeout=30)
                r.raise_for_status()
                body = r.json()
                print(f"{stamp}  read {len(prices)}, stored {body['stored']}, "
                      f"refused {len(body['rejected'])}")
                for rej in body["rejected"][:5]:
                    print("         refused:", rej)
            except NotImplementedError as exc:
                sys.exit(str(exc))
            except Exception as exc:     # keep looping through a blip
                print(f"{stamp}  failed: {exc}")
            time.sleep(every_minutes * 60)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["login", "dump", "run"])
    ap.add_argument("--url", default=os.environ.get("BB_SAVES_URL"))
    ap.add_argument("--server", default=os.environ.get("PUNTMORE_URL", "https://puntmore.com"))
    ap.add_argument("--key", default=os.environ.get("SAVES_INGEST_KEY"))
    ap.add_argument("--every", type=float, default=10, help="minutes between reads")
    a = ap.parse_args()
    if a.command == "login":
        return login()
    if not a.url:
        sys.exit("Set BB_SAVES_URL or pass --url (the Bookie Bashing saves page).")
    if a.command == "dump":
        return dump(a.url)
    if not a.key:
        sys.exit("Set SAVES_INGEST_KEY or pass --key.")
    run(a.url, a.server, a.key, a.every)


if __name__ == "__main__":
    main()
