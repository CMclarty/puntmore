"""bb_presets: a preset priced on the Bookie Bashing builder (Total Odds), read
only, and the `price` request the runner answers with it. The fake builder is
test_bb_saves.py's FAKE, whose presets controls are copied from the 12 Nov 2026
dumps (bb-look-20261112-134300 to -134459)."""
import importlib
import json
import time
from types import SimpleNamespace

import pytest

from scraper import ask, behaviour, config
from scraper.tests.test_bb_saves import _Capture, page  # noqa: F401  (the fixture)

mod = importlib.import_module("scraper.parsers.bb_presets")
saves = importlib.import_module("scraper.parsers.bb_saves")
builder = importlib.import_module("scraper.parsers.bb_builder")

CUP = "English Football League Cup"
EPL = "English Premier League"
BRADFORD = "Bradford v Peterborough - 19:45 tomorrow [C] [S]"
FLEETWOOD = "Fleetwood Town v Arsenal - 20:00 tomorrow"
CHELSEA = "Chelsea v Man Utd - 12:30 31st"
NOTHING_PRESSED = []


@pytest.fixture(autouse=True)
def _fresh():
    mod._STATE.clear()
    builder._STATE.clear()
    yield
    mod._STATE.clear()
    builder._STATE.clear()


def _up(page):  # noqa: F811
    """The builder up, as the bb-look job keeps it (Update Data, Standard View)."""
    builder.bb_builder(page, _Capture())


def _expected(page, value, game):  # noqa: F811
    return page.evaluate("([v, g]) => priceOf(v, g)", [value, game])


def _games_shown(page):  # noqa: F811
    return page.evaluate("[...document.querySelectorAll('.block .game')].map(s => s.value)")


# --- presets -----------------------------------------------------------------
def test_presets_lists_the_use_preset_options_with_whitespace_collapsed(page):  # noqa: F811
    _up(page)
    saves._select_label(saves._field_select(page, "Competition"), "English Football League Cup (8/8)")
    page.wait_for_timeout(300)
    saves._select_label(saves._field_select(page, "Game(s)"), BRADFORD)
    assert mod.presets(page) == [
        "GG", "Home 1,5", "Home 2,6", "Away 2,6", "Away 1,5", "Home 1,5 Away 2 Cards",
        "#WOP - 1+ Goals In Each Half; 4+ Corners In Each Half & 1+ Cards In Each Half",
        "#WOP - Both Teams to Score & 3+ Corners for Each Team & 3+ Cards for Each Team",
        "#WOP - Both Teams to Score & 3+ Corners for Each Team & 3+ Cards for Each Team",
        "Player Foul & Foul Won"]
    assert page.evaluate("TOGGLES") == 1
    assert mod.presets(page) != [] and page.evaluate("TOGGLES") == 1      # open: not clicked again
    assert page.evaluate("LOADED") == [] and page.evaluate("PRESSED") == NOTHING_PRESSED


def test_presets_with_no_game_chosen_says_the_panel_is_not_there(page, monkeypatch):  # noqa: F811
    monkeypatch.setattr(mod, "PANEL_WAIT_MS", 300)
    _up(page)
    with pytest.raises(saves.BuilderNotReady, match="Show/Hide Presets"):
        mod.presets(page)


# --- price ---------------------------------------------------------------------
def test_price_reads_total_odds_for_a_game_and_a_preset(page):  # noqa: F811
    _up(page)
    fair = mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    assert fair == _expected(page, "user4", BRADFORD)
    assert page.evaluate("LOADED") == ["user4"]
    assert _games_shown(page) == [BRADFORD, BRADFORD]              # the preset kept the game
    assert page.evaluate("PRESSED") == NOTHING_PRESSED             # nothing that changes the account
    assert page.locator("#betname").input_value() == ""            # the tracker panel untouched


def test_a_second_item_on_the_same_game_does_not_reselect_it(page):  # noqa: F811
    _up(page)
    a = mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    b = mod.price(page, CUP, "Bradford", "Peterborough", "Away 2,6")
    assert (a, b) == (_expected(page, "user4", BRADFORD), _expected(page, "user5", BRADFORD))
    assert page.evaluate("VISITED").count(BRADFORD) == 1
    assert page.evaluate("TOGGLES") == 1
    c = mod.price(page, CUP, "Fleetwood Town", "Arsenal", "Home 1,5 Away 2 Cards")
    assert c == _expected(page, "user9", FLEETWOOD)
    assert _games_shown(page) == [FLEETWOOD] * 3
    assert page.evaluate("PRESSED") == NOTHING_PRESSED


def test_the_old_total_is_never_read_as_the_new_presets(page):  # noqa: F811
    """Total Odds keeps the last preset's value until the new one lands (as on
    12 Nov 2026: 2.32 stood until the preset's price came)."""
    _up(page)
    page.evaluate("SETTLE_MS = 1500")
    a = mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    b = mod.price(page, CUP, "Bradford", "Peterborough", "Away 1,5")
    assert a != b and b == _expected(page, "user6", BRADFORD)


def test_the_same_preset_again_on_the_same_game_is_read_where_it_is(page):  # noqa: F811
    _up(page)
    a = mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    assert mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6") == a
    assert page.evaluate("LOADED") == ["user4"]


def test_names_match_after_collapsing_whitespace_and_teams_after_normalising(page):  # noqa: F811
    _up(page)
    fair = mod.price(page, "english football league cup", " bradford ", "PETERBOROUGH",
                     "#WOP -  1+ Goals In Each Half; 4+ Corners In Each Half & 1+ Cards In Each Half")
    assert fair == _expected(page, "glob6", BRADFORD)
    mod.price(page, CUP, "Bradford", "Peterborough", "Player Foul & Foul Won")
    assert page.evaluate("LOADED") == ["glob6", "glob445"]


def test_a_name_twice_in_the_list_loads_the_first(page):  # noqa: F811
    _up(page)
    mod.price(page, CUP, "Bradford", "Peterborough",
              "#WOP - Both Teams to Score & 3+ Corners for Each Team & 3+ Cards for Each Team")
    assert page.evaluate("LOADED") == ["glob172"]


@pytest.mark.parametrize("comp, home, away", [(CUP, "Bradford", "Hull"),             # not listed
                                              (CUP, "Peterborough", "Bradford"),     # home and away swapped
                                              ("Spanish La Liga", "Bradford", "Peterborough"),
                                              (EPL, "Bradford", "Peterborough")])   # another competition
def test_an_unknown_game_is_no_game(page, comp, home, away):  # noqa: F811
    _up(page)
    with pytest.raises(mod.NoGame):
        mod.price(page, comp, home, away, "Home 2,6")
    assert page.evaluate("LOADED") == []


def test_an_unknown_preset_is_no_preset(page):  # noqa: F811
    _up(page)
    with pytest.raises(mod.NoPreset):
        mod.price(page, CUP, "Bradford", "Peterborough", "Home 9,9")
    with pytest.raises(mod.NoPreset):
        mod.price(page, CUP, "Bradford", "Peterborough", "home 2,6")       # case is the name's
    assert page.evaluate("LOADED") == []


def _two_leagues(page):  # noqa: F811
    page.evaluate("""() => { GAMES = {
        "English Premier League (2/2)": ["all (2) - 19:45 tomorrow", "Leeds v Hull - 19:45 tomorrow", "Bath v Wells - 20:00 tomorrow"],
        "English Premier League 2 (1/1)": ["all (1) - 12:00 tomorrow", "Leeds v Hull - 12:00 tomorrow"],
        "English Premier League Cup (1/1)": ["all (1) - 15:00 tomorrow", "Leeds v Hull - 15:00 tomorrow"]}; }""")


def test_the_named_competition_chooses_between_two_games_with_the_same_teams(page):  # noqa: F811
    _two_leagues(page)                                  # before the builder draws its Competition list
    _up(page)
    mod.price(page, "English Premier League 2", "Leeds", "Hull", "Home 2,6")
    assert _games_shown(page) == ["Leeds v Hull - 12:00 tomorrow"] * 2
    mod.price(page, EPL, "Leeds", "Hull", "Home 2,6")
    assert _games_shown(page) == ["Leeds v Hull - 19:45 tomorrow"] * 2


def test_still_ambiguous_is_no_game(page):  # noqa: F811
    _two_leagues(page)
    _up(page)
    with pytest.raises(mod.NoGame, match="more than once"):
        mod.price(page, "English Premier", "Leeds", "Hull", "Home 2,6")        # names three
    assert page.evaluate("LOADED") == []


@pytest.mark.parametrize("settle", ["infinity", "flicker"])
def test_total_odds_that_never_settles_is_none_within_the_wait(page, settle):  # noqa: F811
    _up(page)
    mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")       # the panel open
    page.evaluate(f"SETTLE = '{settle}'")
    t0 = time.time()
    assert mod.price(page, CUP, "Bradford", "Peterborough", "Away 2,6") is None
    assert time.time() - t0 < mod.SETTLE_WAIT_S + 3
    assert mod._STATE[id(page)].get("loaded") is None                  # not taken as loaded


def test_a_preset_that_leaves_the_game_is_not_priced(page):  # noqa: F811
    _up(page)
    page.evaluate("KEEPS_GAME = false")
    with pytest.raises(mod.Unpriced, match="not on Bradford v Peterborough"):
        mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")


def test_a_builder_that_is_not_up_says_so(page):  # noqa: F811
    with pytest.raises(saves.BuilderNotReady):
        mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")


def test_it_is_not_a_parser_a_job_could_name():
    from scraper.parsers import PARSERS
    assert "bb_presets" not in PARSERS


# --- the price request, answered by the runner on bb-look's open page -------------
def _bb_look(name="bb-look", parser="bb_builder"):
    return config.Target(name=name, site="bookiebashing", url="https://www.bookiebashing.net/tools/betbuilder/",
                         parser=parser)


def _runner(tmp_path, targets):
    from scraper.alerts import Alerts
    from scraper.guard import Guard
    from scraper.runner import Runner
    s = config.Settings(data_dir=tmp_path, profile_dir=tmp_path / "profile", channel=None,
                        alert_webhook=None, healthcheck_url=None, capture_mb=1, targets=list(targets))
    g = Guard(tmp_path / "g.sqlite3")
    g.quiet = (0, 0)
    return Runner(s, None, g, Alerts(tmp_path / "alerts.log", None), tmp_path / "samples")


def _drop(tmp_path, body):
    folder = tmp_path / "requests"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"ask-{body['id']}.json").write_text(json.dumps(body))


def _item(key, home, away, preset, comp=CUP):
    return {"key": key, "competition": comp, "home": home, "away": away, "preset": preset}


def test_the_runner_prices_a_request_on_the_jobs_open_page(tmp_path, page):  # noqa: F811
    t = _bb_look()
    r = _runner(tmp_path, [t])
    cap = _Capture()
    r.tabs["bb-look"] = SimpleNamespace(target=t, page=page, capture=cap, url=t.url)
    _drop(tmp_path, {"id": "p1", "kind": "price", "job": "bb-look", "save": "no", "items": [
        _item("a", "Bradford", "Peterborough", "Home 2,6"),
        _item("b", "Bradford", "Peterborough", "Home 9,9"),
        _item("c", "Leeds", "Hull", "Home 2,6"),
        _item("d", "Fleetwood Town", "Arsenal", "Away  2,6")]})
    for i in range(10):
        r._requests(now=1_000.0 + i)
        if r.asking is None:
            break
    body = ask.read_result(tmp_path, "p1")
    assert (body["kind"], body["state"], body["reason"]) == ("price", "done", None)
    got = [(x["key"], x["state"], x["fair"], x["saved"], x["preset"]) for x in body["items"]]
    assert got == [("a", "priced", _expected(page, "user4", BRADFORD), "no", "Home 2,6"),
                   ("b", "no_preset", None, "no", "Home 9,9"),
                   ("c", "no_game", None, "no", "Home 2,6"),
                   ("d", "priced", _expected(page, "user5", FLEETWOOD), "no", "Away  2,6")]
    assert body["items"][2]["reason"]
    assert page.evaluate("PRESSED") == NOTHING_PRESSED
    assert cap.last_activity > 0
    assert r.guard.requested("bookiebashing", 1_000.0) == (0, 0)   # nothing loaded
    assert "p1: done" in (tmp_path / "logs" / "ask.log").read_text()


def test_unsettled_on_the_runner(tmp_path, page):  # noqa: F811
    t = _bb_look()
    r = _runner(tmp_path, [t])
    r.tabs["bb-look"] = SimpleNamespace(target=t, page=page, capture=_Capture(), url=t.url)
    page.evaluate("SETTLE = 'infinity'")
    _drop(tmp_path, {"id": "p2", "kind": "price", "job": "bb-look",
                     "items": [_item("a", "Bradford", "Peterborough", "Home 2,6")]})
    r._requests(now=1_000.0)
    (item,) = ask.read_result(tmp_path, "p2")["items"]
    assert (item["state"], item["fair"], item["reason"]) == ("unsettled", None, "Total Odds did not settle")


def test_a_logged_out_builder_stops_the_request(tmp_path, page):  # noqa: F811
    t = _bb_look()
    r = _runner(tmp_path, [t])
    page.set_content("<html><body><h1>How it works</h1></body></html>")
    r.tabs["bb-look"] = SimpleNamespace(target=t, page=page, capture=_Capture(), url=t.url)
    _drop(tmp_path, {"id": "p3", "kind": "price", "job": "bb-look",
                     "items": [_item("a", "Bradford", "Peterborough", "Home 2,6")]})
    r._requests(now=1_000.0)
    body = ask.read_result(tmp_path, "p3")
    assert (body["state"], body["items"]) == ("stopped", [])
    assert body["reason"] == "builder not ready: not the bet builder page - logged out of Bookie Bashing?"
