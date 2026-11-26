"""A preset priced on the Bookie Bashing bet builder: Total Odds for a named
preset on a named game, read off the page the `bb-look` job keeps open. Read
only: nothing here is saved, and no control that changes Callum's account is
pressed. It is the `price` request's driver (ask.py, Runner._ask_price_step),
not a parser a job can name.

Written from the bb-look dumps of 12 Nov 2026 (data/dumps/bb-look-20261112-
134228 to -134459, a competition chosen, a game, the presets open, five presets
loaded one after another on one game):

- "Show/Hide Presets" is drawn only once a game is chosen (with only a
  competition chosen it is not on the page). It toggles a panel: Preset Name,
  Preset Bookmaker, "Save As Preset" (never pressed), and "Use Preset": a
  <select> of optgroups by bookmaker (640 options on 12 Nov 2026, Callum's own
  "user..." ones, "Home 1,5", "Home 2,6", ..., and Bookie Bashing's "glob..."
  ones, "#WOP - ..." among them), none chosen at first. The panel stays open
  after a preset is chosen. Some names carry tabs, doubled or trailing spaces;
  some names are in the list twice.
- Choosing a preset fills the builder with the preset's blocks (Home 2,6: a
  goals block and a corners block, Team set to the home side), every block on
  the game block 1 was on: the game is kept. A user's preset shows "Delete
  Preset" beside the select (never pressed).
- Total Odds keeps the last value until the preset's price lands (2.32 stood
  before the first one); "Infinity" is no price.

NOT in the dumps, so checked rather than assumed: whether a preset chosen
after block 1 is moved to another game puts every block on the new game (the
dumps load presets on one game only), and how long Total Odds takes after a
preset (look waited 2.5 s, and it was there). So after every preset the
blocks' games are read back, and a fair is taken only when Total Odds reads the
same twice `bb.settle_s` apart and is not the value it stood at before the
preset was chosen (as bb_saves._read_total does for its lines): a preset whose
true price equals the last one's is then unsettled, never a stale fair. Up to
SETTLE_WAIT_S for it to settle.

The game: home and away are matched against block 1's Game(s) options as
bb_saves.parse_game_label reads them (NFC, whitespace collapsed, any case),
under the competition named: the options whose name (less its "(n/m)" count)
is the name given, any case; if none, those starting with it, as bb_saves's
COMPETITIONS. One game found is chosen; none, or the same teams more than once
(two competitions, two dates), is NoGame. A game already chosen in block 1 is
not chosen again. A preset is matched by its name with whitespace collapsed,
case as given; of names listed twice, the one whose text is exactly as given,
else the first.

Nothing here loads a page: every step is an in-page selection or the
"Show/Hide Presets" click (browser.click, as look's). Bookie Bashing's load
budget is untouched.
"""
import re
import time
import unicodedata

from .. import behaviour
from .. import browser
from .bb_builder import _STATE as _BUILDER
from .bb_saves import (BuilderNotReady, _field_select, _options, _select_label, _state, _total_odds,
                       bring_up, parse_game_label)

SETTLE_WAIT_S = 6.0          # Total Odds settling after a preset is chosen, at most
PANEL_WAIT_MS = 3000         # "Show/Hide Presets" drawn, and its panel opening
COMP_WAIT_MS = 400           # a competition chosen: its games listed (bb_saves's pause)
GAME_WAIT_MS = 600           # a game chosen
_COUNT = re.compile(r"\s*\(\d+/\d+\)\s*$")


class NoGame(Exception):
    """No game, or more than one, for these teams under that competition."""


class NoPreset(Exception):
    """No preset of that name in Use Preset."""


class Unpriced(Exception):
    """The preset was chosen but the builder did not end on the game."""


def collapse(s: str) -> str:
    return " ".join((s or "").split())


def _name(s: str) -> str:
    """A team or competition name for matching: NFC, whitespace collapsed, any case."""
    return collapse(unicodedata.normalize("NFC", s or "")).casefold()


def _preset_select(page):
    # The Use Preset field's label is a link (<a class="button is-static">), not
    # a button, so bb_saves._field_select does not find it.
    return page.locator("div.field:has(> div.control > a:text-is('Use Preset')) select").first


def _games_field(page):
    return page.locator("div.field:has(> div.control > button:has-text('Game(s)')) select")


def _chosen(sel) -> int:
    return sel.evaluate("s => s.selectedIndex")


def _chosen_label(sel) -> str | None:
    i = _chosen(sel)
    opts = _options(sel)
    return opts[i] if 0 <= i < len(opts) else None


# Per page: the games found (competition, home, away -> the labels chosen) and
# the preset last loaded, by id(page); a closed page's entry is dropped.
_STATE: dict[int, dict] = {}


def up(page, now: float) -> bool:
    """The builder up (bb_saves.bring_up), on the bb-look job's own clock, so a
    request and the job's parse never pull the data twice. Every Playwright
    call on the page then has at most 8 s, as bb_builder's: a control that is
    not there is a failure, not a stall."""
    page.set_default_timeout(8_000)
    return bring_up(page, _state(page, _BUILDER), now)


def _find_game(page, st, competition, home, away) -> tuple[str, str]:
    comp_sel = _field_select(page, "Competition", 0)
    game_sel = _field_select(page, "Game(s)", 0)
    if comp_sel.count() == 0:
        raise BuilderNotReady("no Competition field on the builder")
    want = (_name(home), _name(away))
    key = (_name(competition), *want)
    found = st.setdefault("games", {}).get(key)
    if found and _chosen_label(comp_sel) == found[0] and _chosen_label(game_sel) == found[1]:
        return found
    comps = [c for c in dict.fromkeys(_options(comp_sel)) if c]        # each name once (Featured, A-Z)
    named = _name(competition)
    cands = ([c for c in comps if _name(_COUNT.sub("", c)) == named]
             or [c for c in comps if _name(c).startswith(named)])
    if not cands:
        raise NoGame(f"no competition {competition!r} on the builder")
    current = _chosen_label(comp_sel)
    cands.sort(key=lambda c: c != current)                             # the one chosen first: no reselect
    hits = []
    for c in cands:
        if _chosen_label(comp_sel) != c:
            _select_label(comp_sel, c)
            page.wait_for_timeout(COMP_WAIT_MS)
        for g in _options(game_sel):
            p = parse_game_label(g)
            if p and (_name(p["home"]), _name(p["away"])) == want:
                hits.append((c, g))
    if not hits:
        raise NoGame(f"no {home} v {away} under {competition!r}")
    if len(hits) > 1:
        raise NoGame(f"{home} v {away} is listed more than once under {competition!r}: "
                     + "; ".join(f"{g} ({c})" for c, g in hits))
    (c, g), = hits
    if _chosen_label(comp_sel) != c:
        _select_label(comp_sel, c)
        page.wait_for_timeout(COMP_WAIT_MS)
    if _chosen_label(game_sel) != g:
        _select_label(game_sel, g)
        page.wait_for_timeout(GAME_WAIT_MS)
    if len(st["games"]) > 500:
        st["games"].clear()
    st["games"][key] = (c, g)
    return c, g


def _open_presets(page):
    """The Use Preset select, its panel opened with "Show/Hide Presets" when it
    is not showing (that button is a toggle: never clicked while it shows)."""
    sel = _preset_select(page)
    if sel.count() and sel.is_visible():
        return sel
    toggle = page.get_by_text("Show/Hide Presets", exact=True)
    deadline = time.monotonic() + PANEL_WAIT_MS / 1000
    while not (toggle.count() and toggle.first.is_visible()):
        if time.monotonic() >= deadline:
            raise BuilderNotReady("no Show/Hide Presets on the builder (it is drawn once a game is chosen)")
        page.wait_for_timeout(200)
    browser.click(page, toggle.first)
    try:
        sel.wait_for(state="visible", timeout=PANEL_WAIT_MS)
    except Exception:
        raise BuilderNotReady("the presets panel did not open") from None
    return sel


def _texts(sel) -> list[str]:
    """The options' own text, tabs and doubled spaces as they are."""
    return sel.evaluate("s => Array.from(s.options).map(o => o.textContent)")


def presets(page) -> list[str]:
    """The Use Preset options, whitespace collapsed, in the page's order (a name
    listed twice is here twice). Opens the presets panel when it is shut; a
    game must be chosen for it to be drawn."""
    return [collapse(t) for t in _texts(_open_presets(page))]


def _find_preset(texts: list[str], preset: str) -> int:
    hits = [i for i, t in enumerate(texts) if collapse(t) == collapse(preset)]
    if not hits:
        raise NoPreset(f"no preset {preset!r} in Use Preset")
    return next((i for i in hits if texts[i] == preset), hits[0])


def _settled(page, before: float | None) -> float | None:
    """Total Odds once it reads the same twice bb.settle_s apart and is not
    `before`; None when that does not happen within SETTLE_WAIT_S."""
    settle = behaviour.get("bb.settle_s")
    deadline = time.time() + max(SETTLE_WAIT_S, 4 * settle)
    last = None
    while time.time() < deadline:
        v = _total_odds(page)
        if v is not None and v == last and v != before:
            return v
        last = v
        time.sleep(settle)
    return None


def _on_game(page, game_label: str) -> bool:
    shown = _games_field(page).evaluate_all(
        "els => els.map(s => s.selectedIndex < 0 ? null : s.options[s.selectedIndex].innerText)")
    return bool(shown) and all(collapse(x) == collapse(game_label) for x in shown)


def price(page, competition: str, home: str, away: str, preset: str) -> float | None:
    """Total Odds for `preset` on the game home v away under `competition`, or
    None when it does not settle (SETTLE_WAIT_S). Raises NoGame, NoPreset,
    Unpriced (the builder not on the game after the preset), BuilderNotReady
    (no builder, or no presets panel). The builder must be up (`up`)."""
    st = _state(page, _STATE)
    comp_label, game_label = _find_game(page, st, competition, home, away)
    sel = _open_presets(page)
    texts = _texts(sel)
    i = _find_preset(texts, preset)
    if st.get("loaded") == (comp_label, game_label, i) and _chosen(sel) == i:
        before = None                       # loaded already, on this game: read where it is
    else:
        st["loaded"] = None
        before = _total_odds(page)
        sel.select_option(index=i)
    fair = _settled(page, before)
    if not _on_game(page, game_label):
        st["loaded"] = None
        raise Unpriced(f"the builder is not on {game_label} after the preset was chosen")
    st["loaded"] = (comp_label, game_label, i) if fair is not None else None
    return fair
