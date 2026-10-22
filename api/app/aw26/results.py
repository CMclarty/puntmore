"""AW 26 auto-results: settling a leg from a match's final stats, scraped off
FlashScore and POSTed to /api/aw26/results. One source, applied directly —
Callum's call (22 Oct 2026): no second-source double-check.

A leg is settled DETERMINISTICALLY from the match stats:
  - corners / cards / goals totals  -> the match total vs the leg's line
  - a team-specific total            -> that team's value vs the line
  - "<Team> goalkeeper N+ saves"     -> that keeper's saves vs N
Cards are a COUNT (yellow = 1, red = 1), done in the scraper; here cards_* are
already counts.

A leg this cannot read with confidence returns None and STAYS PENDING — never
assumed. Totals landing exactly on an integer line are a push (void).
"""
import logging
import re
from dataclasses import dataclass

from ..normalise import split_sides, team_similarity
from ..models import AwSlip

logger = logging.getLogger("uvicorn.error")

TEAM_MIN = 0.80
_THRESHOLD_RE = re.compile(r"(\d+)\s*(?:\+|or more)", re.I)
_SAVES_RE = re.compile(r"\bsaves?\b", re.I)
_KINDS = (("corners", re.compile(r"\bcorners?\b", re.I)),
          ("cards", re.compile(r"\b(cards?|bookings?)\b", re.I)),
          ("saves", re.compile(r"\bsaves?\b", re.I)),
          ("goals", re.compile(r"\bgoals?\b", re.I)))
_TOTALS_KINDS = ("corners", "cards", "goals", "saves")


@dataclass
class MatchStats:
    """One finished match's stats, home/away in the SAME order as the event's
    printed teams (FlashScore's header order, not a URL slug)."""
    home: str
    away: str
    goals_home: int | None = None
    goals_away: int | None = None
    corners_home: int | None = None
    corners_away: int | None = None
    cards_home: int | None = None       # yellow + red count
    cards_away: int | None = None
    saves_home: int | None = None
    saves_away: int | None = None

    def pair(self, kind: str) -> tuple[int | None, int | None]:
        return {
            "goals": (self.goals_home, self.goals_away),
            "corners": (self.corners_home, self.corners_away),
            "cards": (self.cards_home, self.cards_away),
            "saves": (self.saves_home, self.saves_away),
        }.get(kind, (None, None))


def _kind_of(text: str) -> str | None:
    for kind, pattern in _KINDS:
        if pattern.search(text):
            return kind
    return None


def _side_for_team(name: str | None, stats: MatchStats) -> str | None:
    """'home' | 'away' | None — which side `name` is, if either is clearly it."""
    if not name:
        return None
    h, a = team_similarity(name, stats.home), team_similarity(name, stats.away)
    if max(h, a) < TEAM_MIN or h == a:
        return None
    return "home" if h > a else "away"


def _event_is_this_match(event: str, stats: MatchStats) -> bool:
    sides = split_sides(event or "")
    if not sides:
        return False
    return (team_similarity(sides[0], stats.home) >= TEAM_MIN
            and team_similarity(sides[1], stats.away) >= TEAM_MIN)


def _over_under(value: int, line: float, side: str) -> str:
    if value == line:                       # integer line landed exactly: push
        return "void"
    over = value > line
    return "won" if (over if side == "over" else not over) else "lost"


def result_for_leg(leg, stats: MatchStats) -> str | None:
    """'won' | 'lost' | 'void', or None if it cannot be settled with confidence."""
    market, selection = leg.market or "", leg.selection or ""
    text = f"{market} {selection}"

    # A keeper threshold — "<Team> goalkeeper to make N or more saves". Needs
    # BOTH a threshold and a resolvable team; otherwise fall through (it may be
    # a "Total Saves over/under" line, handled below).
    if _SAVES_RE.search(text):
        m = _THRESHOLD_RE.search(text)
        team = re.sub(r"\bgoal\s*keeper\b|\bgk\b", "", selection, flags=re.I).strip() or None
        side = _side_for_team(team, stats)
        if m is not None and side is not None:
            saves = stats.saves_home if side == "home" else stats.saves_away
            return None if saves is None else ("won" if saves >= int(m.group(1)) else "lost")

    # Totals (corners / cards / goals / saves) over/under a line.
    kind = leg.kind if leg.kind in _TOTALS_KINDS else _kind_of(text)
    if kind in _TOTALS_KINDS and leg.side in ("over", "under") and leg.line is not None:
        home, away = stats.pair(kind)
        if home is None or away is None:
            return None
        # Team-specific unless it reads as a match/total market.
        if re.search(r"\b(total|match)\b", text, re.I):
            value = home + away
        else:
            team = _single_team(market, selection, stats)
            if team is None:                       # ambiguous team scope → don't assume
                value = home + away if _event_is_this_match(leg.event, stats) else None
            else:
                side = _side_for_team(team, stats)
                value = home if side == "home" else away if side == "away" else None
        if value is None:
            return None
        return _over_under(value, leg.line, leg.side)

    return None


def _single_team(market: str, selection: str, stats: MatchStats) -> str | None:
    """A single team named by a team-specific market, if one side clearly owns it."""
    for text in (selection, market):
        side = _side_for_team(text, stats)
        if side is not None:
            return stats.home if side == "home" else stats.away
    return None


def apply_match_stats(db, stats_list: list[MatchStats]) -> dict:
    """Set pending leg results from scraped match stats, then settle the slips
    whose legs are now all in. Returns counts. A leg not matched to any stats,
    or not confidently settled, is left pending."""
    from .service import settle_slip

    open_slips = db.query(AwSlip).filter(AwSlip.status == "open").all()
    resolved = settled = 0
    for slip in open_slips:
        changed = False
        for leg in slip.legs:
            if leg.result != "pending":
                continue
            stats = next((s for s in stats_list if _event_is_this_match(leg.event, s)), None)
            if stats is None:
                continue
            result = result_for_leg(leg, stats)
            if result in ("won", "lost", "void"):
                leg.result, changed = result, True
                resolved += 1
        if changed:
            settle_slip(slip)
            if slip.status != "open":
                settled += 1
    db.commit()
    logger.info("aw26 results: %d leg(s) resolved, %d slip(s) settled from %d match stat set(s)",
                resolved, settled, len(stats_list))
    return {"matches": len(stats_list), "resolved": resolved, "settled": settled}
