"""Bookie Bashing fair saves prices, read off the Bet Builder, for
POST /api/paddy/saves-fair.

The target's page is https://www.bookiebashing.net/tools/betbuilder/ (the
`bb-saves` target in targets.toml). There is no saves page: the
fair is the builder's "BB xSaves" stat, and it is read by DRIVING the builder
- selecting the game and typing each line - not by scraping a table. Learned
by hand on 26 Oct 2026:

- The builder is a Vue app that renders only after its "Update Data" button
  has pulled its model data (~60 MB: goals/config, goals/list, simulators).
  "Standard View" appearing is the sign it is up. The data goes stale after a
  minute by its own reckoning; it is refreshed at most every UPDATE_EVERY_S.
- Stat chain: "BB Player/Team Stats" -> "BB xSaves". Then Competition,
  Game(s) ("Home v Away - HH:MM [day] [flags]"; the list starts with group rows
  like "all (8) - ..." and "Wed (5) - ..."), and Player, which lists the two
  TEAMS (the keeper's side). Player is EMPTY when no bookmaker has posted a
  saves line for that game - the builder has no model for it then. The Premier
  League is often empty until a day or two before kickoff.
- "Combine Players/Games" adds a second block; its mode "Total Over Players"
  sums the two teams: MATCH-total saves, which is what Paddy's totals_saves
  market is (cards/saves.py prices match totals only).
- The line input has step=1 and the builder reads the INTEGER part: for a typed
  N or N.5, Over = more than N (at least N+1), the standard Over N.5 - but
  Under = fewer than N (at most N-1), NOT the standard Under N.5. So only Over
  is read; the server completes Under as its complement (saves.complete).
- The fair lands in the "Total Odds" field within a second, with no request:
  the model runs in the page. "Infinity" means no price.

ONLY GAMES WITHIN 48 HOURS (Callum, 11 Nov 2026): "It should only be doing
this for games within the next 48 hours." A pass reads only the games under
COMPETITIONS kicking off within WITHIN_H hours; the queue is rebuilt at the
start of every pass, so the window moves with the clock and a game that has
kicked off drops out. With none that near the parser raises rest.Rest: the
runner closes the page and loads nothing until WITHIN_H hours before the
next game it saw, or LOOK_AGAIN_S, whichever is sooner (`python -m scraper
wake bb-saves` ends a rest by hand).

BB xSAVES IS NOT ALWAYS LISTED. Seen on 11 Nov 2026: at 17:57, after one data
pull, "BB Player/Team Stats" listed 12 stats (BB xSOT ... BB xScoreOrAssist)
and no BB xSaves - the page said "304s since data update", the Update Data
button carried a red dot, and the parser waited on the option every minute
until its timeout. At 20:05, after another pull, it listed 13, BB xSaves
between BB Player xCards and BB xScoreOrAssist. So it is there only when the
pull brought the saves model. Now nothing waits on it: a missing BB xSaves
marks the data for a fresh pull on the next call (BuilderNotReady says what
is listed), at most XSAVES_RETRIES (~60 MB each) until it is found or
UPDATE_EVERY_S has passed since the last of them, the normal UPDATE_EVERY_S
applying meanwhile. The count is module-wide, not per page, so a timed
reload's new page does not start it again.

The 48-hour window, like the rest, is absolute time: across a clock change
London wall-clock arithmetic would be an hour out.

ORDER of a call: bring the builder up (Update Data -> Standard View, at most
every UPDATE_EVERY_S); choose BB Player/Team Stats (once per data update);
with no queue, build it from the Game(s) lists (the game list can be read
before BB xSaves is chosen) - empty means a rest; choose BB xSaves (once per
data update); read the queued games.

PACING. Nothing here loads a page: every step is an in-page selection, so the
Bookie Bashing load budget (sites.py) is untouched. Each call (every
PARSE_EVERY_S) works through games for at most CALL_BUDGET_S, then returns
what it has; a pass over COMPETITIONS takes a few calls. The page is marked
active after every call so the runner never reloads it as stale; a reload
only happens if this parser keeps failing, which is the right self-heal.
"""
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..rest import Rest, when

LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")

# Option-label prefixes in the builder's Competition select, in the order they
# are walked. Editable here; what Paddy quotes saves on decides the list.
COMPETITIONS = (
    "English Premier League",
    "English Football League Cup",
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Conference League",
    "English Sky Bet Championship",
    "Spanish La Liga",
    "Italian Serie A",
    "German Bundesliga",
    "French Ligue 1",
)
# Match-total saves lines Paddy tends to quote. Half lines only, as everywhere.
LINES = (3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)
CALL_BUDGET_S = 40.0          # work per call; the runner's tick waits on it
UPDATE_EVERY_S = 30 * 60      # "Update Data" costs ~60 MB
READY_TIMEOUT_MS = 60_000     # the builder rendering after Update Data, per try
READY_ATTEMPTS = 3
SETTLE_S = 0.25               # Total Odds settles within this after a change
WITHIN_H = 48                 # only games kicking off within this many hours (Callum, 11 Nov 2026)
LOOK_AGAIN_S = 24 * 3600      # with none that near, rest at most this long
XSAVES_RETRIES = 3            # extra data pulls in a row when BB xSaves is not listed

_GAME_RE = re.compile(r"^(?P<home>.+?) v (?P<away>.+?) - (?P<time>\d{1,2}:\d{2})\s*(?P<day>[^\[]*)")
_GROUP_RE = re.compile(r"^[\w ]+ \(\d+\)")


class BuilderNotReady(Exception):
    pass


def _now() -> datetime:
    """The clock the 48-hour window is read against (a seam for the tests)."""
    return datetime.now(LONDON)


def parse_game_label(label: str, now: datetime | None = None) -> dict | None:
    """"Bradford v Peterborough - 19:45 tomorrow [C] [S]" -> home, away and the
    kickoff as UTC ISO. None for the group rows ("all (8) - 19:45 tomorrow").

    The day is "" (today), "tomorrow", or an ordinal ("28th", "1st"): the next
    date from today with that day of the month. London time."""
    label = label.strip()
    m = _GAME_RE.match(label)
    if not m or _GROUP_RE.match(label):
        return None
    now = (now or datetime.now(LONDON)).astimezone(LONDON)
    hour, minute = (int(x) for x in m["time"].split(":"))
    day = m["day"].strip().lower()
    date = now.date()
    if day == "tomorrow":
        date += timedelta(days=1)
    elif day and day != "today":
        n = re.match(r"(\d{1,2})(st|nd|rd|th)$", day)
        if not n:
            return None
        want = int(n.group(1))
        for ahead in range(0, 62):
            d = now.date() + timedelta(days=ahead)
            if d.day == want:
                date = d
                break
        else:
            return None
    kickoff = datetime(date.year, date.month, date.day, hour, minute, tzinfo=LONDON)
    return {"home": m["home"].strip(), "away": m["away"].strip(),
            "kickoff": kickoff.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


# --- the page -------------------------------------------------------------------

def _field_select(page, label, nth=0):
    """The <select> in the builder field whose button is `label` (the builder's
    "button + select" rows). nth picks the block: 0 the first, 1 the combined."""
    return page.locator(f"div.field:has(> div.control > button:has-text('{label}')) select").nth(nth)


def _options(sel) -> list[str]:
    return [o.strip() for o in sel.locator("option").all_inner_texts()]


def _select_label(sel, label: str) -> bool:
    opts = _options(sel)
    if label not in opts:
        return False
    sel.select_option(index=opts.index(label))
    return True


def _select_prefix(sel, prefix: str) -> str | None:
    for i, o in enumerate(_options(sel)):
        if o.startswith(prefix):
            sel.select_option(index=i)
            return o
    return None


def _stat_select(page, label: str):
    """The stat chain's selects carry no button; find the one holding `label`."""
    page.locator(f"select:has(option:text-is('{label}'))").first.select_option(label=label)


def _total_odds(page) -> float | None:
    box = page.locator("div.field:has(> div.control > button:text-is('Total Odds')) button").nth(1)
    try:
        v = float(box.inner_text().strip())
    except ValueError:
        return None
    return v if v > 1.0 else None


def _read_total(page, previous: float | None) -> float | None:
    """Total Odds after it has settled: the same value twice SETTLE_S apart,
    and not the previous line's (consecutive lines never price the same)."""
    deadline = time.time() + 3.0
    last = None
    while time.time() < deadline:
        v = _total_odds(page)
        if v is not None and v == last and v != previous:
            return v
        last = v
        time.sleep(SETTLE_S)
    return last if last != previous else None


def _type_line(page, line: float):
    inp = page.locator("div.field:has(> div.control > button:text-is('saves')) input[type=number]").first
    inp.click()
    page.keyboard.press("Control+A")
    page.keyboard.type(f"{line:g}")
    page.keyboard.press("Tab")


def _ensure_ready(page, state: dict, now: float):
    """Bring the builder up (Update Data -> Standard View) and onto the BB
    Player/Team Stats section. BB xSaves is chosen later (_ensure_xsaves), only
    when there is a game to read."""
    standard = page.get_by_text("Standard View", exact=True).first
    if page.get_by_text("Update Data", exact=True).count() == 0:
        # Not the builder at all: logged out, Bookie Bashing sends the tool's
        # url to its "How it works" page. Said at once, not after a timeout.
        raise BuilderNotReady("not the bet builder page - logged out of Bookie Bashing?")
    if not standard.is_visible() or now - state.get("updated", 0) >= UPDATE_EVERY_S:
        # One click does not always bring it up (a data pull that failed shows
        # as a red count by the title); a second usually does.
        for attempt in range(READY_ATTEMPTS):
            page.get_by_text("Update Data", exact=True).first.click()
            try:
                standard.wait_for(timeout=READY_TIMEOUT_MS)
                break
            except Exception:
                if attempt == READY_ATTEMPTS - 1:
                    raise BuilderNotReady("the builder did not render after Update Data")
        state["updated"] = now
        state["queue"] = []                 # the games may have changed
        state["section_set"] = False
        state["stat_set"] = False
    if not state.get("section_set"):
        _stat_select(page, "BB Player/Team Stats")
        page.wait_for_timeout(600)
        state["section_set"] = True


def _ensure_xsaves(page, state: dict, now: float):
    """BB xSaves in the stats list, once per data update. It is only listed
    when the data pull brought the saves model (11 Nov 2026): when it is not
    there, nothing waits on it - the data is marked for a fresh pull on the
    next call, at most XSAVES_RETRIES times until BB xSaves is found or
    UPDATE_EVERY_S has passed since the last of them; meanwhile the normal
    UPDATE_EVERY_S applies. The count is kept in _XSAVES, not per page: the
    runner gives the job a new page on every timed reload."""
    if state.get("stat_set"):
        return
    if page.locator("select:has(option:text-is('BB xSaves'))").count() == 0:
        listed = page.locator("select:has(option:text-is('BB xSOT'))")
        names = _options(listed.first) if listed.count() else []
        what = f" (listed: {', '.join(names)})" if names else ""
        if now - _XSAVES["last"] >= UPDATE_EVERY_S:
            _XSAVES["pulls"] = 0
        if _XSAVES["pulls"] < XSAVES_RETRIES:
            _XSAVES["pulls"] += 1
            _XSAVES["last"] = now
            state["updated"] = 0            # the next call clicks Update Data again
            then = "updating the data again"
        else:
            then = "waiting for the next data update"
        raise BuilderNotReady(f"BB xSaves is not listed under BB Player/Team Stats{what} - {then}")
    _XSAVES["pulls"] = 0
    _stat_select(page, "BB xSaves")
    page.wait_for_timeout(600)
    state["stat_set"] = True


def _kickoff(game: dict) -> datetime:
    return datetime.fromisoformat(game["kickoff"].replace("Z", "+00:00"))


def _build_queue(page, now: datetime) -> tuple[list[tuple[str, str]], tuple[datetime, str] | None]:
    """(competition option, game option) for every game under COMPETITIONS
    kicking off within WITHIN_H hours of `now`; and the earliest game after
    that (its kickoff and "Home v Away"), None when there is none. The window
    is absolute time (UTC), as the rest is: London wall-clock arithmetic would
    be an hour out across a clock change."""
    comp = _field_select(page, "Competition")
    now = now.astimezone(UTC)
    horizon = now + timedelta(hours=WITHIN_H)
    queue, later = [], None
    for prefix in COMPETITIONS:
        label = _select_prefix(comp, prefix)
        if label is None:
            continue
        page.wait_for_timeout(500)
        for g in _options(_field_select(page, "Game(s)")):
            game = parse_game_label(g, now)
            if game is None:
                continue
            kickoff = _kickoff(game)
            if now < kickoff <= horizon:
                queue.append((label, g))
            elif kickoff > horizon and (later is None or kickoff < later[0]):
                later = (kickoff, f"{game['home']} v {game['away']}")
    return queue, later


def _rest(now: datetime, later: tuple[datetime, str] | None) -> Rest:
    """Nothing within WITHIN_H hours: rest until WITHIN_H hours before the
    next game seen, or LOOK_AGAIN_S, whichever is sooner."""
    look_again = now.timestamp() + LOOK_AGAIN_S
    if later is None:
        return Rest(look_again, "no games listed under the configured competitions")
    kickoff, teams = later
    return Rest(min(kickoff.timestamp() - WITHIN_H * 3600, look_again),
                f"no games within {WITHIN_H} hours - next is {teams}, {when(kickoff.timestamp())}")


def _select_block(page, block: int, comp_label: str, game_label: str) -> bool:
    """Point one player block at the game and its team (home for block 0, away
    for 1). False when the builder has no saves model for the game (an empty
    Player list) or the block is not there."""
    comp = _field_select(page, "Competition", block)
    if comp.count() == 0 or not _select_label(comp, comp_label):
        return False
    page.wait_for_timeout(400)
    if not _select_label(_field_select(page, "Game(s)", block), game_label):
        return False
    page.wait_for_timeout(600)
    players = _field_select(page, "Player", block)
    if len(_options(players)) < 2:
        return False
    players.select_option(index=block)
    page.wait_for_timeout(300)
    return True


def _ensure_second_block(page):
    """"Combine Players/Games" adds the second player block, once. The button
    only works once the first block has a team, so this follows _select_block(0)."""
    if _field_select(page, "Player", 1).count() == 0:
        page.get_by_text("Combine Players/Games", exact=True).first.click()
        page.wait_for_timeout(800)


def _set_total_mode(page) -> bool:
    """"Total Over Players" (match-total saves) and Over. The mode control is
    only drawn while BOTH blocks are on the same game, and it resets to
    "Each Player" whenever a block changes game - so this runs last, after
    both blocks are set, for every game."""
    mode = page.locator("select:has(option:text-is('Total Over Players'))").first
    if mode.count() == 0:
        return False
    mode.select_option(label="Total Over Players")
    page.locator("div.field:has(> div.control > button:text-is('saves')) select").first.select_option(
        value="over")
    page.wait_for_timeout(300)
    return True


def _read_game(page, comp_label: str, game_label: str, now: datetime) -> list[dict]:
    """Both blocks on this game, one team each, then every line. [] when the
    builder has no saves model for the game (empty Player list)."""
    game = parse_game_label(game_label, now)
    if game is None:
        return []
    if not _select_block(page, 0, comp_label, game_label):
        return []
    _ensure_second_block(page)
    if not _select_block(page, 1, comp_label, game_label) or not _set_total_mode(page):
        return []
    rows, previous = [], None
    for line in LINES:
        _type_line(page, line)
        fair = _read_total(page, previous)
        previous = fair
        if fair is not None:
            rows.append({**game, "line": line, "over": round(fair, 3)})
    return rows


# Per page (the game queue, when the data was last refreshed), by id(page).
# Each entry holds its page, so the id is not reused while the entry lives,
# and an entry whose page has closed is dropped on the next call.
_STATE: dict[int, dict] = {}
# The extra data pulls for a missing BB xSaves (_ensure_xsaves): how many since
# it was last found, and when the last was asked for. Not per page: a timed
# reload gives the job a new page, and the cap must hold across it.
_XSAVES = {"pulls": 0, "last": 0.0}


def _state(page) -> dict:
    for key in [k for k, st in _STATE.items() if st["page"] is not page and st["page"].is_closed()]:
        del _STATE[key]
    return _STATE.setdefault(id(page), {"page": page})


def bb_saves(page, capture) -> list[dict]:
    """Drive the builder for up to CALL_BUDGET_S and return the saves fairs read.
    State (the game queue, when the data was last refreshed) lives per page.
    Raises Rest when no game kicks off within WITHIN_H hours."""
    state = _state(page)
    started = time.time()
    now = _now()
    page.set_default_timeout(8_000)         # a control that is not there is a skip, not a stall
    _ensure_ready(page, state, started)
    if not state.get("queue"):
        state["queue"], later = _build_queue(page, now)
        state["cursor"] = 0
        if not state["queue"]:
            _STATE.pop(id(page), None)      # the runner closes the page
            raise _rest(now, later)
    _ensure_xsaves(page, state, started)
    rows = []
    while state["cursor"] < len(state["queue"]) and time.time() - started < CALL_BUDGET_S:
        comp_label, game_label = state["queue"][state["cursor"]]
        state["cursor"] += 1
        rows.extend(_read_game(page, comp_label, game_label, now))
    if state["cursor"] >= len(state["queue"]):
        state["queue"] = []                 # next call rebuilds (and refreshes when due)
    capture.last_activity = time.time()     # driven, not idle: never reload as stale
    return rows
