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
  ones, "#WOP - ..." among them), none chosen at first, and NO empty or
  placeholder option. The panel stays open after a preset is chosen. Some
  names carry tabs, doubled or trailing spaces; 17 names (whitespace
  collapsed) are listed more than once, 4 of them within one bookmaker.
- Choosing a preset fills the builder with the preset's blocks (Home 2,6: a
  goals block and a corners block, Team set to the home side), every block on
  the game block 1 was on: the game is kept. A user's preset shows "Delete
  Preset" beside the select (never pressed).
- Total Odds keeps the last value until the preset's price lands (2.32 stood
  before the first one); "Infinity" is no price.
- Below Total Odds, the field Reset / Update Data / "Ns since data update",
  then the bet's description in a message-body: absent before a game is
  chosen; "England v Croatia (Over 3.5 goals)" with a game and no preset;
  "All of England (Over 1.5 goals) and England (Over 5.5 corners)" once Home
  2,6 is chosen. (The Stat row sits in a message-body too, so the description
  is found as the one right after that field.)

A FAIR IS TAKEN ONLY WITH PROOF the preset was applied to this game, as all
of these, read every `bb.settle_s` for up to SETTLE_WAIT_S (else the item is
unsettled, with the reason):
- the preset was not already the chosen option: Use Preset has no empty
  option to go through, so an option already chosen cannot be applied afresh
  without choosing another real preset, which is not done;
- the description is there, and not the one before the preset was chosen;
- it names no team of a game any block was on before this item that is not
  in this game (a block left on the last game);
- Total Odds is a price and not the one before the preset was chosen (a true
  fair equal to the last item's is then unsettled, never a stale one);
- Total Odds and the description have both read the same for STABLE_S;
- afterwards every block's Game(s) is this game (else Unpriced).

The game: home and away are matched against block 1's Game(s) options as
bb_saves.parse_game_label reads them (NFC, whitespace collapsed, any case),
under the competition named: the options whose name (less its "(n/m)" count)
is the name given, any case; if none, those starting with it, as bb_saves's
COMPETITIONS. One game found is chosen; none, or the same teams more than once
(two competitions, two dates), is NoGame. A game already chosen in block 1 is
not chosen again. A preset is matched by its name with whitespace collapsed,
case as given; a name listed more than once is NoPreset (neither is chosen).

What the driver knows of a page (the games found) is dropped when `up` pulls
the data. Nothing here loads a page: every step is an in-page selection or
the "Show/Hide Presets" click (browser.click, as look's). Bookie Bashing's
load budget is untouched.
"""
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .. import behaviour
from .. import browser
from .bb_builder import _STATE as _BUILDER
from .bb_saves import (BuilderNotReady, _field_select, _options, _select_label, _state, _total_odds,
                       bring_up, parse_game_label)

SETTLE_WAIT_S = 8.0          # after a preset is chosen, the proof above within this, at most
STABLE_S = 1.0               # Total Odds and the description unchanged this long
PANEL_WAIT_MS = 3000         # "Show/Hide Presets" drawn, and its panel opening
COMP_WAIT_MS = 400           # a competition chosen: its games listed (bb_saves's pause)
GAME_WAIT_MS = 600           # a game chosen
FIRST_GAME_COMPS = 12        # competitions tried for a game to open the panel with, at most
_COUNT = re.compile(r"\s*\(\d+/\d+\)\s*$")
_AGE = re.compile(r"(\d+)\s*s since data update")
_UPDATE_FIELD = "div.field:has(> div.control > a:text-is('Update Data'))"

NO_DESCRIPTION = "no description of the bet"
SAME_DESCRIPTION = "the description did not change after the preset was chosen"
NO_PRICE = "Total Odds shows no price"
SAME_TOTAL = "Total Odds did not change after the preset was chosen"
UNSTABLE = f"Total Odds and the description did not hold for {STABLE_S:.1f} s"


class NoGame(Exception):
    """No game, or more than one, for these teams under that competition."""


class NoPreset(Exception):
    """No preset of that name in Use Preset, or more than one."""


class Unpriced(Exception):
    """The preset was chosen but the builder did not end on the game."""


@dataclass(frozen=True)
class Reading:
    fair: float | None           # None: not proved (reason says why)
    description: str | None      # the bet's description as last read
    data_age_s: int | None       # "Ns since data update"
    reason: str | None


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


def description(page) -> str | None:
    """The bet's description (the message-body right after the Update Data
    field), whitespace collapsed; None when it is not drawn."""
    box = page.locator(f"{_UPDATE_FIELD} + div.message > div.message-body")
    if box.count() == 0:
        return None
    return collapse(box.first.inner_text()) or None


def data_age(page) -> int | None:
    """Seconds since the builder's data was pulled, as the page says it, or None."""
    try:
        field = page.locator(_UPDATE_FIELD)
        if field.count() == 0:
            return None
        m = _AGE.search(collapse(field.first.inner_text()))
        return int(m.group(1)) if m else None
    except Exception:
        return None


# Per page: the games found (competition, home, away -> the labels chosen), by
# id(page); a closed page's entry is dropped, and a data pull drops it (`up`).
_STATE: dict[int, dict] = {}


def up(page, now: float) -> bool:
    """The builder up (bb_saves.bring_up), on the bb-look job's own clock, so a
    request and the job's parse never pull the data twice. Every Playwright
    call on the page then has at most 8 s, as bb_builder's. When this pulled
    the data, what the driver knew of the page is forgotten."""
    page.set_default_timeout(8_000)
    pulled = bring_up(page, _state(page, _BUILDER), now)
    if pulled:
        _STATE.pop(id(page), None)
    return pulled


def _teams(label: str | None) -> set[str]:
    p = parse_game_label(label or "")
    return {_name(p["home"]), _name(p["away"])} if p else set()


def _shown_games(page) -> list[str | None]:
    return _games_field(page).evaluate_all(
        "els => els.map(s => s.selectedIndex < 0 ? null : s.options[s.selectedIndex].innerText)")


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


def chosen_preset(page) -> str | None:
    """The preset Use Preset shows chosen (whitespace collapsed), or None when
    the panel is shut or nothing is chosen."""
    sel = _preset_select(page)
    if sel.count() == 0 or not sel.is_visible():
        return None
    i, texts = _chosen(sel), _texts(sel)
    return collapse(texts[i]) if 0 <= i < len(texts) else None


def _first_game(page) -> str:
    """Choose a game in block 1 so the presets panel is drawn: the first game
    listed, the competition chosen first, then the others in their order (at
    most FIRST_GAME_COMPS). Says which."""
    comp_sel = _field_select(page, "Competition", 0)
    game_sel = _field_select(page, "Game(s)", 0)
    if comp_sel.count() == 0:
        raise BuilderNotReady("no Competition field on the builder")
    current = _chosen_label(comp_sel)
    comps = [c for c in dict.fromkeys(_options(comp_sel)) if c]
    comps.sort(key=lambda c: c != current)
    for c in comps[:FIRST_GAME_COMPS]:
        if _chosen_label(comp_sel) != c:
            _select_label(comp_sel, c)
            page.wait_for_timeout(COMP_WAIT_MS)
        g = next((g for g in _options(game_sel) if parse_game_label(g)), None)
        if g is not None:
            _select_label(game_sel, g)
            page.wait_for_timeout(GAME_WAIT_MS)
            return f"{g} ({c}), chosen to open the panel: no game was chosen"
    raise BuilderNotReady("no game listed to open the presets panel with")


def list_presets(page) -> tuple[list[dict], str]:
    """Every Use Preset name (whitespace collapsed) with how many options carry
    it, in the page's order; and the game the panel was drawn for. With no
    game chosen, the first one listed is chosen to draw it (`_first_game`).
    No preset is chosen."""
    sel = _preset_select(page)
    game_sel = _field_select(page, "Game(s)", 0)
    chosen = _chosen_label(game_sel) if game_sel.count() else None
    if chosen and parse_game_label(chosen):
        came_from = f"{chosen} (already chosen)"
    elif sel.count() and sel.is_visible():
        came_from = "the panel already open"
    else:
        came_from = _first_game(page)
    counts = Counter(presets(page))
    return [{"name": n, "count": c} for n, c in counts.items()], came_from


def _find_preset(texts: list[str], preset: str) -> int:
    hits = [i for i, t in enumerate(texts) if collapse(t) == collapse(preset)]
    if not hits:
        raise NoPreset(f"no preset {preset!r} in Use Preset")
    if len(hits) > 1:
        raise NoPreset(f"{preset!r} is listed {len(hits)} times in Use Preset - none chosen")
    return hits[0]


def _mentions(text: str, team: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(team) + r"(?!\w)", text) is not None


def _not_yet(desc, total, desc_before, total_before, gone) -> str | None:
    """Why (desc, total) is not yet proof of the preset applied; None when it is."""
    if not desc:
        return NO_DESCRIPTION
    if desc == desc_before:
        return SAME_DESCRIPTION
    for team, shown in gone:
        if _mentions(_name(desc), team):
            return f"the description still names {shown} from the last game"
    if total is None:
        return NO_PRICE
    if total == total_before:
        return SAME_TOTAL
    return None


def _proved(page, desc_before, total_before, gone) -> tuple[float | None, str | None, str | None]:
    """(fair, description, why not): read every bb.settle_s until _not_yet
    passes and both have held STABLE_S, for up to SETTLE_WAIT_S."""
    settle = behaviour.get("bb.settle_s")
    deadline = time.time() + max(SETTLE_WAIT_S, STABLE_S + 4 * settle)
    seen, since, why = None, 0.0, NO_PRICE
    desc = None
    while time.time() < deadline:
        desc, total = description(page), _total_odds(page)
        now = time.time()
        if (desc, total) != seen:
            seen, since = (desc, total), now
        why = _not_yet(desc, total, desc_before, total_before, gone)
        if why is None:
            if now - since >= STABLE_S:
                return total, desc, None
            why = UNSTABLE
        time.sleep(settle)
    return None, desc, why


def price_reading(page, competition: str, home: str, away: str, preset: str) -> Reading:
    """Total Odds for `preset` on the game home v away under `competition`,
    taken only with the proof in the module's docstring; with none, a Reading
    whose fair is None and whose reason says why. Raises NoGame, NoPreset,
    Unpriced (the builder not on the game after the preset), BuilderNotReady
    (no builder, or no presets panel). The builder must be up (`up`)."""
    st = _state(page, _STATE)
    before_games = _shown_games(page)                   # every block, before this item
    comp_label, game_label = _find_game(page, st, competition, home, away)
    sel = _open_presets(page)
    i = _find_preset(_texts(sel), preset)
    now_teams = _teams(game_label)
    gone = []
    for g in before_games:
        p = parse_game_label(g or "")
        for t in (p["home"], p["away"]) if p else ():
            if _name(t) not in now_teams and (_name(t), t) not in gone:
                gone.append((_name(t), t))
    if _chosen(sel) == i:
        return Reading(None, description(page), data_age(page),
                       f"{preset!r} is already chosen in Use Preset, which has no empty option to go "
                       f"through: it cannot be shown to apply afresh")
    desc_before, total_before = description(page), _total_odds(page)
    sel.select_option(index=i)
    fair, desc, why = _proved(page, desc_before, total_before, gone)
    if fair is not None and not all(collapse(x or "") == collapse(game_label) for x in _shown_games(page)):
        raise Unpriced(f"the builder is not on {game_label} after the preset was chosen")
    return Reading(fair, desc, data_age(page), why)


def price(page, competition: str, home: str, away: str, preset: str) -> float | None:
    """price_reading's fair: Total Odds, or None when not proved."""
    return price_reading(page, competition, home, away, preset).fair
