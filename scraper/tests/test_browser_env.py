"""Where Chrome runs decides what is set by hand (browser.launch_options).

On the Hub's desktop Chrome keeps its own defaults on the real display; only
in the container (SCRAPER_IN_CONTAINER=1, the Dockerfile) or headless do the
no-screen stand-ins apply. The one deliberate departure (no
--enable-automation, AutomationControlled off) applies everywhere."""
import pytest

from scraper import browser

DESKTOP = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "LANG": "en_GB.UTF-8"}
CONTAINER = {"SCRAPER_IN_CONTAINER": "1", "DISPLAY": ":99", "LANG": "C"}


def _flags(kw):
    return {a.split("=")[0] for a in kw["args"]}


def test_container_flag():
    assert browser.in_container(CONTAINER)
    assert not browser.in_container(DESKTOP)
    assert not browser.in_container({"SCRAPER_IN_CONTAINER": "0"})


def test_desktop_launch_is_chromes_own_defaults():
    kw = browser.launch_options(headless=False, environ=DESKTOP)
    assert not {"--window-size", "--window-position", "--accept-lang"} & _flags(kw)
    assert "--ozone-platform=x11" in kw["args"]           # Xwayland: starts with no monitor on
    assert "--no-sandbox" not in _flags(kw)
    assert kw["env"]["LANG"] == "en_GB.UTF-8"             # the session's own, untouched
    assert kw["env"]["WAYLAND_DISPLAY"] == "wayland-0"
    assert kw["env"]["TZ"] == "Europe/London"
    assert kw["headless"] is False and kw["no_viewport"] is True


@pytest.mark.parametrize("environ,headless", [(CONTAINER, False), (DESKTOP, True)])
def test_no_screen_gets_the_stand_ins(environ, headless):
    kw = browser.launch_options(headless=headless, environ=environ)
    assert "--window-size=1600,1000" in kw["args"]
    assert "--accept-lang=en-GB,en-US,en" in kw["args"]
    assert "--ozone-platform" not in _flags(kw)           # no Wayland session to work round
    assert kw["env"]["LANG"] == "en_GB.UTF-8" and kw["env"]["TZ"] == "Europe/London"


@pytest.mark.parametrize("environ,headless", [(DESKTOP, False), (CONTAINER, False), (DESKTOP, True)])
def test_a_persons_chrome_everywhere(environ, headless):
    kw = browser.launch_options(headless=headless, environ=environ)
    assert "--disable-blink-features=AutomationControlled" in kw["args"]
    assert kw["ignore_default_args"] == ["--enable-automation"]
    assert "--no-sandbox" not in _flags(kw)


def test_desktop_without_a_display_says_why():
    with pytest.raises(RuntimeError, match="WAYLAND_DISPLAY or DISPLAY"):
        browser.need_display(False, {"LANG": "en_GB.UTF-8"})
    browser.need_display(False, DESKTOP)
    browser.need_display(False, {"DISPLAY": ":0"})
    browser.need_display(True, {})                        # headless needs none
    browser.need_display(False, {"SCRAPER_IN_CONTAINER": "1"})  # Xvfb is the entrypoint's


def test_selfcheck_lines_show_the_webgl_renderer():
    seen = dict(userAgent="UA", brands=[], webdriver=False, language="en-GB",
                languages=["en-GB"], timezone="Europe/London", utcOffsetMin=0,
                inner=[1, 1], outer=[1, 1], screen=[1, 1], dpr=1, version="157",
                enable_automation=False, automation_controlled_disabled=True,
                command_line_read=True, deviceMemory=8, cores=32,
                webgl="ANGLE (AMD, AMD Radeon 8060S Graphics, OpenGL ES 3.2)")
    lines = browser.selfcheck_lines(seen, "test")
    assert any("WebGL renderer" in l and "Radeon 8060S" in l for l in lines)
    seen["webgl"] = None
    assert any("WebGL renderer" in l and "NONE" in l for l in browser.selfcheck_lines(seen, "test"))
    del seen["webgl"], seen["deviceMemory"]        # a selfcheck.json from before this line
    assert any("WebGL renderer" in l for l in browser.selfcheck_lines(seen, "test"))
