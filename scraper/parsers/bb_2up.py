"""Bookie Bashing's Early Payout Tracker
(https://www.bookiebashing.net/trackers/early-payout-tracker/#/), one row per
selection on it, for POST /api/2up/bb.

Written from what the Scraper's Chrome held, signed in, on 18 Nov 2026: the
pages the `bb-2up` job saved at 12:10:39 and 12:11:40
(dumps/bb-2up-20261118-121039/0.html, -121140/0.html; 194 rows each) and
the payloads the page received from 12:09:46 to 12:28:47 (samples/
20261118-114343/bb-2up/). Cut into tests/fixtures/bb_2up/: tracker.html
(the table, 24 of its rows) and ratings.json (the payload it was drawn
from, the same 24 records). It reads the page's HTML (`page.content()`, no
request) and the payloads the page has received (`capture.recent`), and
clicks nothing: the page refreshes itself, and its controls (the filters,
Refresh, hide, add to my selections) change Callum's account.

What the page is:

- The table is drawn from a ratings payload the page fetches when it loads
  and again about once a minute: a JSON object with `count`, `metric`,
  `limit` (500), `offset` and `records`, one record per row, in the table's
  order (194 of 194, both dumps). A record carries all the table shows and
  more: `eventName`, `selectionName`, `bookName` ("Bet365"), `bookPrice`,
  `fairOdds` (the Lay column), `score` (EV, the rating: 100 is break-even),
  `evPound`, `nOdds`, `ko` and `changed` (unix seconds), `homeAway` (true
  for the home team), `metric` and `ratings` (the EV under each metric and
  model: `lay`, `last_trade`, and `dam_lay`, `dam_last_trade` on some).
- The table's "Last update" ("1m ago") and the line above it ("194/194
  shown · last refresh 0s ago") are written when the table is drawn and do
  not tick afterwards, so they say nothing about how old the page is. The
  first number of that line is how many rows are shown.
- KO is the record's `ko` shown in London time ("24/11/2026, 20:00:00" is
  1795550400): every row of both dumps agreed.
- The table's Book odds is `bookPrice` to two places as JavaScript rounds
  it: 2.625 (bet365's 13/8) shows as 2.63. The row carries the payload's
  number.
- The filters (Book, Competition, EV min, KO window, Metric, Display, 2-up
  model, Odds above, Odds below) are Vue controls whose values the saved
  HTML does not carry, but the payload shows what they did. On 18 Nov
  (screenshot: Book Bet365, all competitions, EV min 80, KO window Any,
  Metric Lay, Display EV, 2-up model Fixed trigger) every record had
  `metric` "lay", `bookName` "Bet365", and `score` equal to
  `ratings.lay.ev`, the fixed trigger's EV (never `ratings.dam_lay.ev`,
  on any of the records that carry one). What `score` holds under the
  Dynamic (CS) model has not been seen, so the check is only that it is
  still the lay rating's EV. The request's url carries the filters too; it
  is not read, the payload says all the rows depend on.
- `count` was 194 with 194 records and a `limit` of 500, so it is taken as
  the tracker's total: more than the records fetched is a short board.

So the rows are read from the newest ratings payload, which is exact (a
price, not its rounding; a kick-off as an instant, not London wall time)
and says when it arrived, and the table is the check that the page drew
it: the header must be the tracker's, and the table's rows, the shown count
and the payload's records must be the same selections. The payload is
known by its shape, as pp_most knows its payloads, never by its url.

A row (the contract with Render's /api/2up/bb):
  bookmaker   the record's bookName, lower-cased ("bet365")
  event, home, away, selection   the record's names (whitespace collapsed)
  side        "home" or "away": which team the selection is; it must agree
              with `homeAway`. The contract allows "draw", but no draw has
              been seen, so a selection that is neither team is skipped and
              quoted in `said`
  book_odds   bookPrice;  lay   fairOdds (both as the payload has them)
  rating      score, n_odds nOdds: to two places, as the page shows them
  kickoff     ko, UTC ISO ("2026-11-24T20:00:00Z")
  updated_s   now minus `changed`, in whole seconds (null with no `changed`)
  seen_at     when this parse ran, UTC ISO

Nothing is posted (no rows, and `said` says why), so Render keeps the last
board it had rather than a wrong one, when:
  - no ratings payload has been seen yet (a page just opened, no capture);
  - the newest one arrived more than STALE_S ago: the page has stopped
    refreshing. The parser does not touch the capture: each payload that
    arrives is the page's activity, so with none the capture goes quiet
    and the runner reloads the page after the job's `stale_min`;
  - the settings changed: the payload's metric is not lay, a record's book
    is not bet365, or a score is not the fixed trigger's;
  - the page fetched fewer records than the tracker has (`count` over
    `limit`);
  - there is no tracker table, or its header is not the tracker's (logged
    out, still loading, a new layout);
  - the table, the shown count and the payload disagree on the selections;
  - more than MAX_SKIPPED of the records could not be read.
A record with a field that cannot be read as its type is skipped and
counted, never guessed. Only games kicking off within `games.within_h`
hours are rows (the "Games" setting every job that reads games follows; 0
is no limit; a game under way is out); the others are not skips.
"""
import json
import math
import re
import time
import unicodedata
from datetime import datetime, timezone

from .. import behaviour
from .dom import Node, parse
from .rows import Rows

HEADER = ("", "Bookie", "Event", "Selection", "Book odds", "Lay", "EV", "EV £(100)", "N.Odds", "KO",
          "Last update")
COL = {name: i for i, name in enumerate(HEADER)}
METRIC = "lay"
BOOK = "bet365"
STALE_S = 180                   # the newest ratings payload older than this: the page has stopped
MAX_SKIPPED = 0.10              # more of the records unreadable than this: post nothing
SCORE_SLACK = 1e-6
EARLIEST, LATEST = 1_577_836_800, 4_102_444_800     # 2020-01-01, 2100-01-01 UTC

NOT_TEXT = frozenset(("script", "style", "noscript", "template"))
SHOWN = re.compile(r"^([0-9]+)/([0-9]+) shown\b")


def bb_2up(page, capture, now: float | None = None) -> Rows:
    """The rows, read from the page's HTML and its newest ratings payload;
    `now` is a seam for the tests. Never touches the capture or the page."""
    now = time.time() if now is None else now
    return read(page.content(), latest_ratings(capture), now, behaviour.get("games.within_h"))


# --- the payload ------------------------------------------------------------
def latest_ratings(capture) -> tuple[float, dict] | None:
    """(when it arrived, the payload) for the newest ratings payload the
    page has received, known by its shape; None when there is none."""
    if capture is None:
        return None
    for ts, _kind, _url, text in sorted(getattr(capture, "recent", ()), key=lambda r: r[0], reverse=True):
        if '"records"' not in text or '"metric"' not in text:
            continue
        try:
            body = json.loads(text)
        except (ValueError, RecursionError):
            continue
        if _is_ratings(body):
            return ts, body
    return None


def _is_ratings(body) -> bool:
    if not isinstance(body, dict) or not isinstance(body.get("records"), list) or "metric" not in body:
        return False
    return all(isinstance(r, dict) and "eventName" in r and "selectionName" in r for r in body["records"])


# --- the parse --------------------------------------------------------------
def read(html: str, ratings: tuple[float, dict] | None, now: float, within_h: float = 0) -> Rows:
    if ratings is None:
        return Rows([], "no ratings payload received yet: nothing posted until the page has fetched one")
    at, body = ratings
    if now - at > STALE_S:
        return Rows([], f"the newest ratings payload is {int((now - at) // 60)} min old: the page has "
                        "stopped refreshing; nothing posted, the page is left for the runner's stale reload")
    records = body["records"]
    changed = _settings(body)
    if changed:
        return Rows([], f"{changed}; nothing posted")
    count, limit = body.get("count"), body.get("limit")
    if _whole(count) and count > len(records):
        return Rows([], f"the tracker has {count} selections but the page fetched {len(records)}"
                        + (f" (its limit is {limit})" if _whole(limit) else "") + "; nothing posted")

    root = parse(html or "")
    tables = [(t, _header(t)) for t in root.find_all(lambda n: n.tag == "table")]
    table = next((t for t, head in tables if head == HEADER), None)
    if table is None:
        heads = [head for _t, head in tables if head]
        if heads:
            return Rows([], "the table's header is not the Early Payout Tracker's: " + " | ".join(heads[0]))
        return Rows([], "no Early Payout Tracker table on the page - logged out of Bookie Bashing, "
                        "or still loading?")
    trs = _body_rows(table)
    shown = _shown(root)
    if shown is None:
        return Rows([], "the page's \"N/M shown\" line is missing; nothing posted")
    if not (shown == len(trs) == len(records)):
        return Rows([], f"the page shows {shown}, its table has {len(trs)} rows and the newest payload "
                        f"{len(records)} records; nothing posted")
    drawn = sorted(_key(_text(_cells(tr)[COL["Event"]]), _text(_cells(tr)[COL["Selection"]]))
                   for tr in trs if len(_cells(tr)) == len(HEADER))
    fetched = sorted(_key(r.get("eventName"), r.get("selectionName")) for r in records)
    if drawn != fetched:
        return Rows([], "the table is not the newest payload's selections (not drawn yet?); nothing posted")
    if not records:
        return Rows([], "the tracker lists no selections")

    seen_at = _iso(now)
    horizon = now + within_h * 3600 if within_h else None
    rows, skipped, first_odd, outside = [], {}, None, 0
    for rec in records:
        row, why = _row(rec, now, seen_at)
        if row is None:
            skipped[why] = skipped.get(why, 0) + 1
            if why == "selection is neither team" and first_odd is None:
                first_odd = _clean(rec.get("selectionName"))
            continue
        if horizon is not None and not now < rec["ko"] <= horizon:
            outside += 1
            continue
        rows.append(row)
    said = []
    if skipped:
        n = sum(skipped.values())
        parts = [f"{k} {why}" + (f" (first: {first_odd!r})" if why == "selection is neither team" else "")
                 for why, k in sorted(skipped.items())]
        said.append(f"{n} of {len(records)} records skipped: " + ", ".join(parts))
        if n > MAX_SKIPPED * len(records):
            return Rows([], "; ".join(said) + f" - over {MAX_SKIPPED:.0%}, nothing posted")
    if outside:
        said.append(f"{outside} kicking off outside the next {within_h:g} hours left out")
    return Rows(rows, "; ".join(said) or None)


def _settings(body: dict) -> str | None:
    """Which tracker setting is not the one the rows are read for, else None."""
    metric = body.get("metric")
    if metric != METRIC:
        return f"the tracker's Metric is {metric!r}, not {METRIC!r}"
    books = sorted({_clean(r.get("bookName")).lower() for r in body["records"]} - {BOOK}, key=str)
    if any(r.get("metric", METRIC) != METRIC for r in body["records"]):
        return f"a record's metric is not {METRIC!r}"
    if books:
        return f"the Book filter is not {BOOK} alone (the payload has {', '.join(map(repr, books))})"
    for r in body["records"]:
        ev = (r.get("ratings") or {}).get(METRIC, {}) if isinstance(r.get("ratings"), dict) else {}
        score = r.get("score")
        if (isinstance(ev, dict) and _real(ev.get("ev")) and _real(score)
                and abs(score - ev["ev"]) > SCORE_SLACK):
            return "the 2-up model is not the fixed trigger (EV is not the lay rating's)"
    return None


# --- one record -------------------------------------------------------------
def _row(rec: dict, now: float, seen_at: str) -> tuple[dict | None, str | None]:
    event, selection = _clean(rec.get("eventName")), _clean(rec.get("selectionName"))
    if not event or not selection:
        return None, "with no event or selection"
    teams = _teams(event, selection)
    if teams is None:
        if " v " not in event:
            return None, "with an event that is not 'Home v Away'"
        return None, "selection is neither team"
    home, away, side = teams
    if isinstance(rec.get("homeAway"), bool) and rec["homeAway"] != (side == "home"):
        return None, "where homeAway disagrees with the names"
    book_odds, lay, n_odds = (rec.get(k) for k in ("bookPrice", "fairOdds", "nOdds"))
    if not all(_real(v) and v > 1.0 for v in (book_odds, lay, n_odds)):
        return None, "with odds that are not a number above 1"
    rating = rec.get("score")
    if not _real(rating):
        return None, "with no EV"
    ko = rec.get("ko")
    if not (_whole(ko) and EARLIEST <= ko < LATEST):
        return None, "with no kick-off"
    changed = rec.get("changed")
    updated = max(0, int(now - changed)) if _whole(changed) and EARLIEST <= changed < LATEST else None
    return {"bookmaker": _clean(rec.get("bookName")).lower(), "event": event, "home": home, "away": away,
            "selection": selection, "side": side,
            "book_odds": float(book_odds), "lay": float(lay), "rating": round(float(rating), 2),
            "n_odds": round(float(n_odds), 2),
            "kickoff": _iso(ko), "updated_s": updated, "seen_at": seen_at}, None


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


def _real(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _clean(s) -> str:
    """A name as the page shows it: NFC, whitespace collapsed; "" for a non-string."""
    return " ".join(unicodedata.normalize("NFC", s).split()) if isinstance(s, str) else ""


def _key(event, selection) -> tuple[str, str]:
    return _clean(event), _clean(selection)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _text(node: Node) -> str:
    out = []

    def walk(n: Node):
        for c in n.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag not in NOT_TEXT:
                walk(c)
    walk(node)
    return " ".join(unicodedata.normalize("NFC", "".join(out)).split())
