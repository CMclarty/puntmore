"""Team to have the MOST corners / cards — a fair three-way price from Pinnacle.

Neither book exposes "most corners" as a market on OddsPapi (only Corners /
Bookings HANDICAP), so there is no Paddy Power price to value against here —
the shop terminal has one. What the tool can give is the FAIR price to read it
against: P(home > away), P(level), P(away > home) from the team SPLIT of the
fixture's match ladder (pricing/counts/split.py, via cards/laddered.py::
team_split) — one joint law for both sides, with the share pinned by Pinnacle's
two team lines and its handicap, so "level" is priced from the same model as
everything else rather than from two counts assumed independent. A fixture
whose quotes contradict each other on the share is left out.
"""
from dataclasses import dataclass, field
from datetime import datetime

from ..normalise import team_similarity
from ..pricing.counts.split import TeamSplit
from ..ttl_cache import MISS, TTLCache
from .laddered import team_split
from .oddspapi import (TEAM_CARDS_AWAY, TEAM_CARDS_HOME,
                       TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME)
from .saves import KICKOFF_TOLERANCE_HOURS, MIN_TEAM_SIMILARITY, _parse_time
from .value import CardValueRow, _get_bookmaker

MOST_CORNERS = "most_corners"
MOST_CARDS = "most_cards"
_PAIRS = {
    MOST_CORNERS: (TEAM_CORNERS_HOME, TEAM_CORNERS_AWAY),
    MOST_CARDS: (TEAM_CARDS_HOME, TEAM_CARDS_AWAY),
}


@dataclass
class FairThreeWay:
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    sport_key: str
    market_key: str       # most_corners | most_cards
    p_home: float
    p_tie: float
    p_away: float
    basis: str            # hybrid — the team split (pricing/counts/split.py)
    # outcome -> (lowest p, highest p) over the match fit's dispersion band x
    # the share's range: the same combinations a team line's band is built from.
    band: dict = field(default_factory=dict)

    def _p(self, outcome: str) -> float:
        return {"home": self.p_home, "tie": self.p_tie, "away": self.p_away}[outcome]

    def fair(self, outcome: str) -> float | None:
        p = self._p(outcome)
        return round(1.0 / p, 2) if p > 1e-9 else None

    def fair_high(self, outcome: str) -> float | None:
        """The least favourable fair in the band — what an edge is measured
        against, exactly as FairPrice.edge_vs does for a line."""
        lo = self.band.get(outcome, (self._p(outcome),))[0]
        return 1.0 / lo if lo > 1e-9 else None


_BANDS = TTLCache(ttl=6 * 3600, maxsize=2000)


def _three_way_band(split) -> dict[str, tuple[float, float]]:
    key, point, totals, share, _ = split
    hit = _BANDS.get(key)
    if hit is not MISS:
        return hit
    combos = []
    for total in totals:
        for p in (share.lo, share.p, share.hi):
            s = point if (total is totals[0] and p == share.p) else TeamSplit(total, p)
            combos.append(s.three_way())
    band = {name: (min(c[i] for c in combos), max(c[i] for c in combos))
            for i, name in enumerate(("home", "tie", "away"))}
    _BANDS.set(key, band)
    return band


def most_fairs(events: list[dict]) -> list[FairThreeWay]:
    """One FairThreeWay per fixture and family where the share can be read off
    Pinnacle (a team line or the handicap) and the match ladder has fitted."""
    out: list[FairThreeWay] = []
    for event in events:
        pin = _get_bookmaker(event, "pinnacle")
        if pin is None:
            continue
        sport_key = event.get("sport_key", "")
        for market_key, (home_key, _away_key) in _PAIRS.items():
            split = team_split(event, pin, home_key, sport_key)
            if split is None:
                continue
            _, point, _, share, _ = split
            if share.disagree:
                continue
            p_home, p_tie, p_away = point.three_way()
            out.append(FairThreeWay(
                event_id=event["id"],
                home_team=event.get("home_team", ""),
                away_team=event.get("away_team", ""),
                commence_time=event.get("commence_time", ""),
                sport_key=sport_key,
                market_key=market_key,
                p_home=p_home, p_tie=p_tie, p_away=p_away,
                basis="hybrid",
                band=_three_way_band(split),
            ))
    return out


# --- Paddy's own three-way, from the desktop scraper -------------------------
# OddsPapi does not carry "most corners / most cards", so Paddy's price arrives
# the way Bookie Bashing's saves fairs do: a script on Callum's PC reads the
# Paddy page and POSTs it (/api/paddy/most-prices, routers/saves_ingest.py).
# Bounded and expiring, in memory; matched to a sweep fixture by team names and
# kickoff day. With a price in hand a three-way becomes a value row like any
# other; without one it stays a fair-only row on the feed.

@dataclass
class PaddyMost:
    home: str
    away: str
    kickoff: datetime
    market: str           # most_corners | most_cards
    home_price: float
    tie_price: float
    away_price: float


STORE = TTLCache(ttl=36 * 3600, maxsize=5000)


def store(entries: list[PaddyMost]) -> None:
    for e in entries:
        STORE.set((e.home.lower(), e.away.lower(), e.kickoff.date(), e.market), e)


def _paddy_for(event: dict, entries: list[PaddyMost]) -> dict[str, PaddyMost]:
    """Paddy's three-ways for one fixture: best team match, on the right day."""
    start = _parse_time(event.get("commence_time", ""))
    best: dict[str, tuple[float, PaddyMost]] = {}
    for e in entries:
        if start and abs((e.kickoff - start).total_seconds()) > KICKOFF_TOLERANCE_HOURS * 3600:
            continue
        score = min(team_similarity(e.home, event.get("home_team", "")),
                    team_similarity(e.away, event.get("away_team", "")))
        if score < MIN_TEAM_SIMILARITY:
            continue
        if e.market not in best or score > best[e.market][0]:
            best[e.market] = (score, e)
    return {market: e for market, (_, e) in best.items()}


_OUTCOMES = (("home", "Home"), ("tie", "Tie"), ("away", "Away"))


def most_value(events: list[dict], min_edge: float, entries=None) -> list[CardValueRow]:
    """Value rows for Paddy's "most" three-ways that have a fair from the split.

    The edge is measured against the band's least favourable fair, exactly as
    a line's is (FairPrice.edge_vs with require_band), and the row shows that
    same fair so Odds ÷ Fair − 1 is the Value printed. `side` carries
    Home | Tie | Away and `line` is 0 — no line exists.
    """
    entries = list(STORE.values()) if entries is None else entries
    if not entries:
        return []
    fairs = {(f.event_id, f.market_key): f for f in most_fairs(events)}
    rows: list[CardValueRow] = []
    for event in events:
        for market_key, quote in _paddy_for(event, entries).items():
            fair = fairs.get((event["id"], market_key))
            if fair is None:
                continue
            for outcome, side in _OUTCOMES:
                price = getattr(quote, f"{outcome}_price")
                reference, point = fair.fair_high(outcome), fair.fair(outcome)
                if not price or reference is None or point is None:
                    continue
                edge = round(price / reference - 1, 4)
                if edge < min_edge:
                    continue
                rows.append(CardValueRow(
                    event_id=event["id"],
                    home_team=event.get("home_team", ""),
                    away_team=event.get("away_team", ""),
                    commence_time=event.get("commence_time", ""),
                    sport_key=event.get("sport_key", ""),
                    market_key=market_key,
                    market_label=f"{side} ({market_key})",
                    line=0.0,
                    side=side,
                    paddy_odds=round(price, 3),
                    pinnacle_odds=round(point, 3),
                    fair_odds=round(reference, 3),
                    pinnacle_margin=0.0,
                    edge=edge,
                    basis="hybrid",
                ))
    return rows
