"""Fair goalkeeper-saves prices from Bookie Bashing -> puntmore.

Runs on YOUR PC, not the server: Bookie Bashing is behind your login, and the
login stays in a browser profile on this machine. The script only sends the
fair prices it reads to POST /api/paddy/saves-fair, where they are matched to
Paddy Power's saves lines (from OddsPapi) and valued like corners and cards.

SETUP (once)
    pip install playwright httpx
    playwright install chrome     # real Google Chrome (may need sudo); falls
                                  # back to bundled Chromium if it is missing
    python bookiebashing_saves.py login        # log in, then close the window

ON A HEADLESS LINUX SERVER
    Run under a virtual display rather than in headless mode - a real Chrome
    window is the ordinary browser this profile was logged in with, headless
    is not:
        sudo apt install xvfb x11vnc
        xvfb-run -a python bookiebashing_saves.py run
    `login`, and clearing a Cloudflare check, need you to SEE the window:
        Xvfb :99 -screen 0 1400x1000x24 &  x11vnc -display :99 -localhost &
        DISPLAY=:99 python bookiebashing_saves.py login
    then VNC in over an SSH tunnel (ssh -L 5900:localhost:5900 server).

COMMANDS
    login   Opens a visible browser on Bookie Bashing. Log in, close it. The
            session is kept in PROFILE_DIR for every later run.
    dump    Saves the saves page as HTML + a screenshot into ./bb-dump/.
            SEND THESE OVER: the reader (parse_page) is written from them.
    run     The loop: read the page, send the prices, wait, repeat.
            It never answers a Cloudflare check or types your password. On
            either it alerts (Discord, if BB_ALERT_WEBHOOK is set), backs off
            and keeps checking quietly until you have dealt with it by hand.
            Ctrl+C / SIGTERM stops it cleanly; the login lives in the
            profile, so starting it again carries straight on.

SETTINGS (environment variables, or flags)
    BB_SAVES_URL      the Bookie Bashing page with the saves prices
    PUNTMORE_URL      default https://puntmore.com
    SAVES_INGEST_KEY  the same value as SAVES_INGEST_KEY in the server's .env
    BB_ALERT_WEBHOOK  optional Discord webhook for "needs you" alerts
    BB_CHANNEL        browser channel, default "chrome" ("" = bundled Chromium)
"""
import argparse
import os
import pathlib
import random
import signal
import sys
import threading
from datetime import datetime

PROFILE_DIR = pathlib.Path.home() / ".puntmore-bookiebashing"
BB_HOME = "https://www.bookiebashing.net/"

# Backoff while the page needs a human (Cloudflare check, logged out): first
# re-check after 15 min, doubling to at most 2 h. Never a tight retry loop.
BLOCKED_BACKOFF_MIN = (15, 120)

_stop = threading.Event()


def _browser(p, headless):
    channel = os.environ.get("BB_CHANNEL", "chrome") or None
    kw = dict(headless=headless, viewport={"width": 1400, "height": 1000})
    try:
        return p.chromium.launch_persistent_context(str(PROFILE_DIR), channel=channel, **kw)
    except Exception as exc:
        if channel is None:
            raise
        print(f"Chrome ({channel}) would not start ({exc.__class__.__name__}); "
              "using bundled Chromium. `playwright install chrome` to fix.")
        return p.chromium.launch_persistent_context(str(PROFILE_DIR), **kw)


def _blocked(page) -> str | None:
    """Why the page needs a human, or None. Only ever DETECTS - solving the
    check or re-entering the password is left to you."""
    title = (page.title() or "").lower()
    if ("just a moment" in title or "attention required" in title
            or page.locator("#challenge-form, #cf-challenge-running").count()
            or page.locator("iframe[src*='challenges.cloudflare.com']").count()):
        return "Cloudflare check"
    if page.locator("input[type=password]:visible").count():
        return "logged out"
    return None


def _alert(message):
    print(message)
    hook = os.environ.get("BB_ALERT_WEBHOOK")
    if not hook:
        return
    import httpx
    try:
        httpx.post(hook, json={"content": f"Bookie Bashing reader: {message}"}, timeout=15)
    except Exception as exc:
        print(f"         alert not sent: {exc}")


def _on_signal(signum, _frame):
    print(f"\nStopping ({signal.Signals(signum).name}) - closing the browser.")
    _stop.set()


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
        reason = _blocked(page)
        if reason:
            ctx.close()
            sys.exit(f"The page is behind a {reason} - run `login` and deal with it, then dump again.")
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


def run(url, server, key, every_minutes, headless):
    import httpx
    from playwright.sync_api import sync_playwright
    endpoint = server.rstrip("/") + "/api/paddy/saves-fair"
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    blocked_for = None     # the reason we are waiting on you, while we are
    backoff = BLOCKED_BACKOFF_MIN[0]
    with sync_playwright() as p:
        ctx = _browser(p, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        while not _stop.is_set():
            stamp = datetime.now().strftime("%H:%M")
            # Jittered, so reads are never on a fixed beat.
            wait = every_minutes * 60 * random.uniform(0.75, 1.35)
            try:
                _load(page, url)
                reason = _blocked(page)
                if reason:
                    if reason != blocked_for:
                        _alert(f"{stamp}  {reason} - needs you (run `login`). "
                               f"Re-checking every {backoff}-{BLOCKED_BACKOFF_MIN[1]} min.")
                    blocked_for = reason
                    wait = backoff * 60 * random.uniform(0.9, 1.2)
                    backoff = min(backoff * 2, BLOCKED_BACKOFF_MIN[1])
                    _stop.wait(wait)
                    continue
                if blocked_for:
                    _alert(f"{stamp}  clear again ({blocked_for} sorted) - reading.")
                    blocked_for, backoff = None, BLOCKED_BACKOFF_MIN[0]
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
                ctx.close()
                sys.exit(str(exc))
            except Exception as exc:     # keep looping through a blip
                if _stop.is_set():
                    break
                print(f"{stamp}  failed: {exc}")
            _stop.wait(wait)
        ctx.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["login", "dump", "run"])
    ap.add_argument("--url", default=os.environ.get("BB_SAVES_URL"))
    ap.add_argument("--server", default=os.environ.get("PUNTMORE_URL", "https://puntmore.com"))
    ap.add_argument("--key", default=os.environ.get("SAVES_INGEST_KEY"))
    ap.add_argument("--every", type=float, default=10, help="minutes between reads (jittered)")
    ap.add_argument("--headless", action="store_true",
                    help="no window at all - use xvfb-run instead where you can")
    a = ap.parse_args()
    if a.command == "login":
        return login()
    if not a.url:
        sys.exit("Set BB_SAVES_URL or pass --url (the Bookie Bashing saves page).")
    if a.command == "dump":
        return dump(a.url)
    if not a.key:
        sys.exit("Set SAVES_INGEST_KEY or pass --key.")
    run(a.url, a.server, a.key, a.every, a.headless)


if __name__ == "__main__":
    main()
