"""Reading an AW 26 leg's market, and finding its fair price.

The terminal prints a market and a selection in words ("Total Match Corners",
"Over 9.5"). To price and settle the leg those words become a kind, a line and
a side. What cannot be read that way is kind 'other' and matched on its words
alone — never forced into a totals market it may not be.
"""
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from ..normalise import split_sides, team_similarity

_KINDS = (
    ("corners", re.compile(r"\bcorners?\b", re.I)),
    ("cards", re.compile(r"\b(cards?|bookings?)\b", re.I)),
    ("saves", re.compile(r"\bsaves?\b", re.I)),
    ("goals", re.compile(r"\bgoals?\b|\bover\s*/\s*under\b|\btotal\b", re.I)),
)
_SIDE_RE = re.compile(r"\b(over|under)\b", re.I)
_LINE_RE = re.compile(r"(\d+(?:\.\d+)?)")

# The same bars the coupon matcher uses: both sides must look like the same
# clubs, and the kickoff must be the same day (fixture_checks.py's 12 hours).
TEAM_MIN = 0.80
KICKOFF_HOURS = 12
OTHER_TEXT_MIN = 0.85


def classify(market: str | None, selection: str | None) -> tuple[str, float | None, str | None]:
    """(kind, line, side) from the printed words. A totals kind needs a side AND
    a line from the SELECTION; anything short of that is 'other'."""
    sel = selection or ""
    side = _SIDE_RE.search(sel)
    line = _LINE_RE.search(sel)
    if side and line:
        text = f"{market or ''} {sel}"
        for kind, pattern in _KINDS:
            if pattern.search(text):
                return kind, float(line.group(1)), side.group(1).lower()
    return "other", None, None


def _same_fixture(event: str, home: str, away: str) -> bool:
    sides = split_sides(event)
    if not sides:
        return False
    return (team_similarity(sides[0], home) >= TEAM_MIN
            and team_similarity(sides[1], away) >= TEAM_MIN)


def _same_day(a: datetime | None, b: datetime | None) -> bool:
    # Either side may not know its kickoff; then the names decide alone.
    return a is None or b is None or abs(a - b) <= timedelta(hours=KICKOFF_HOURS)


def _words(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def find_fair(leg, fairs) -> float | None:
    """The fair price for `leg` among `fairs`, or None.

    Totals match on fixture, kind, line and side exactly. 'other' legs match on
    fixture plus near-identical market and selection words. Among several
    matches the most recently received wins — the latest scrape is the day's
    view. Ambiguity between DIFFERENT prices for the same thing is resolved the
    same way, never averaged."""
    best = None
    for f in fairs:
        if not _same_fixture(leg.event, f.home, f.away) or not _same_day(leg.kickoff, f.kickoff):
            continue
        if leg.kind and leg.kind != "other":
            if (f.kind, f.line, f.side) != (leg.kind, leg.line, leg.side):
                continue
        else:
            text = SequenceMatcher(None, _words(f"{leg.market} {leg.selection}"),
                                   _words(f"{f.market} {f.selection}")).ratio()
            if text < OTHER_TEXT_MIN:
                continue
        if best is None or (f.received_at or datetime.min) > (best.received_at or datetime.min):
            best = f
    return best.fair_odds if best else None


def find_fixture_id(leg, events) -> str | None:
    """OddsPapi's fixture id for `leg`, from the Paddy sweep's events (they
    carry it as `id`), so the leg can be settled from OddsPapi's results."""
    for e in events or []:
        if not _same_fixture(leg.event, e.get("home_team", ""), e.get("away_team", "")):
            continue
        try:
            ko = datetime.fromisoformat(str(e.get("commence_time", "")).replace("Z", "+00:00"))
            ko = ko.replace(tzinfo=None)
        except ValueError:
            ko = None
        if _same_day(leg.kickoff, ko):
            return str(e.get("id")) if e.get("id") is not None else None
    return None
