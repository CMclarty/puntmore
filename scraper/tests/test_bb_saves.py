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
FAKE = """<html><body>
<button id=upd>Update Data</button>
<div id=app style="display:none">
<button>Standard View</button>
<select id=stat><option>BB xG</option><option>BB Player/Team Stats</option></select>
<select id=sub><option>BB xSOT</option><option>BB xSaves</option></select>
<div id=blocks></div>
<button id=combine>Combine Players/Games</button>
<div class=field><div class=control><select><option value=over>Over</option><option value=under>Under</option></select></div>
<div class=control><input type=number step=1 id=line></div><div class=control><button>saves</button></div></div>
<span id=modebox></span>
<div class=field><div class=control><button>Total Odds</button></div><div class=control><button id=total>Infinity</button></div></div>
</div>
<script>
const GAMES = {"English Football League Cup (8/8)": ["all (2) - 19:45 tomorrow", "Bradford v Peterborough - 19:45 tomorrow [C] [S]", "Fleetwood Town v Arsenal - 20:00 tomorrow"],
               "English Premier League (21/21)": ["all (1) - 12:30 31st", "Chelsea v Man Utd - 12:30 31st"]};
const PLAYERS = {"Bradford v Peterborough - 19:45 tomorrow [C] [S]": ["Bradford", "Peterborough"],
                 "Fleetwood Town v Arsenal - 20:00 tomorrow": ["Arsenal", "Fleetwood Town"],
                 "Chelsea v Man Utd - 12:30 31st": []};
function block(i) {
  const d = document.createElement('div'); d.className = 'block';
  d.innerHTML = `<div class=field><div class=control><button>Competition</button></div><div class=control><select class=comp><option>Select Competition</option>${Object.keys(GAMES).map(c => '<option>' + c + '</option>').join('')}</select></div></div>
  <div class=field><div class=control><button><span>Game(s)</span></button></div><div class=control><select class=game></select></div></div>
  <div class=field><div class=control><button>Player</button></div><div class=control><select class=player></select></div></div>`;
  const comp = d.querySelector('.comp'), game = d.querySelector('.game'), player = d.querySelector('.player');
  comp.onchange = () => { game.innerHTML = (GAMES[comp.value] || []).map(g => '<option>' + g + '</option>').join(''); game.onchange(); };
  game.onchange = () => { player.innerHTML = (PLAYERS[game.value] || []).map(p => '<option>' + p + '</option>').join(''); recalc(); };
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
document.getElementById('upd').onclick = () => setTimeout(() => { document.getElementById('app').style.display = ''; block(0); }, 300);
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
    # Chelsea v Man Utd has no saves model (empty Player list): no rows, no error.
    assert not any(r["home"] == "Chelsea" for r in rows)
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
