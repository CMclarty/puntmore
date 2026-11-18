"""Bookie Bashing's Early Payout Tracker as the Hub's Scraper posts it
(`POST /api/2up/bb`), and the join of its rows to OddsPapi's fixtures.

The register (18 Nov 2026): bet365's price for 2-Up comes from the tracker,
and the feed runs two modes over its rows, `bb` (BB's rating taken as given)
and `pinnacle` (the rescue model against Pinnacle). Render never loads the
tracker; the Scraper reads it on the Hub and the Hub posts it here.

THE SNAPSHOT. Each post is the whole tracker table as read at that moment and
REPLACES the previous one. Only bet365's home and away rows are kept (a draw
has no 2-up); what else arrived is counted. It is held in memory, on
`app.state` through the feed, never in a table: a restart loses it and the
next post refills it. A post whose newest `seen_at` is older than the held
snapshot's is refused, so a delayed file can never put older prices back.

THE JOIN. A tracker row carries team names and a kick-off, not OddsPapi's
fixture id, so it is matched by name the way the Paddy board joins the
Scraper's saves and "most" prices to OddsPapi's fixtures (`cards/saves.py`):
`team_similarity` at `MIN_TEAM_SIMILARITY` on BOTH sides, home to home and
away to away, inside `KICKOFF_TOLERANCE_HOURS` of the posted kick-off. It never
guesses: no fixture passing is `unmatched`; more than one passing is
`ambiguous` unless exactly one of them is an exact match of both normalised
names. Either way the row simply has no `pinnacle` verdict, and is counted.

THE ID. A row's `event_id` is made from the row itself, `bb:<home>|<away>|
<yyyy-mm-dd>` (normalised names, the kick-off's UTC date), never from the
OddsPapi fixture it matched: the Bet Placer's reader makes its bet id from
`event_id` + `side`, and a match that comes and goes between passes must never
make one selection two bets.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..cards.saves import KICKOFF_TOLERANCE_HOURS, MIN_TEAM_SIMILARITY
from ..normalise import normalise_team, team_similarity

BET365 = "bet365"
SIDES = ("home", "away")
# A row read further ahead of Render's clock than this is refused at ingest:
# the Hub's clock is wrong, and its prices would otherwise look fresh for ever.
SEEN_AT_FUTURE_S = 120


@dataclass(frozen=True)
class TrackerRow:
    """One bet365 selection off the tracker, as posted (validated)."""
    bookmaker: str
    event: str
    home: str
    away: str
    selection: str
    side: str                      # home | away (draws are not kept)
    book_odds: float               # bet365's price
    lay: float
    rating: float                  # BB's EV rating for the 2-up bet, 100 = break-even
    n_odds: float                  # BB's fair odds for the 2-up bet
    kickoff: datetime              # aware, UTC
    updated_s: int | None          # the data's age at `seen_at`: seen_at less BB's change time
    seen_at: datetime              # when the Scraper read the row, aware, UTC

    @property
    def bet365_priced_at(self) -> datetime | None:
        """When bet365's price on the row was true: `seen_at` less `updated_s`.
        None when the tracker gave no age: the row's age is unknown."""
        if self.updated_s is None:
            return None
        return self.seen_at - timedelta(seconds=self.updated_s)

    @property
    def event_id(self) -> str:
        return event_id_for(self.home, self.away, self.kickoff)


@dataclass(frozen=True)
class Snapshot:
    """The tracker table as last posted, with what the post carried."""
    rows: tuple[TrackerRow, ...]
    received_at: datetime
    received: int                  # rows in the post
    newest_seen_at: datetime | None
    dropped: dict = field(default_factory=dict)   # reason -> rows not kept

    def age_s(self, now: datetime) -> float:
        """The snapshot's age: since it was received, or since its newest row
        was read, whichever is older, so a post delivered late is as old as
        what it carries."""
        ages = [(now - self.received_at).total_seconds()]
        if self.newest_seen_at is not None:
            ages.append((now - self.newest_seen_at).total_seconds())
        return max(ages)


def event_id_for(home: str, away: str, kickoff: datetime) -> str:
    """`bb:<home>|<away>|<yyyy-mm-dd>`: normalised names and the kick-off's
    UTC date. Stable for a selection whatever the fixture join does."""
    day = kickoff.astimezone(timezone.utc).date().isoformat()
    return f"bb:{normalise_team(home)}|{normalise_team(away)}|{day}"


class TrackerStore:
    """The one held snapshot. `put` replaces it whole; reads get the
    immutable `Snapshot`, never a list being changed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None

    def snapshot(self) -> Snapshot | None:
        with self._lock:
            return self._snap

    def put(self, rows: list[TrackerRow], received: int, dropped: dict,
            now: datetime) -> tuple[Snapshot | None, str | None]:
        """(the new snapshot, None), or (None, why) when the post is older
        than the one held and so refused."""
        newest = max((r.seen_at for r in rows), default=None)
        with self._lock:
            held = self._snap
            if (held is not None and held.newest_seen_at is not None and newest is not None
                    and newest < held.newest_seen_at):
                return None, (f"older than the snapshot held: its newest row was seen at "
                              f"{newest.isoformat()}, the held one's at "
                              f"{held.newest_seen_at.isoformat()}")
            self._snap = Snapshot(rows=tuple(rows), received_at=now, received=received,
                                  newest_seen_at=newest, dropped=dict(dropped))
            return self._snap, None


@dataclass(frozen=True)
class Fixture:
    """One OddsPapi fixture off Pinnacle's board, for the join."""
    fixture_id: str
    home: str
    away: str
    start: datetime
    pinnacle: tuple | None         # (home, draw, away) raw prices; None: not all three
    competition: str = ""
    fair_priced_at: datetime | None = None   # when Pinnacle's price was true: fetched less the delay


def selection_agrees(selection: str, side: str, home: str, away: str) -> bool:
    """Whether a row's `selection` is the team on its `side`: it must clear
    `MIN_TEAM_SIMILARITY` against that team and score strictly higher
    against it than against the other, by the join's own similarity."""
    own, other = (home, away) if side == "home" else (away, home)
    s_own, s_other = team_similarity(selection, own), team_similarity(selection, other)
    return s_own >= MIN_TEAM_SIMILARITY and s_own > s_other


def match_fixture(row: TrackerRow, fixtures: list[Fixture]) -> tuple[Fixture | None, str]:
    """(the fixture, "matched") or (None, "unmatched" | "ambiguous").

    A fixture whose names also clear the floor the other way round (row home
    against fixture away and row away against fixture home) is never joined:
    beside a straight match it makes the row ambiguous, alone it leaves the
    row unmatched. A derby of two similar names ("Manchester United v
    Manchester City") is therefore never priced against Pinnacle."""
    window = KICKOFF_TOLERANCE_HOURS * 3600
    hits: dict[str, tuple[float, Fixture]] = {}
    swapped = False
    for fx in fixtures:
        if abs((fx.start - row.kickoff).total_seconds()) > window:
            continue
        score = min(team_similarity(row.home, fx.home), team_similarity(row.away, fx.away))
        if min(team_similarity(row.home, fx.away),
               team_similarity(row.away, fx.home)) >= MIN_TEAM_SIMILARITY:
            # Either way round: this fixture can never be joined, and a straight
            # match on it means the row cannot be told from its reverse.
            swapped = True
            if score >= MIN_TEAM_SIMILARITY:
                return None, "ambiguous"
            continue
        if score < MIN_TEAM_SIMILARITY:
            continue
        if fx.fixture_id not in hits or score > hits[fx.fixture_id][0]:
            hits[fx.fixture_id] = (score, fx)
    if swapped:
        return None, "ambiguous" if hits else "unmatched"
    if not hits:
        return None, "unmatched"
    if len(hits) == 1:
        return next(iter(hits.values()))[1], "matched"
    exact = [fx for score, fx in hits.values() if score >= 1.0]
    if len(exact) == 1:
        return exact[0], "matched"
    return None, "ambiguous"
