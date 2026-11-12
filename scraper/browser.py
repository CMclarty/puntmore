"""One real Chrome, one persistent profile, shared by every site (Paddy Power,
Bookie Bashing, Finder's generic sources and any other page a tool needs).

Nothing here fakes a fingerprint: spoofed values that disagree with the real
browser are what bot checks look for. The profile is a real, ordinary Chrome
profile, and the pages are kept OPEN so the site's own scripts keep them
fresh — which is what keeps the traffic down to roughly what one person with
a few tabs open would make.

The ONE departure from Playwright's defaults (Callum's call, 26 Oct 2026):
Chrome is started without Playwright's `--enable-automation` test flag and
with AutomationControlled off, so it is the same Chrome a person would open —
no "controlled by automated test software" bar, `navigator.webdriver` false.
Everything else is left as Chrome sets it. Challenges are only ever DETECTED,
never answered.

One departure from Chrome's own defaults on the desktop (7 Nov 2026): Chrome
is told to use X11 (Xwayland) rather than the Wayland socket. On a Wayland
session with no monitor connected (the Hub's screen off overnight) a new
Wayland Chrome never finishes starting - it waits for an output that never
comes - and the service sat dead for 15 h behind launch timeouts, three times
in two days, until a screen was plugged back in. Xwayland keeps its last
geometry and starts Chrome in under a second either way. Measured 7 Nov 2026.
"""
import os
import random
import re
import time

from . import behaviour
from . import login

# Background tabs in Chrome have their timers throttled to about once a minute,
# which would stop a kept-open page refreshing its own prices. These keep every
# tab running as if it were the one on screen.
_KEEP_TABS_LIVE = [
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]


# Where Chrome runs decides how much of it is set by hand.
#
# On the Hub's desktop (the default since 5 Nov 2026) Chrome is left to its
# own defaults on the real display: on Linux Chrome takes its language from
# the session's locale (it ignores --lang there), which on the Hub is
# en_GB.UTF-8 and gives Chrome's own "en-GB,en-US;q=0.9,en;q=0.8"; the window
# is whatever Chrome and the profile choose on the real screen; and the GPU,
# fonts and screen a site sees are the box's own. Measured 5 Nov 2026: Chrome
# 157 in the container reported no WebGL at all and the container's fonts;
# the same binary on the Hub's Wayland session reports the
# Radeon 8060S through ANGLE.
#
# In the container (SCRAPER_IN_CONTAINER=1, set by the Dockerfile) and in a
# headless run (tests, CI) there is no real screen and no en_GB locale, so
# the settings below stand in for them. Language: there Chrome reads
# "en-US@posix" whatever LANGUAGE/LANG say (measured 26 Oct 2026, locale-gen
# included), so the list is given with --accept-lang, as Chrome's own list:
# navigator.languages reads ['en-GB', 'en-US', 'en'] and the header follows
# it. Playwright's `locale` option would instead give a bare "en-GB" that no
# real Chrome sends. Window: a real 1600x1000 window on Xvfb's 1920x1080
# screen (deploy/entrypoint.sh), not an emulated viewport - an emulated one
# made the page as tall as the whole screen.
_WINDOW = ["--window-size=1600,1000", "--window-position=0,0"]
_LANGUAGE = ["--accept-lang=en-GB,en-US,en"]
_ENV = {"LANGUAGE": "en_GB:en", "LANG": "en_GB.UTF-8", "TZ": "Europe/London"}
# A person's Chrome, not a test harness's (see the module docstring). This
# applies everywhere, the desktop included.
_NOT_A_TEST_RUN = ["--disable-blink-features=AutomationControlled"]
_PLAYWRIGHT_TEST_FLAGS = ["--enable-automation"]
# Desktop only: Xwayland, so Chrome starts with no monitor on (module docstring).
_X11 = ["--ozone-platform=x11"]


def in_container(environ=None) -> bool:
    """True inside the Scraper's Docker image (its Dockerfile sets the flag)."""
    return (os.environ if environ is None else environ).get("SCRAPER_IN_CONTAINER") == "1"


def launch_options(headless: bool = False, environ=None) -> dict:
    """launch_persistent_context's options here: the no-screen stand-ins
    (window, language list, locale env) only in the container or headless;
    on the desktop, Chrome's own defaults on the real display. Chrome is
    not handed the site logins (login.py): only this process types them."""
    env = dict(os.environ if environ is None else environ)
    for name in login.env_names():
        env.pop(name, None)
    args = list(_KEEP_TABS_LIVE)
    if in_container(env) or headless:
        args += _WINDOW + _LANGUAGE
        env.update(_ENV)
    else:
        args += _X11
        env.setdefault("TZ", "Europe/London")
    args += _NOT_A_TEST_RUN
    return dict(headless=headless, no_viewport=True, args=args,
                ignore_default_args=_PLAYWRIGHT_TEST_FLAGS, env=env)


def need_display(headless: bool = False, environ=None) -> None:
    """A headed Chrome on the desktop needs the session's display; without one
    Chrome dies with a bare 'Missing X server or $DISPLAY'. Say why instead."""
    env = os.environ if environ is None else environ
    if headless or in_container(env):
        return
    if not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
        raise RuntimeError(
            "no WAYLAND_DISPLAY or DISPLAY: the Scraper runs Chrome on its own "
            "virtual screen. Run it as the user service puntmore-scraper, which "
            "takes its display from puntmore-screen.service.")


def launch(p, settings, headless=False):
    need_display(headless)
    kw = launch_options(headless)
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
  webgl: (() => {
    try {
      const gl = document.createElement('canvas').getContext('webgl');
      if (!gl) return null;
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
                 : gl.getParameter(gl.RENDERER);
    } catch (e) { return null; }
  })(),
  deviceMemory: navigator.deviceMemory === undefined ? null : navigator.deviceMemory,
  cores: navigator.hardwareConcurrency,
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
            if not seen["brands"] or seen.get("deviceMemory") is None:
                # Both exist only on a secure page; this one is.
                secure = page.evaluate(_SEEN)
                seen["brands"] = seen["brands"] or secure["brands"]
                seen["deviceMemory"] = secure["deviceMemory"]
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
        f"  WebGL renderer      {seen.get('webgl') or 'NONE - no WebGL (no GPU, e.g. Xvfb)'}",
        f"  hardware            deviceMemory {seen.get('deviceMemory') or '-'} GB, "
        f"{seen.get('cores') or '-'} cores",
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


# Where a site sends a logged-out request for a tool. Bookie Bashing's is its
# "How it works" page, whose login form is a hidden modal (Oct 2026), so the
# password check alone saw nothing and the saves driver failed on it every
# minute. Only these paths count: a logged-IN tool may redirect too (the bet
# builder lands on /tools/daily/#/calculator), and that is not a logout.
_LOGIN_PATHS = ("/how-it-works", "wp-login.php", "/login")


def logged_out(page, wanted_url: str | None = None) -> bool:
    """A visible password field, or a page on one of the sites' login paths."""
    try:
        if page.locator("input[type=password]:visible").count() > 0:
            return True
        if wanted_url and any(p in _path(page.url) for p in _LOGIN_PATHS):
            return True
    except Exception:
        return False
    return False


def _path(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path.rstrip("/") or "/"


_MIN_COOKIES = ("Reject All", "Reject all", "Necessary only", "Only necessary",
                "Accept necessary", "Necessary cookies only",
                "Allow Necessary Only")   # Paddy's OneTrust banner (Oct 2026)


def dismiss_consent(page):
    """Pick the minimum-cookies option if a consent banner is showing."""
    for label in _MIN_COOKIES:
        btn = page.get_by_role("button", name=label)
        try:
            if btn.count() and btn.first.is_visible():
                click(page, btn.first)
                page.wait_for_timeout(_n("arrive.after_consent_ms"))
                return label
        except Exception:
            continue
    return None


# The waits, steps and distances below are settings in behaviour.py ("Arriving
# on a page", "Clicks", "Reading down a page"), read at each use. Where the
# pointer wanders while looking down a page stays here: it is the window's
# own area, not a setting.
def _n(key) -> int:
    """A whole number drawn from a range setting (ms, steps, px, passes)."""
    return random.randint(*behaviour.get(key))


def _x(key) -> float:
    return random.uniform(*behaviour.get(key))


def settle(page):
    """Arrive like a reader: wait for the page to draw, look down it a little."""
    page.wait_for_timeout(_n("arrive.wait_ms"))
    for _ in range(_n("arrive.passes")):
        page.mouse.move(random.randint(200, 1100), random.randint(200, 800),
                        steps=_n("arrive.move_steps"))
        page.mouse.wheel(0, _n("arrive.scroll_px"))
        page.wait_for_timeout(_n("arrive.pause_ms"))
    page.mouse.wheel(0, -_n("arrive.back_up_px"))


def click(page, loc):
    """Click the way a hand does: bring it into view, move the pointer over
    it, pause, press and release - not a jump to its exact centre."""
    loc.scroll_into_view_if_needed(timeout=5000)
    page.wait_for_timeout(_n("click.before_move_ms"))
    box = loc.bounding_box()
    if not box:
        raise RuntimeError("element has no box")
    x = box["x"] + box["width"] * _x("click.aim_x")
    y = box["y"] + box["height"] * _x("click.aim_y")
    page.mouse.move(x, y, steps=_n("click.move_steps"))
    page.wait_for_timeout(_n("click.before_press_ms"))
    page.mouse.click(x, y, delay=_n("click.press_ms"))


def screenshot(page, path, full_page=False) -> None:
    """Save a PNG of the page by asking Chrome for it directly (the devtools
    Page.captureScreenshot), not through Playwright's page.screenshot: that one
    first raises the tab, and on the Scraper's own screen it then waited 30 s
    on any tab that was not already in front and gave up (12 Nov 2026), with
    the whole loop waiting on it. This takes the picture where the tab is.
    `full_page` takes the whole document, not just what the window shows.
    Raises if Chrome will not give one."""
    import base64
    cdp = page.context.new_cdp_session(page)
    try:
        params = {"format": "png"}
        if full_page:
            size = cdp.send("Page.getLayoutMetrics")["cssContentSize"]
            params.update(captureBeyondViewport=True,
                          clip={"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1})
        data = cdp.send("Page.captureScreenshot", params)["data"]
    finally:
        try:
            cdp.detach()
        except Exception:
            pass
    with open(path, "wb") as f:
        f.write(base64.b64decode(data))


def read_down(page):
    """Look down what a click has just shown, then back up to the top, so
    markets drawn only when they come into view are drawn."""
    for _ in range(_n("read.passes")):
        page.mouse.move(random.randint(250, 1100), random.randint(300, 800),
                        steps=_n("read.move_steps"))
        page.mouse.wheel(0, _n("read.scroll_px"))
        page.wait_for_timeout(_n("read.pause_ms"))
    page.mouse.wheel(0, -_n("read.back_up_px"))
    page.wait_for_timeout(_n("read.back_up_pause_ms"))


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


def visible_tabs(page) -> list[str]:
    """The names on the page's tab bars, as drawn - for a log line when a
    tab was not found. Reads the DOM: costs no request."""
    try:
        names = (page.locator(":is([role=tab], [data-testid=tab])").all_inner_texts()
                 or page.locator(_TAB_BAR).locator(":is(a, button)").all_inner_texts())
    except Exception:
        return []
    return list(dict.fromkeys(" ".join(n.split()) for n in names if n.strip()))[:20]


def wait_for_feed(page, capture, since: float, feed, timeout_s: float = 12.0) -> bool:
    """Wait, up to timeout_s, for the page's own network to answer: True once
    a response whose URL matches `feed` has arrived after `since`."""
    deadline = since + timeout_s
    while time.time() < deadline:
        if any(feed.search(url) for _ts, _k, url, _t in capture.since(since)):
            page.wait_for_timeout(_n("click.after_feed_ms"))   # its prices follow
            return True
        page.wait_for_timeout(500)
    return False
