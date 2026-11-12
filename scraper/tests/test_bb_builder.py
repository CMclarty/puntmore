"""The bb_builder job: brings the Bookie Bashing bet builder up and keeps it
up, so it can be looked at (`python -m scraper look`). The fake builder page
is test_bb_saves.py's."""
import importlib

import pytest

from scraper import behaviour
from scraper.parsers import PARSERS
from scraper.tests.test_bb_saves import _Capture, page  # noqa: F401  (the fixture)

mod = importlib.import_module("scraper.parsers.bb_builder")
saves = importlib.import_module("scraper.parsers.bb_saves")


@pytest.fixture(autouse=True)
def _fresh():
    mod._STATE.clear()
    yield
    mod._STATE.clear()


def test_it_is_a_parser():
    assert PARSERS["bb_builder"] is mod.bb_builder


def test_it_brings_the_builder_up_returns_no_rows_and_marks_the_page_active(page):  # noqa: F811
    cap = _Capture()
    assert mod.bb_builder(page, cap) == []
    assert page.locator("#app").is_visible()                # Standard View is up
    assert page.evaluate("UPDATES") == 1
    assert cap.last_activity > 0
    assert page.evaluate("SUB_SET") is None                 # no stat chosen, no game read
    assert page.evaluate("VISITED") == []
    assert mod.bb_builder(page, cap) == []                  # up: no second pull
    assert page.evaluate("UPDATES") == 1


def test_it_pulls_the_data_again_every_update_interval(page):  # noqa: F811
    mod.bb_builder(page, _Capture())
    mod._STATE[id(page)]["updated"] -= behaviour.get("bb.update_every_s")
    mod.bb_builder(page, _Capture())
    assert page.evaluate("UPDATES") == 2


def test_it_never_rests_however_far_off_the_games_are(page):  # noqa: F811
    page.evaluate("GAMES = {}; PLAYERS = {}")
    assert mod.bb_builder(page, _Capture()) == []           # bb_saves would raise Rest here


def test_a_logged_out_page_says_so(page):  # noqa: F811
    page.set_content("<html><body><h1>How it works</h1></body></html>")
    with pytest.raises(saves.BuilderNotReady, match="logged out"):
        mod.bb_builder(page, _Capture())


def test_a_builder_that_never_renders_raises(page, monkeypatch):  # noqa: F811
    page.evaluate("document.getElementById('upd').onclick = null")
    monkeypatch.setattr(saves, "READY_TIMEOUT_MS", 800)
    with pytest.raises(saves.BuilderNotReady, match="did not render"):
        mod.bb_builder(page, _Capture())


def test_it_shares_the_bring_up_with_bb_saves():
    assert mod.bring_up is saves.bring_up
