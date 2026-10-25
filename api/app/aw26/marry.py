"""Marrying an AW 26 leg to the snapshot line it came from.

A leg is married to the line in the NEAREST snapshot at or before its slip's
placed_at that quotes the same thing: the same fixture (both team names at
aw26/legs.py's TEAM_MIN, kickoffs the same day), and the same kind, line and
side EXACTLY. A team line (`<Team> Over 4.5 corners`) only marries a team line
for that team; a match line only a match line — the printed text decides which
it is, and a leg naming neither team is a match line.

Only totals legs marry (kind corners | cards | goals | saves); an 'other' leg,
or one whose line or side was not read, stays unmarried. So does a leg whose
line was priced worse than the sweep's CARDS_MIN_EDGE (the sweep never
returned it, so no snapshot holds it) or whose slip was struck more than
LOOKBACK_HOURS after the last snapshot.

The line's offered and fair prices are copied onto the AwLegLine, so the
offered-vs-taken record outlives the snapshot.
"""
from datetime import timedelta

from ..models import AwLegLine, ValueLine, ValueSnapshot
from ..normalise import split_sides, team_similarity
from .legs import TEAM_MIN, _same_day

LOOKBACK_HOURS = 3
SOURCES = ("paddy",)            # the terminal is Paddy's; more sources later
_KINDS = ("corners", "cards", "goals", "saves")


def team_scope(leg, home: str, away: str) -> str | None:
    """'home' | 'away' when the leg's selection (else market) names one team
    clearly; None for a match line."""
    for text in (leg.selection, leg.market):
        if not text:
            continue
        h, a = team_similarity(text, home), team_similarity(text, away)
        if max(h, a) >= TEAM_MIN and h != a:
            return "home" if h > a else "away"
    return None


def find_line(db, leg, placed_at) -> tuple[ValueLine, ValueSnapshot, float] | None:
    """(line, its snapshot, match score) for `leg`, or None."""
    if leg.kind not in _KINDS or leg.line is None or leg.side is None:
        return None
    sides = split_sides(leg.event or "")
    if not sides:
        return None
    hits = (
        db.query(ValueLine, ValueSnapshot)
        .join(ValueSnapshot, ValueLine.snapshot_id == ValueSnapshot.id)
        .filter(ValueSnapshot.source.in_(SOURCES),
                ValueSnapshot.at <= placed_at,
                ValueSnapshot.at >= placed_at - timedelta(hours=LOOKBACK_HOURS),
                ValueLine.market.in_((leg.kind, f"team_{leg.kind}")),
                ValueLine.line == leg.line,
                ValueLine.side == leg.side)
        .order_by(ValueSnapshot.at.desc(), ValueSnapshot.id.desc())
        .all()
    )
    best = None
    for line, snap in hits:
        if best is not None and snap.id != best[1].id:
            break                       # the nearest snapshot that had a match wins
        score = min(team_similarity(sides[0], line.home), team_similarity(sides[1], line.away))
        if score < TEAM_MIN or not _same_day(leg.kickoff, line.kickoff):
            continue
        scope = team_scope(leg, line.home, line.away)
        if scope is None:
            if line.market != leg.kind:
                continue
        elif line.market != f"team_{leg.kind}" or line.team != scope:
            continue
        if best is None or score > best[2]:
            best = (line, snap, score)
    return best


def marry_slip(db, slip, refresh: bool = False) -> int:
    """Marry each of `slip`'s legs to its line. A leg already married is left
    alone unless `refresh` (an edit: the leg may now read differently).
    Returns how many legs are married."""
    married = 0
    for leg in slip.legs:
        if leg.line_link is not None and not refresh:
            married += 1
            continue
        found = find_line(db, leg, slip.placed_at)
        if found is None:
            leg.line_link = None
            continue
        line, snap, score = found
        # Updated in place when there is one: leg_id is unique, and a delete
        # plus an insert in the same flush is not ordered that way round.
        link = leg.line_link or AwLegLine()
        link.line_id, link.source, link.snapshot_at = line.id, snap.source, snap.at
        link.offered_odds_at_snapshot, link.fair_odds = line.offered_odds, line.fair_odds
        link.edge_at_snapshot, link.match_score = line.edge, round(score, 3)
        link.basis = line.basis
        leg.line_link = link
        married += 1
    return married
