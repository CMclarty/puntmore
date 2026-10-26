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
import re
import time

# Background tabs in Chrome have their timers throttled to about once a minute,
# which would stop a kept-open page refreshing its own prices. These keep every
# tab running as if it were the one on screen.
_KEEP_TABS_LIVE = [
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]


# One UK reader's ordinary Chrome window, set the way Chrome itself sets
# things - not overridden page by page. Language: on Linux Chrome takes it
# from the environment (it ignores --lang there), which gives its own
# "en-GB,en-US;q=0.9,en;q=0.8"; Playwright's `locale` option would instead
# override it to a bare "en-GB" that no real Chrome sends. Window: a real
# window of a normal size on the (larger) screen, not an emulated viewport -
# an emulated one made the page as tall as the whole screen.
_WINDOW = ["--window-size=1600,1000", "--window-position=0,0"]
_ENV = {"LANGUAGE": "en_GB:en", "TZ": "Europe/London"}


def launch(p, settings, headless=False):
    env = {**_ENV, **{k: v for k, v in os.environ.items() if k != "LANGUAGE"}}
    kw = dict(headless=headless, no_viewport=True, args=_KEEP_TABS_LIVE + _WINDOW,
              env=env)
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


_SEEN = """() => ({
  userAgent: navigator.userAgent,
  brands: (navigator.userAgentData && navigator.userAgentData.brands || [])
            .map(b => b.brand + ' ' + b.version),
  webdriver: navigator.webdriver,
  language: navigator.language,
  languages: navigator.languages,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  utcOffsetMin: -new Date().getTimezoneOffset(),
  inner: [innerWidth, innerHeight], outer: [outerWidth, outerHeight],
  screen: [screen.width, screen.height], dpr: devicePixelRatio,
})"""


def selfcheck(ctx) -> dict:
    """What a site sees of this browser, read from about:blank and Chrome's own
    chrome://version page (no network). Changes nothing."""
    page = ctx.new_page()
    try:
        page.goto("about:blank")
        seen = page.evaluate(_SEEN)
        try:
            page.goto("chrome://version")
            seen["version"] = " ".join(page.locator("#version").inner_text().split())
            if not seen["brands"]:          # only on a secure page; this one is
                seen["brands"] = page.evaluate(_SEEN)["brands"]
            cmd = page.locator("#command_line").inner_text()
        except Exception:
            seen["version"], cmd = None, ""
        seen["enable_automation"] = "--enable-automation" in cmd
        seen["automation_controlled_disabled"] = "AutomationControlled" in cmd
        seen["command_line_read"] = bool(cmd)
        return seen
    finally:
        page.close()


def selfcheck_lines(seen: dict, source: str) -> list[str]:
    yn = lambda b: "yes" if b else "no"
    auto = ("PRESENT - Chrome shows its 'controlled by automated test software' bar"
            if seen["enable_automation"] else "absent")
    if not seen.get("command_line_read"):
        auto = "unknown (chrome://version unreadable)"
    return [
        f"selfcheck - what a site sees ({source})",
        f"  browser             {seen.get('version') or 'unknown'}",
        f"  userAgent           {seen['userAgent']}",
        f"  brands              {', '.join(seen['brands']) or '-'}",
        f"  navigator.webdriver {str(seen['webdriver']).lower()}",
        f"  language            {seen['language']}  (languages: {', '.join(seen['languages'])})",
        f"  timezone            {seen['timezone']}  (UTC{seen['utcOffsetMin'] / 60 + 0.0:+g} now)",
        f"  window              inner {seen['inner'][0]}x{seen['inner'][1]}, "
        f"outer {seen['outer'][0]}x{seen['outer'][1]}, "
        f"screen {seen['screen'][0]}x{seen['screen'][1]}, dpr {seen['dpr']:g}",
        f"  --enable-automation {auto}",
        f"  AutomationControlled disabled  {yn(seen['automation_controlled_disabled'])}",
    ]


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
                click(page, btn.first)
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


def click(page, loc):
    """Click the way a hand does: bring it into view, move the pointer over
    it, pause, press and release - not a jump to its exact centre."""
    loc.scroll_into_view_if_needed(timeout=5000)
    page.wait_for_timeout(random.randint(300, 900))
    box = loc.bounding_box()
    if not box:
        raise RuntimeError("element has no box")
    x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    page.mouse.move(x, y, steps=random.randint(12, 30))
    page.wait_for_timeout(random.randint(250, 900))
    page.mouse.click(x, y, delay=random.randint(50, 140))


def read_down(page):
    """Look down what a click has just shown, then back up to the top, so
    markets drawn only when they come into view are drawn."""
    for _ in range(random.randint(2, 4)):
        page.mouse.move(random.randint(250, 1100), random.randint(300, 800),
                        steps=random.randint(8, 25))
        page.mouse.wheel(0, random.randint(300, 700))
        page.wait_for_timeout(random.randint(900, 2500))
    page.mouse.wheel(0, -random.randint(2500, 4000))
    page.wait_for_timeout(random.randint(500, 1200))


# Where a page's own tab bar is, when its tabs are not ARIA tabs/links/buttons.
_TAB_BAR = (":is([role=tablist], nav, [class*='tab' i]:not([class*='table' i]), "
            "[data-testid*='tab' i])")


def find_tab(page, name):
    """The visible in-page tab called `name`, as (locator, its text), or None.
    An exact name wins over a prefix ("Cards" finds "Cards & Fouls" only when
    there is no tab called just "Cards"). ARIA tabs, links and buttons first;
    then plain elements inside something that looks like a tab bar."""
    esc = re.escape(name.strip())
    for pat in (re.compile(rf"^\s*{esc}\s*$", re.I), re.compile(rf"^\s*{esc}\b", re.I)):
        candidates = [page.get_by_role(role, name=pat) for role in ("tab", "link", "button")]
        candidates.append(page.locator(_TAB_BAR).get_by_text(pat))
        for loc in candidates:
            try:
                for i in range(min(loc.count(), 5)):
                    el = loc.nth(i)
                    if el.is_visible():
                        return el, " ".join(el.inner_text().split())
            except Exception:
                continue
    return None


def wait_for_feed(page, capture, since: float, feed, timeout_s: float = 12.0) -> bool:
    """Wait, up to timeout_s, for the page's own network to answer: True once
    a response whose URL matches `feed` has arrived after `since`."""
    deadline = since + timeout_s
    while time.time() < deadline:
        if any(feed.search(url) for _ts, _k, url, _t in capture.since(since)):
            page.wait_for_timeout(random.randint(1500, 3000))   # its prices follow
            return True
        page.wait_for_timeout(500)
    return False
