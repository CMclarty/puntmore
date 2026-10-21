"""Fair prices from Bookie Bashing's Group Tracker -> puntmore's AW 26 slips.

Runs on YOUR PC, not the server, for the same reason as the saves script:
Bookie Bashing is behind your login, and the login stays in the browser profile
on this machine. It shares that profile with bookiebashing_saves.py, so if you
have logged in for one you are logged in for both.

The script only sends the fair prices it reads to POST /api/aw26/fairs. Every
AW 26 slip still waiting on a fair price is priced the moment they arrive; a
slip whose EV is already fixed is left alone.

SETUP (once)
    pip install playwright httpx
    playwright install chromium
    python bookiebashing_tracker.py login      # log in, then close the window

COMMANDS
    login   Opens a visible browser on Bookie Bashing. Log in, close it.
    dump    Saves the Group Tracker page as HTML + a screenshot into
            ./bb-tracker-dump/. SEND THESE OVER: the reader (parse_page) is
            written from them, not guessed.
    once    Read the page and send the prices, once (for Task Scheduler).
    run     The same, then again every --every minutes (default: daily).

SETTINGS (environment variables, or flags)
    BB_TRACKER_URL    the Group Tracker page
    PUNTMORE_URL      default https://puntmore.com
    SAVES_INGEST_KEY  the same value as SAVES_INGEST_KEY in the server's .env
                      (one key for both Bookie Bashing scripts)
"""
import argparse
import os
import pathlib
import sys
import time
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bookiebashing_saves import _browser, _load, login  # noqa: E402  (one profile, one login)


def dump(url):
    from playwright.sync_api import sync_playwright
    out = pathlib.Path("bb-tracker-dump")
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
    """The fair prices on the Group Tracker, as the server wants them:

        {"home": "Arsenal", "away": "Chelsea", "kickoff": "2026-10-24T14:00:00Z",
         "market": "Total Match Corners", "selection": "Over 9.5",
         "fair_odds": 1.83}

    The market and selection in WORDS, as the tracker prints them: the server
    reads the line and side out of them the same way it reads a slip's, so the
    two are matched like for like. kickoff may be left out.
    NOT WRITTEN YET - it needs the page's layout. Run `dump` and send the files.
    """
    raise NotImplementedError(
        "parse_page needs the Group Tracker's layout - run "
        "`python bookiebashing_tracker.py dump` and send bb-tracker-dump/ over."
    )


def send_once(page, url, endpoint, key) -> None:
    import httpx
    stamp = datetime.now().strftime("%d %b %H:%M")
    _load(page, url)
    fairs = parse_page(page)
    r = httpx.post(endpoint, json={"fairs": fairs}, headers={"X-API-Key": key}, timeout=60)
    r.raise_for_status()
    body = r.json()
    print(f"{stamp}  read {len(fairs)}, stored {body['stored']}, priced {body['priced']} "
          f"slips, {body['still_unpriced']} still waiting")


def run(url, server, key, every_minutes, once=False):
    from playwright.sync_api import sync_playwright
    endpoint = server.rstrip("/") + "/api/aw26/fairs"
    with sync_playwright() as p:
        ctx = _browser(p, headless=True)
        page = ctx.new_page()
        while True:
            try:
                send_once(page, url, endpoint, key)
            except NotImplementedError as exc:
                sys.exit(str(exc))
            except Exception as exc:
                if once:
                    sys.exit(f"failed: {exc}")
                print(f"{datetime.now():%d %b %H:%M}  failed: {exc}")   # keep looping through a blip
            if once:
                return
            time.sleep(every_minutes * 60)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["login", "dump", "once", "run"])
    ap.add_argument("--url", default=os.environ.get("BB_TRACKER_URL"))
    ap.add_argument("--server", default=os.environ.get("PUNTMORE_URL", "https://puntmore.com"))
    ap.add_argument("--key", default=os.environ.get("SAVES_INGEST_KEY"))
    ap.add_argument("--every", type=float, default=24 * 60, help="minutes between reads")
    a = ap.parse_args()
    if a.command == "login":
        return login()
    if not a.url:
        sys.exit("Set BB_TRACKER_URL or pass --url (the Bookie Bashing Group Tracker page).")
    if a.command == "dump":
        return dump(a.url)
    if not a.key:
        sys.exit("Set SAVES_INGEST_KEY or pass --key.")
    run(a.url, a.server, a.key, a.every, once=a.command == "once")


if __name__ == "__main__":
    main()
