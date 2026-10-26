"""One real Chrome, one persistent profile, shared by both sites.

Nothing here hides automation or fakes a fingerprint: spoofed values that
disagree with the real browser are what bot checks look for. The profile is a
real, ordinary Chrome profile, and the pages are kept
OPEN so the site's own scripts keep them fresh — which is what keeps the
traffic down to roughly what one person with a few tabs open would make.

Challenges are only ever DETECTED, never answered.
"""
import os
import random

# Background tabs in Chrome have their timers throttled to about once a minute,
# which would stop a kept-open page refreshing its own prices. These keep every
# tab running as if it were the one on screen.
_KEEP_TABS_LIVE = [
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]


def launch(p, settings, headless=False):
    kw = dict(headless=headless, viewport={"width": 1400, "height": 1000},
              args=_KEEP_TABS_LIVE)
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    if os.environ.get("SCRAPER_CHROME_PATH"):      # tests / unusual installs
        kw["executable_path"] = os.environ["SCRAPER_CHROME_PATH"]
        return p.chromium.launch_persistent_context(str(settings.profile_dir), **kw)
    try:
        return p.chromium.launch_persistent_context(
            str(settings.profile_dir), channel=settings.channel, **kw)
    except Exception as exc:
        if settings.channel is None:
            raise
        print(f"Chrome ({settings.channel}) would not start ({exc.__class__.__name__}); "
              "using bundled Chromium. `playwright install chrome` to fix.")
        return p.chromium.launch_persistent_context(str(settings.profile_dir), **kw)


def blocked(page, status: int | None = None) -> str | None:
    """Why this page needs a human (or must be left alone), else None."""
    if status in (403, 429):
        return f"HTTP {status}"
    try:
        title = (page.title() or "").lower()
        if ("just a moment" in title or "attention required" in title
                or page.locator("#challenge-form, #cf-challenge-running").count()
                or page.locator("iframe[src*='challenges.cloudflare.com']").count()):
            return "Cloudflare check"
    except Exception:
        return None
    return None


def logged_out(page) -> bool:
    try:
        return page.locator("input[type=password]:visible").count() > 0
    except Exception:
        return False


_MIN_COOKIES = ("Reject All", "Reject all", "Necessary only", "Only necessary",
                "Accept necessary", "Necessary cookies only")


def dismiss_consent(page):
    """Pick the minimum-cookies option if a consent banner is showing."""
    for label in _MIN_COOKIES:
        btn = page.get_by_role("button", name=label)
        try:
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(random.randint(600, 1500))
                return label
        except Exception:
            continue
    return None


def settle(page):
    """Arrive like a reader: wait for the page to draw, look down it a little."""
    page.wait_for_timeout(random.randint(2500, 5000))
    for _ in range(random.randint(1, 3)):
        page.mouse.move(random.randint(200, 1100), random.randint(200, 800),
                        steps=random.randint(8, 25))
        page.mouse.wheel(0, random.randint(250, 700))
        page.wait_for_timeout(random.randint(700, 2200))
    page.mouse.wheel(0, -random.randint(800, 2000))
