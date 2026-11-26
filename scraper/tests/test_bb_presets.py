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
def _desc(page):  # noqa: F811
    return page.locator("#desc .message-body").inner_text()


def test_price_reads_total_odds_for_a_game_and_a_preset(page):  # noqa: F811
    _up(page)
    page.evaluate("UPDATED_AT = Date.now() - 113000")
    got = mod.price_reading(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    assert got.fair == _expected(page, "user4", BRADFORD)
    assert got.description == _desc(page) == "All of Bradford (user4 leg 1) and Bradford (user4 leg 2)"
    assert 113 <= got.data_age_s <= 125 and got.reason is None
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


def test_a_wrong_total_shown_for_900_ms_is_never_taken(page):  # noqa: F811
    """Total Odds and the description must hold for a whole second: a number
    the builder shows on the way to the right one is not the fair."""
    _up(page)
    page.evaluate("SETTLE = 'intermediate'")
    assert mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6") == _expected(page, "user4", BRADFORD)
    assert mod.price(page, CUP, "Bradford", "Peterborough", "Away 2,6") == _expected(page, "user5", BRADFORD)


def test_the_same_preset_again_on_the_same_game_is_unsettled(page):  # noqa: F811
    """No shortcut: a preset already chosen cannot be shown to apply afresh."""
    _up(page)
    mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    got = mod.price_reading(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    assert got.fair is None and "already chosen" in got.reason


@pytest.mark.parametrize("reapply", [True, False])
def test_the_same_preset_on_the_next_game_is_never_the_last_games_number(page, reapply):  # noqa: F811
    """Use Preset has no empty option (12 Nov 2026 dump), so the option already
    chosen cannot be gone through again: the item is unsettled, whether or not
    choosing it again would re-apply it (REAPPLY)."""
    _up(page)
    page.evaluate(f"REAPPLY = {'true' if reapply else 'false'}")
    a = mod.price(page, CUP, "Bradford", "Peterborough", "GG")             # one block
    assert a == _expected(page, "user0", BRADFORD)
    got = mod.price_reading(page, CUP, "Fleetwood Town", "Arsenal", "GG")
    assert got.fair is None and "already chosen" in got.reason
    assert got.fair != a


def test_a_description_that_still_names_the_last_games_team_is_not_taken(page):  # noqa: F811
    """A preset chosen after the game moved, whose second block stayed on the
    last game: its description names that game's team, so no fair."""
    _up(page)
    page.evaluate("KEEPS_GAME = 'old'")
    mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    got = mod.price_reading(page, CUP, "Fleetwood Town", "Arsenal", "Away 2,6")
    assert got.fair is None
    assert got.reason == "the description still names Peterborough from the last game"


def test_names_match_after_collapsing_whitespace_and_teams_after_normalising(page):  # noqa: F811
    _up(page)
    fair = mod.price(page, "english football league cup", " bradford ", "PETERBOROUGH",
                     "#WOP -  1+ Goals In Each Half; 4+ Corners In Each Half & 1+ Cards In Each Half")
    assert fair == _expected(page, "glob6", BRADFORD)
    mod.price(page, CUP, "Bradford", "Peterborough", "Player Foul & Foul Won")
    assert page.evaluate("LOADED") == ["glob6", "glob445"]


def test_a_name_listed_twice_is_no_preset(page):  # noqa: F811
    """Two options share the name (and price differently): neither is chosen."""
    _up(page)
    page.evaluate(f"PRICES = {{'glob172|{BRADFORD}': 3.1, 'glob251|{BRADFORD}': 4.4}}")
    with pytest.raises(mod.NoPreset, match="listed 2 times"):
        mod.price(page, CUP, "Bradford", "Peterborough",
                  "#WOP - Both Teams to Score &  3+ Corners for Each Team & 3+ Cards for Each Team")
    assert page.evaluate("LOADED") == []


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
    mod.price(page, EPL, "Leeds", "Hull", "Away 2,6")
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
    got = mod.price_reading(page, CUP, "Bradford", "Peterborough", "Away 2,6")
    assert got.fair is None
    assert got.reason == {"infinity": "Total Odds shows no price",
                          "flicker": "Total Odds and the description did not hold for 1.0 s"}[settle]
    assert time.time() - t0 < mod.SETTLE_WAIT_S + 3


def test_a_preset_that_leaves_the_game_is_not_priced(page):  # noqa: F811
    _up(page)
    page.evaluate("KEEPS_GAME = false")
    with pytest.raises(mod.Unpriced, match="not on Bradford v Peterborough"):
        mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")


def test_a_builder_that_is_not_up_says_so(page):  # noqa: F811
    with pytest.raises(saves.BuilderNotReady):
        mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")


def test_a_data_pull_forgets_what_the_driver_knew_of_the_page(page):  # noqa: F811
    _up(page)
    mod.price(page, CUP, "Bradford", "Peterborough", "Home 2,6")
    assert id(page) in mod._STATE
    assert mod.up(page, time.time()) is False                          # not due: nothing pulled
    assert id(page) in mod._STATE
    builder._STATE[id(page)]["updated"] -= behaviour.get("bb.update_every_s")
    assert mod.up(page, time.time()) is True
    assert id(page) not in mod._STATE
    assert page.evaluate("UPDATES") == 2


def test_data_age_is_read_off_the_page(page):  # noqa: F811
    _up(page)
    page.evaluate("UPDATED_AT = Date.now() - 300000")
    page.wait_for_timeout(400)
    assert 300 <= mod.data_age(page) <= 305
    page.set_content("<html><body><h1>How it works</h1></body></html>")
    assert mod.data_age(page) is None                                  # no builder: no age


def test_list_presets_with_no_game_chosen_chooses_the_first_listed(page):  # noqa: F811
    _up(page)
    listed, came_from = mod.list_presets(page)
    assert came_from == f"{BRADFORD} ({CUP} (8/8)), chosen to open the panel: no game was chosen"
    assert listed[:2] == [{"name": "GG", "count": 1}, {"name": "Home 1,5", "count": 1}]
    assert {"name": "#WOP - Both Teams to Score & 3+ Corners for Each Team & 3+ Cards for Each Team",
            "count": 2} in listed
    assert len(listed) == 9 and page.evaluate("LOADED") == []
    assert mod.list_presets(page)[1] == f"{BRADFORD} (already chosen)"
    assert page.evaluate("PRESSED") == NOTHING_PRESSED


def test_the_chosen_preset_is_read(page):  # noqa: F811
    _up(page)
    assert mod.chosen_preset(page) is None
    mod.price(page, CUP, "Bradford", "Peterborough", "Player Foul & Foul Won")
    assert mod.chosen_preset(page) == "Player Foul & Foul Won"


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
    assert body["items"][0]["description"] == "All of Bradford (user4 leg 1) and Bradford (user4 leg 2)"
    assert isinstance(body["items"][0]["data_age_s"], int)
    assert (body["presets"], body["presets_from"]) == (None, None)
    # c's preset is the one just chosen, so d goes first (_ask_price_next)
    assert got == [("a", "priced", _expected(page, "user4", BRADFORD), "no", "Home 2,6"),
                   ("b", "no_preset", None, "no", "Home 9,9"),
                   ("d", "priced", _expected(page, "user5", FLEETWOOD), "no", "Away  2,6"),
                   ("c", "no_game", None, "no", "Home 2,6")]
    assert body["items"][3]["reason"]
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
    assert (item["state"], item["fair"], item["reason"]) == ("unsettled", None, "Total Odds shows no price")


def test_the_runner_lists_presets_with_no_items(tmp_path, page):  # noqa: F811
    t = _bb_look()
    r = _runner(tmp_path, [t])
    r.tabs["bb-look"] = SimpleNamespace(target=t, page=page, capture=_Capture(), url=t.url)
    _drop(tmp_path, {"id": "l1", "kind": "price", "job": "bb-look", "items": [], "list_presets": True})
    r._requests(now=1_000.0)
    body = ask.read_result(tmp_path, "l1")
    assert (body["state"], body["items"]) == ("done", [])
    assert {"name": "Home 2,6", "count": 1} in body["presets"] and len(body["presets"]) == 9
    assert body["presets_from"].startswith(BRADFORD)
    assert page.evaluate("LOADED") == [] and page.evaluate("PRESSED") == NOTHING_PRESSED


def test_the_runner_does_not_take_next_an_item_whose_preset_is_already_chosen(tmp_path, page):  # noqa: F811
    """GG on one game, then GG on another would be unsettled (already chosen):
    another item goes between them. Each item keeps its key."""
    t = _bb_look()
    r = _runner(tmp_path, [t])
    r.tabs["bb-look"] = SimpleNamespace(target=t, page=page, capture=_Capture(), url=t.url)
    _drop(tmp_path, {"id": "o1", "kind": "price", "job": "bb-look", "items": [
        _item("a", "Bradford", "Peterborough", "GG"), _item("b", "Fleetwood Town", "Arsenal", "GG"),
        _item("c", "Fleetwood Town", "Arsenal", "Home 2,6")]})
    for i in range(10):
        r._requests(now=1_000.0 + i)
        if r.asking is None:
            break
    body = ask.read_result(tmp_path, "o1")
    assert [(x["key"], x["state"]) for x in body["items"]] == [("a", "priced"), ("c", "priced"), ("b", "priced")]
    assert body["items"][2]["fair"] == _expected(page, "user0", FLEETWOOD)


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
