"""Bookie Bashing's Early Payout Tracker
(https://www.bookiebashing.net/trackers/early-payout-tracker/#/), one row per
row of its table, for POST /api/2up/bb.

Written from the page the Scraper's Chrome held, signed in, on 18 Nov 2026
(dumps/bb-2up-20261118-121039/0.html and bb-2up-20261118-121140/0.html, a
minute apart; cut into tests/fixtures/bb_2up/tracker.html). It reads the
page's HTML (`page.content()`, no request) and clicks nothing: the page
refreshes itself, and its controls (the filters, Refresh, hide, add to my
selections) change what Callum's account shows.

What the page is:

- One table, its header `'', Bookie, Event, Selection, Book odds, Lay, EV,
  EV £(100), N.Odds, KO, Last update`. It is found by those header texts, not
  by its place or its classes (the `data-v-...` attributes change between
  builds of the page). The first column holds the row's buttons ("Hide
  selection", "Add to my selections"). Vue leaves `<!---->` where an optional
  column is not drawn; the DOM drops comments, so cells line up with the
  header.
- Bookie: a badge, `<span class="bb-book-badge" title="bet365">B365</span>`;
  the title is the book's name (bet365 on all 194 rows: the Book filter was
  Bet365). Event: "Home v Away". Selection: one of the two teams as the
  event names it (194 of 194 in both dumps; no draw was seen).
- Every game in the table is read, however far off its kick-off: the page
  is one load whatever it lists, so the 48-hour window (`games.within_h`),
  which saves loads, has nothing to save here; the page's own "KO window"
  filter narrows the table if Callum wants it narrower.
- Book odds, Lay and N.Odds are decimal odds ("13.00"). EV is a rating
  where 100 is break-even ("108.86") and EV £(100) the same as pounds on
  £100 ("£8.86", "£-19.68"): on every row seen, EV = 100 + EV £(100). That
  is checked, so a page showing EV some other way (the "Display" control) is
  never read as a rating.
- KO is London time as the page prints it, "24/11/2026, 20:00:00" (the
  Scraper's Chrome runs in Europe/London: Real Madrid v PSV at 20:00, a
  21:00 CET Champions League game, and English 15:00 kick-offs). Converted
  to UTC with zoneinfo.
- Last update: "1m ago" in the first dump and "25s ago" on every row in the
  second, a minute later, so the page had refreshed itself in between.
- Above the table a line "194/194 shown · last refresh 0s ago · prefs
  loaded"; the first number is the rows the page shows.
- The filters (Book, Competition, EV min, KO window, Metric, Display, 2-up
  model, Odds above, Odds below) are Vue-bound controls: what they hold is
  the controls' live value, which the saved HTML does not carry (no option is
  marked selected, no input has a value). So they are not read. The
  screenshot of 18 Nov showed Book Bet365, all competitions, EV min 80, KO
  window Any, Metric Lay, Display EV, Fixed trigger, no odds limits.

A row (the contract with Render's /api/2up/bb):
  bookmaker   the badge's title, lower-cased ("bet365")
  event, home, away, selection   as the page writes them
  side        "home" or "away": which team the selection is. The contract
              also allows "draw", but no draw has been seen on the page, so
              how it would write one is not known: a selection that is
              neither team is skipped, counted, and the first one quoted in
              `said`, so a real draw row shows how to read it
  book_odds, lay, rating, n_odds   numbers, as shown
  kickoff     UTC, "2026-11-24T20:00:00Z"
  updated_s   seconds since the row's last update ("1m ago" 60, "25s ago"
              25, "2h ago" 7200), null for any other text
  seen_at     when this parse ran, UTC ISO
A row with a cell that cannot be read as its type (no badge title, an event
without " v ", a selection that is neither team, odds that are not a number
above 1, EV not 100 + EV £(100), a KO that is not a date) is skipped and
counted, never guessed. A table whose header is not the one above gives no
rows, and so does a page with no table at all (logged out, or still
loading); `said` says which, so the job's log shows it and `min_rows` raises
the alarm.

Kept open, a page that refreshes itself: the capture is marked active after
a parse while some row was updated within FRESH_S, so the runner does not
reload it as stale. When no row was (the page has stopped refreshing, or
Bookie Bashing has stopped updating it), the capture is left alone, the
parse says so, and the runner's stale reload (`stale_min`) happens once the
page has also received nothing for that long.
"""
import math
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .dom import Node, parse
from .rows import Rows

LONDON = ZoneInfo("Europe/London")
HEADER = ("", "Bookie", "Event", "Selection", "Book odds", "Lay", "EV", "EV £(100)", "N.Odds", "KO",
          "Last update")
COL = {name: i for i, name in enumerate(HEADER)}
FRESH_S = 300                   # some row updated within this: the page is still refreshing
RATING_SLACK = 0.015            # EV and EV £(100) are each rounded to pennies

NOT_TEXT = frozenset(("script", "style", "noscript", "template"))
KO = re.compile(r"(\d{2})/(\d{2})/(\d{4}), (\d{2}):(\d{2}):(\d{2})")
AGO = re.compile(r"(\d{1,6})\s?([smh]) ago")
UNIT_S = {"s": 1, "m": 60, "h": 3600}
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
POUNDS = re.compile(r"£\s?(-?\d+(?:\.\d+)?)|(-)£\s?(\d+(?:\.\d+)?)")
SHOWN = re.compile(r"^(\d+)/(\d+) shown\b")


def bb_2up(page, capture) -> Rows:
    rows = read(page.content(), time.time())
    if capture is not None and fresh(rows):
        capture.last_activity = time.time()     # refreshing itself: never reload as stale
    return rows


def fresh(rows) -> bool:
    return any(r["updated_s"] is not None and r["updated_s"] <= FRESH_S for r in rows)


def read(html: str, now: float) -> Rows:
    root = parse(html or "")
    tables = [(t, _header(t)) for t in root.find_all(lambda n: n.tag == "table")]
    table = next((t for t, head in tables if head == HEADER), None)
    if table is None:
        heads = [head for _t, head in tables if head]
        if heads:
            return Rows([], "the table's header is not the Early Payout Tracker's: "
                            + " | ".join(heads[0]))
        return Rows([], "no Early Payout Tracker table on the page - logged out of Bookie Bashing, "
                        "or still loading?")
    seen_at = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    trs = _body_rows(table)
    rows, skipped, first_odd = [], {}, None
    for tr in trs:
        row, why = _row(tr, seen_at)
        if row is None:
            skipped[why] = skipped.get(why, 0) + 1
            if why == "selection is neither team" and first_odd is None:
                first_odd = _text(_cells(tr)[COL["Selection"]])
        else:
            rows.append(row)
    said = []
    if skipped:
        parts = [f"{n} {why}" + (f" (first: {first_odd!r})" if why == "selection is neither team" else "")
                 for why, n in sorted(skipped.items())]
        said.append(f"{sum(skipped.values())} of {len(trs)} rows skipped: " + ", ".join(parts))
    shown = _shown(root)
    if shown is not None and shown != len(trs):
        said.append(f"the page says {shown} shown, {len(trs)} rows in the table")
    if rows and not fresh(rows):
        said.append(f"no row updated in the last {FRESH_S // 60} minutes: the page is left for the "
                    "runner's stale reload")
    return Rows(rows, "; ".join(said) or None)


# --- the table --------------------------------------------------------------
def _header(table: Node) -> tuple[str, ...]:
    thead = table.find(lambda n: n.tag == "thead")
    tr = thead.find(lambda n: n.tag == "tr") if thead is not None else None
    if tr is None:
        return ()
    return tuple(_text(c) for c in tr.children if isinstance(c, Node) and c.tag in ("th", "td"))


def _body_rows(table: Node) -> list[Node]:
    out = []
    for body in (c for c in table.children if isinstance(c, Node) and c.tag == "tbody"):
        out.extend(c for c in body.children if isinstance(c, Node) and c.tag == "tr")
    return out


def _cells(tr: Node) -> list[Node]:
    return [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]


def _shown(root: Node) -> int | None:
    for p in root.find_all(lambda n: n.tag == "p"):
        m = SHOWN.match(_text(p))
        if m:
            return int(m.group(1))
    return None


# --- one row ----------------------------------------------------------------
def _row(tr: Node, seen_at: str) -> tuple[dict | None, str | None]:
    cells = _cells(tr)
    if len(cells) != len(HEADER):
        return None, "with a cell missing or extra"
    text = {name: _text(cells[i]) for name, i in COL.items()}
    badge = cells[COL["Bookie"]].find(lambda n: n.has_class("bb-book-badge"))
    bookmaker = " ".join((badge.get("title") or "").split()).lower() if badge is not None else ""
    if not bookmaker:
        return None, "with no bookmaker"
    teams = _teams(text["Event"], text["Selection"])
    if teams is None:
        if " v " not in text["Event"]:
            return None, "with an event that is not 'Home v Away'"
        return None, "selection is neither team"
    home, away, side = teams
    book_odds, lay, n_odds = (_odds(text[k]) for k in ("Book odds", "Lay", "N.Odds"))
    if None in (book_odds, lay, n_odds):
        return None, "with odds that are not a number above 1"
    rating, pounds = _number(text["EV"]), _pounds(text["EV £(100)"])
    if rating is None or pounds is None or abs(rating - (100 + pounds)) > RATING_SLACK:
        return None, "with EV not 100 + EV £(100)"
    kickoff = _kickoff(text["KO"])
    if kickoff is None:
        return None, "with a KO that is not a date"
    return {"bookmaker": bookmaker, "event": text["Event"], "home": home, "away": away,
            "selection": text["Selection"], "side": side,
            "book_odds": book_odds, "lay": lay, "rating": rating, "n_odds": n_odds,
            "kickoff": kickoff, "updated_s": updated_s(text["Last update"]), "seen_at": seen_at}, None


def _teams(event: str, selection: str) -> tuple[str, str, str] | None:
    """(home, away, side) for the event's one split at " v " that names the
    selection; None when there is none, or more than one (a name with " v "
    in it that leaves it unclear)."""
    found = set()
    parts = event.split(" v ")
    for i in range(1, len(parts)):
        home, away = " v ".join(parts[:i]).strip(), " v ".join(parts[i:]).strip()
        if not home or not away:
            continue
        if selection == home:
            found.add((home, away, "home"))
        elif selection == away:
            found.add((home, away, "away"))
    return found.pop() if len(found) == 1 else None


def _number(s: str) -> float | None:
    if not NUMBER.fullmatch(s):
        return None
    v = float(s)
    return v if math.isfinite(v) else None


def _odds(s: str) -> float | None:
    v = _number(s)
    return v if v is not None and v > 1.0 else None


def _pounds(s: str) -> float | None:
    m = POUNDS.fullmatch(s)
    if not m:
        return None
    return float(m.group(1)) if m.group(1) is not None else -float(m.group(3))


def _kickoff(s: str) -> str | None:
    """"24/11/2026, 20:00:00", London time, as UTC ISO; None when it is not
    a real date and time."""
    m = KO.fullmatch(s)
    if not m:
        return None
    day, month, year, hour, minute, second = (int(x) for x in m.groups())
    try:
        local = datetime(year, month, day, hour, minute, second, tzinfo=LONDON)
    except ValueError:
        return None
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def updated_s(s: str) -> int | None:
    """"1m ago" -> 60, "25s ago" -> 25, "2h ago" -> 7200; None for any other text."""
    m = AGO.fullmatch(s.strip())
    return int(m.group(1)) * UNIT_S[m.group(2)] if m else None


def _text(node: Node) -> str:
    out = []

    def walk(n: Node):
        for c in n.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag not in NOT_TEXT:
                walk(c)
    walk(node)
    return " ".join("".join(out).split())
