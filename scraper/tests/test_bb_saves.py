"""The Bookie Bashing bet-builder driver against a fake builder page: the same
control structure (button + select fields, Update Data -> Standard View,
Combine Players/Games, Total Odds), a toy model in place of BB's.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import importlib

# The package re-exports the FUNCTION bb_saves, which shadows the module name.
mod = importlib.import_module("scraper.parsers.bb_saves")
parse_game_label = mod.parse_game_label

LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 10, 26, 17, 0, tzinfo=LONDON)       # a Sunday, BST over (UTC+0)


def test_game_labels_resolve_to_teams_and_a_utc_kickoff():
    g = parse_game_label("Bradford v Peterborough - 19:45 tomorrow [C] [S]", NOW)
    assert g == {"home": "Bradford", "away": "Peterborough", "kickoff": "2026-10-27T19:45:00Z"}
    g = parse_game_label("Chelsea v Man Utd - 12:30 31st  ", NOW)
    assert g["kickoff"] == "2026-10-31T12:30:00Z"
    g = parse_game_label("Liverpool v Arsenal - 16:30 1st", NOW)
    assert g["kickoff"] == "2026-11-01T16:30:00Z"               # next month's 1st
    g = parse_game_label("Paris St-G v Lyon - 19:30 [C] [S]", NOW)
    assert g["kickoff"] == "2026-10-26T19:30:00Z"               # today


def test_group_rows_are_not_games():
    assert parse_game_label("all (8) - 19:45 tomorrow", NOW) is None
    assert parse_game_label("Wed (5) - 19:30 28th", NOW) is None
    assert parse_game_label("7 Nov (4) - 12:30 7th", NOW) is None


# A field is "button + select", as the builder draws them. Two blocks after
# "Combine Players/Games". Total Odds = a toy model of the chosen line.
# Knobs, set through page.evaluate before the first call: GAMES and PLAYERS
# (the games listed, labelled relative to the test's `now`), XSAVES_AFTER (the
# Update Data click from which BB xSaves is in the stats list, as on 11 Nov
# 2026 when one data pull brought it and another did not). It records
# UPDATES (Update Data clicks), VISITED (games chosen) and SUB_SET (a stat
# chosen in the stats list).
FAKE = """<html><body>
<button id=upd>Update Data</button>
<div id=app style="display:none">
<button>Standard View</button>
<select id=stat><option>BB xG</option><option>BB Player/Team Stats</option></select>
<select id=sub></select>
<div id=blocks></div>
<button id=combine>Combine Players/Games</button>
<div class=field><div class=control><select><option value=over>Over</option><option value=under>Under</option></select></div>
<div class=control><input type=number step=1 id=line></div><div class=control><button>saves</button></div></div>
<span id=modebox></span>
<div class=field><div class=control><button>Total Odds</button></div><div class=control><button id=total>Infinity</button></div></div>
</div>
<script>
var UPDATES = 0, XSAVES_AFTER = 1, VISITED = [], SUB_SET = null;
const STATS = ["BB xSOT", "BB xShots", "BB xAssists", "BB xTackles", "BB xFouls", "BB xFouls Won",
               "BB xOffsides", "BB xThrow Ins", "BB xPenalties Awarded", "BB xPenalties Scored",
               "BB Player xCards", "BB xScoreOrAssist"];
var GAMES = {"English Football League Cup (8/8)": ["all (2) - 19:45 tomorrow", "Bradford v Peterborough - 19:45 tomorrow [C] [S]", "Fleetwood Town v Arsenal - 20:00 tomorrow"],
               "English Premier League (21/21)": ["all (2) - 20:00 27th", "Everton v Leeds - 20:00 27th", "Chelsea v Man Utd - 12:30 31st"]};
var PLAYERS = {"Bradford v Peterborough - 19:45 tomorrow [C] [S]": ["Bradford", "Peterborough"],
                 "Fleetwood Town v Arsenal - 20:00 tomorrow": ["Arsenal", "Fleetwood Town"],
                 "Everton v Leeds - 20:00 27th": [],
                 "Chelsea v Man Utd - 12:30 31st": ["Chelsea", "Man Utd"]};
function block(i) {
  const d = document.createElement('div'); d.className = 'block';
  d.innerHTML = `<div class=field><div class=control><button>Competition</button></div><div class=control><select class=comp><option>Select Competition</option>${Object.keys(GAMES).map(c => '<option>' + c + '</option>').join('')}</select></div></div>
  <div class=field><div class=control><button><span>Game(s)</span></button></div><div class=control><select class=game></select></div></div>
  <div class=field><div class=control><button>Player</button></div><div class=control><select class=player></select></div></div>`;
  const comp = d.querySelector('.comp'), game = d.querySelector('.game'), player = d.querySelector('.player');
  comp.onchange = () => { game.innerHTML = (GAMES[comp.value] || []).map(g => '<option>' + g + '</option>').join(''); game.onchange(); };
  game.onchange = () => { if (game.value) VISITED.push(game.value); player.innerHTML = (PLAYERS[game.value] || []).map(p => '<option>' + p + '</option>').join(''); recalc(); };
  player.onchange = recalc;
  document.getElementById('blocks').appendChild(d);
}
function recalc() {
  const blocks = [...document.querySelectorAll('.block')];
  const players = blocks.map(b => b.querySelector('.player').value).filter(Boolean);
  const line = parseFloat(document.getElementById('line').value);
  const modeSel = document.getElementById('mode');
  const mode = modeSel ? modeSel.value : 'Each Player';
  const ok = players.length === blocks.length && players.length > 0 && !isNaN(line);
  const total = mode === 'Total Over Players' ? players.length : 1;
  document.getElementById('total').textContent = ok ? (1 + line * 0.3 / total).toFixed(2) : 'Infinity';
}
document.getElementById('upd').onclick = () => {
  UPDATES += 1;
  setTimeout(() => {
    const stats = STATS.slice();
    if (UPDATES >= XSAVES_AFTER) stats.splice(stats.indexOf("BB xScoreOrAssist"), 0, "BB xSaves");
    document.getElementById('sub').innerHTML = stats.map(x => '<option>' + x + '</option>').join('');
    document.getElementById('app').style.display = '';
    if (!document.querySelector('.block')) block(0);
  }, 300);
};
document.getElementById('sub').onchange = () => { SUB_SET = document.getElementById('sub').value; };
// As the real builder: a second block, and the mode control, only once the
// first block has a team chosen.
document.getElementById('combine').onclick = () => {
  if (!document.querySelector('.block .player').value || document.querySelectorAll('.block').length > 1) return;
  block(1);
  document.getElementById('modebox').innerHTML = '<select id=mode><option>Each Player</option><option>Total Over Players</option></select>';
  document.getElementById('mode').onchange = recalc;
};
document.getElementById('line').oninput = recalc;
</script></body></html>"""


class _Capture:
    last_activity = 0.0


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    """The parser's clock is NOW (labels like "19:45 tomorrow" resolve from it);
    no page's state is left over from another test."""
    monkeypatch.setattr(mod, "_now", lambda: NOW)
    mod._STATE.clear()
    mod._XSAVES.update(pulls=0, last=0.0)
    yield
    mod._STATE.clear()
    mod._XSAVES.update(pulls=0, last=0.0)


@pytest.fixture
def page():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = {"headless": True}
        if os.environ.get("SCRAPER_CHROME_PATH"):
            kw["executable_path"] = os.environ["SCRAPER_CHROME_PATH"]
        browser = p.chromium.launch(**kw)
        pg = browser.new_page()
        pg.set_content(FAKE)
        yield pg
        browser.close()


def test_drives_the_builder_and_reads_match_total_fairs(page, monkeypatch):
    monkeypatch.setattr(mod, "COMPETITIONS", ("English Football League Cup", "English Premier League"))
    monkeypatch.setattr(mod, "LINES", (3.5, 4.5))
    cap = _Capture()
    rows = mod.bb_saves(page, cap)
    assert cap.last_activity > 0
    by = {(r["home"], r["line"]): r for r in rows}
    # Two teams combined, "Total Over Players": the toy model halves the slope.
    assert by[("Bradford", 3.5)]["over"] == pytest.approx(1 + 3.5 * 0.3 / 2, abs=0.01)
    assert by[("Bradford", 4.5)]["over"] == pytest.approx(1 + 4.5 * 0.3 / 2, abs=0.01)
    assert by[("Fleetwood Town", 3.5)]["away"] == "Arsenal"
    assert all(r["kickoff"].endswith("Z") for r in rows)
    # Everton v Leeds has no saves model (empty Player list): no rows, no error.
    assert not any(r["home"] == "Everton" for r in rows)
    # Chelsea v Man Utd (31 Oct) is more than 48 hours off: never chosen.
    assert not any(r["home"] == "Chelsea" for r in rows)
    assert not [g for g in page.evaluate("VISITED") if g.startswith("Chelsea")]
    assert len(rows) == 4
    # The pass is complete; the next call starts a new one without re-updating.
    assert mod._STATE[id(page)]["queue"] == []
    rows2 = mod.bb_saves(page, cap)
    assert len(rows2) == 4


def test_a_builder_that_never_renders_raises(page, monkeypatch):
    page.evaluate("document.getElementById('upd').onclick = null")
    monkeypatch.setattr(mod, "READY_TIMEOUT_MS", 800)
    mod._STATE.pop(id(page), None)
    with pytest.raises(Exception):
        mod.bb_saves(page, _Capture())


# --- the 48-hour rule and a missing BB xSaves (11 Nov 2026) -------------------
def _games(page, games: dict, players: dict | None = None, xsaves_after: int = 1):
    page.evaluate("([g, p, x]) => { GAMES = g; PLAYERS = p; XSAVES_AFTER = x; }",
                  [games, players or {}, xsaves_after])


def _only_epl(monkeypatch):
    monkeypatch.setattr(mod, "COMPETITIONS", ("English Premier League",))
    monkeypatch.setattr(mod, "LINES", (3.5,))


def test_only_games_kicking_off_within_48_hours_are_read(page, monkeypatch):
    _only_epl(monkeypatch)
    games = ["all (4) - 16:00",
             "Spurs v Wolves - 16:00",                  # kicked off an hour ago
             "Paris v Lyon - 19:30",                    # today, 2.5 h
             "Leeds v Hull - 16:30 28th",               # 47.5 h
             "Bath v Wells - 17:30 28th"]               # 48.5 h
    _games(page, {"English Premier League (4/4)": games},
           {g: ["H", "A"] for g in games if " v " in g})
    rows = mod.bb_saves(page, _Capture())
    assert sorted({r["home"] for r in rows}) == ["Leeds", "Paris"]
    visited = set(page.evaluate("VISITED"))
    assert not any(g.startswith(("Spurs", "Bath")) for g in visited)


def test_no_game_within_48_hours_rests_until_48_hours_before_the_next(page, monkeypatch):
    _only_epl(monkeypatch)
    _games(page, {"English Premier League (1/1)": ["all (1) - 12:30 29th", "Chelsea v Man Utd - 12:30 29th"]},
           {"Chelsea v Man Utd - 12:30 29th": ["Chelsea", "Man Utd"]}, xsaves_after=99)
    with pytest.raises(mod.Rest) as e:
        mod.bb_saves(page, _Capture())
    kickoff = datetime(2026, 10, 29, 12, 30, tzinfo=LONDON).timestamp()
    assert e.value.until == kickoff - 48 * 3600
    assert e.value.why == "no games within 48 hours - next is Chelsea v Man Utd, Thu 29 Oct 12:30"
    assert page.evaluate("SUB_SET") is None              # BB xSaves never asked for
    assert [g for g in page.evaluate("VISITED") if " v " in g] == []   # no game chosen
    assert id(page) not in mod._STATE                     # the page is closed next


def test_a_next_game_more_than_three_days_off_rests_a_day(page, monkeypatch):
    now = datetime(2026, 11, 11, 17, 57, tzinfo=LONDON)
    monkeypatch.setattr(mod, "_now", lambda: now)
    _only_epl(monkeypatch)
    _games(page, {"English Premier League (10/10)": ["all (10) - 12:30 21st", "21 Nov (7) - 12:30 21st",
                                                     "Man City v Fulham - 12:30 21st",
                                                     "Arsenal v Spurs - 17:30 21st"]}, xsaves_after=99)
    with pytest.raises(mod.Rest) as e:
        mod.bb_saves(page, _Capture())
    assert e.value.until == now.timestamp() + 24 * 3600
    assert e.value.why == "no games within 48 hours - next is Man City v Fulham, Sat 21 Nov 12:30"


def test_nothing_listed_rests_a_day(page, monkeypatch):
    _only_epl(monkeypatch)
    _games(page, {"English Premier League (0/0)": []})
    with pytest.raises(mod.Rest) as e:
        mod.bb_saves(page, _Capture())
    assert e.value.until == NOW.timestamp() + 24 * 3600
    assert e.value.why == "no games listed under the configured competitions"


_SOON = {"English Premier League (1/1)": ["all (1) - 19:45 tomorrow", "Bradford v Hull - 19:45 tomorrow"]}
_SOON_PLAYERS = {"Bradford v Hull - 19:45 tomorrow": ["Bradford", "Hull"]}


def test_a_missing_xsaves_says_what_is_listed_and_pulls_the_data_again(page, monkeypatch):
    import time
    _only_epl(monkeypatch)
    _games(page, _SOON, _SOON_PLAYERS, xsaves_after=99)
    t0 = time.time()
    with pytest.raises(mod.BuilderNotReady) as e:
        mod.bb_saves(page, _Capture())
    assert time.time() - t0 < 5                           # no 8 s wait on a locator
    assert str(e.value) == (
        "BB xSaves is not listed under BB Player/Team Stats (listed: BB xSOT, BB xShots, "
        "BB xAssists, BB xTackles, BB xFouls, BB xFouls Won, BB xOffsides, BB xThrow Ins, "
        "BB xPenalties Awarded, BB xPenalties Scored, BB Player xCards, BB xScoreOrAssist)"
        " - updating the data again")
    assert page.evaluate("UPDATES") == 1
    with pytest.raises(mod.BuilderNotReady):
        mod.bb_saves(page, _Capture())
    assert page.evaluate("UPDATES") == 2                  # the next call clicked Update Data


def test_xsaves_that_comes_with_the_second_pull_is_read(page, monkeypatch):
    _only_epl(monkeypatch)
    _games(page, _SOON, _SOON_PLAYERS, xsaves_after=2)
    with pytest.raises(mod.BuilderNotReady):
        mod.bb_saves(page, _Capture())
    rows = mod.bb_saves(page, _Capture())
    assert [(r["home"], r["line"]) for r in rows] == [("Bradford", 3.5)]
    assert page.evaluate("SUB_SET") == "BB xSaves"
    assert mod._XSAVES["pulls"] == 0                      # found: the count starts again


def test_after_three_extra_pulls_it_waits_for_the_next_data_update(page, monkeypatch):
    _only_epl(monkeypatch)
    _games(page, _SOON, _SOON_PLAYERS, xsaves_after=99)
    said = []
    for _ in range(6):
        with pytest.raises(mod.BuilderNotReady) as e:
            mod.bb_saves(page, _Capture())
        said.append(str(e.value).rsplit(" - ", 1)[1])
    assert said == ["updating the data again"] * 3 + ["waiting for the next data update"] * 3
    assert page.evaluate("UPDATES") == 1 + mod.XSAVES_RETRIES


def _fresh_page(page):
    """Another page in the same browser with the fake builder, as the runner's
    timed reload gives the job."""
    other = page.context.browser.new_page()
    other.set_content(FAKE)
    return other


def test_the_extra_pull_count_survives_a_new_page(page, monkeypatch):
    """The runner gives the job a new page on every timed reload; the extra
    pulls spent on the old one still count (11 Nov 2026 review)."""
    _only_epl(monkeypatch)
    _games(page, _SOON, _SOON_PLAYERS, xsaves_after=99)
    for _ in range(mod.XSAVES_RETRIES):
        with pytest.raises(mod.BuilderNotReady) as e:
            mod.bb_saves(page, _Capture())
        assert str(e.value).endswith("updating the data again")
    page.close()
    fresh = _fresh_page(page)
    _games(fresh, _SOON, _SOON_PLAYERS, xsaves_after=99)
    for _ in range(2):
        with pytest.raises(mod.BuilderNotReady) as e:
            mod.bb_saves(fresh, _Capture())
        assert str(e.value).endswith("waiting for the next data update")
    assert fresh.evaluate("UPDATES") == 1                 # its own first pull, no fourth extra


def test_extra_pulls_are_allowed_again_once_an_update_interval_has_passed(page, monkeypatch):
    _only_epl(monkeypatch)
    _games(page, _SOON, _SOON_PLAYERS, xsaves_after=99)
    for _ in range(mod.XSAVES_RETRIES + 1):
        with pytest.raises(mod.BuilderNotReady):
            mod.bb_saves(page, _Capture())
    mod._XSAVES["last"] -= mod.UPDATE_EVERY_S            # the last extra pull, 30 min ago
    with pytest.raises(mod.BuilderNotReady) as e:
        mod.bb_saves(page, _Capture())
    assert str(e.value).endswith("updating the data again")


def test_a_closed_pages_state_is_dropped(page, monkeypatch):
    _only_epl(monkeypatch)
    _games(page, _SOON, _SOON_PLAYERS)
    other = _fresh_page(page)
    _games(other, _SOON, _SOON_PLAYERS)
    mod.bb_saves(other, _Capture())
    assert id(other) in mod._STATE
    other.close()
    mod.bb_saves(page, _Capture())
    assert id(other) not in mod._STATE                    # a later page cannot inherit it


# --- the 48-hour window is absolute time (11 Nov 2026 review) -----------------
CLOCKS_GO_FORWARD = datetime(2026, 3, 28, 12, 0, tzinfo=LONDON)   # Sat; BST from 01:00 Sun 29 Mar


def test_across_a_clock_change_a_game_47_and_a_half_hours_ahead_is_read(page, monkeypatch):
    monkeypatch.setattr(mod, "_now", lambda: CLOCKS_GO_FORWARD)
    _only_epl(monkeypatch)
    games = ["all (1) - 12:30 30th", "Leeds v Hull - 12:30 30th"]    # 11:30 UTC Mon: 47.5 h
    _games(page, {"English Premier League (1/1)": games}, {games[1]: ["Leeds", "Hull"]})
    rows = mod.bb_saves(page, _Capture())
    assert [r["home"] for r in rows] == ["Leeds"]


def test_across_a_clock_change_a_game_48_and_a_half_hours_ahead_rests_half_an_hour(page, monkeypatch):
    monkeypatch.setattr(mod, "_now", lambda: CLOCKS_GO_FORWARD)
    _only_epl(monkeypatch)
    games = ["all (1) - 13:30 30th", "Bath v Wells - 13:30 30th"]    # 12:30 UTC Mon: 48.5 h
    _games(page, {"English Premier League (1/1)": games}, {games[1]: ["Bath", "Wells"]})
    with pytest.raises(mod.Rest) as e:
        mod.bb_saves(page, _Capture())
    assert e.value.until == CLOCKS_GO_FORWARD.timestamp() + 30 * 60
    assert e.value.why == "no games within 48 hours - next is Bath v Wells, Mon 30 Mar 13:30"


def test_a_game_at_now_is_out_and_one_exactly_48_hours_ahead_is_in(page, monkeypatch):
    _only_epl(monkeypatch)
    games = ["all (2) - 17:00",
             "Spurs v Wolves - 17:00",                  # kicks off now
             "Leeds v Hull - 17:00 28th"]               # exactly now + 48 h
    _games(page, {"English Premier League (2/2)": games}, {g: ["H", "A"] for g in games[1:]})
    rows = mod.bb_saves(page, _Capture())
    assert [r["home"] for r in rows] == ["Leeds"]
    assert not [g for g in page.evaluate("VISITED") if g.startswith("Spurs")]


def test_an_ordinal_that_rolls_into_next_month_is_within_the_window(page, monkeypatch):
    now = datetime(2026, 11, 30, 12, 0, tzinfo=LONDON)
    monkeypatch.setattr(mod, "_now", lambda: now)
    _only_epl(monkeypatch)
    games = ["all (1) - 12:30 1st", "Leeds v Hull - 12:30 1st"]      # 1 Dec: 24.5 h
    _games(page, {"English Premier League (1/1)": games}, {games[1]: ["Leeds", "Hull"]})
    rows = mod.bb_saves(page, _Capture())
    assert [(r["home"], r["kickoff"]) for r in rows] == [("Leeds", "2026-12-01T12:30:00Z")]
