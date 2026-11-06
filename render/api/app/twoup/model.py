"""The 2-up model: pure functions, no I/O beyond reading the rescue table.

bet365's "2 Goals Ahead Early Payout" settles a match-result bet as a winner
the moment the selection goes two goals up, whatever happens after. bet365
does not price that concession into its 1X2 odds (the price is the same with
or without the offer), so the value is a pure overlay on the standard line:

    edge = P(win) + P(two up but fails to win) - 1 / bet365_price

P(win) is Pinnacle's 1X2 de-vigged with the power method (`odds/utils.py`,
the method used everywhere in this app). P(two up but fails to win) — the
RESCUE — comes from a table keyed by the selection's fair win probability,
held in `rescue_table.json` so it can be tuned against Bookie Bashing's 2-up
tracker without a code change (`tools/twoup_backtest.py`).

The edge is proven (Callum, 5 Nov 2026): this module implements it, it does
not re-derive or question it. Every dial is a named setting (`config.py`,
`twoup_*`) gathered in `Params`.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..odds.utils import devigd_multi

DEFAULT_TABLE_PATH = Path(__file__).with_name("rescue_table.json")
# Used when the table file is missing or unreadable, and for a probability no
# bucket covers when the file names no default of its own. The published
# per-match base rate (~1.8%) split between two sides, rounded up slightly
# because a side backed at value is more often the stronger one.
FALLBACK_RESCUE = 0.012

SourceScope = Literal["shared", "owner"]


# --- parameters ---------------------------------------------------------------

@dataclass(frozen=True)
class Params:
    """The four dials the filters read. Defaults match config.py;
    `from_settings` reads the live values."""
    min_edge: float = 0.005
    min_price: float = 1.25
    max_price: float = 8.0
    min_lead_minutes: int = 15

    @classmethod
    def from_settings(cls, settings=None) -> "Params":
        if settings is None:
            from ..config import settings
        return cls(
            min_edge=settings.twoup_min_edge,
            min_price=settings.twoup_min_price,
            max_price=settings.twoup_max_price,
            min_lead_minutes=settings.twoup_min_lead_minutes,
        )

    def but(self, **changes) -> "Params":
        return replace(self, **changes)


# --- the candidate ------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """One bettable selection, priced. The decision layer's only input type.

    2-up rows come from `feed.py` with `source_scope="shared"`."""
    event_id: str
    event: str                     # "Home v Away"
    home: str
    away: str
    kickoff: datetime              # aware, UTC
    selection: str                 # the team backed, as OddsPapi names it
    side: str                      # home | away
    price: float                   # bet365's decimal price
    p_win: float                   # fair P(win), Pinnacle de-vigged
    rescue: float                  # P(two up but fails to win)
    edge: float                    # p_win + rescue - 1/price
    market: str = "Match Result"
    competition: str = ""
    fair_price: float | None = None   # 1 / p_win
    source: str = "2up"
    source_scope: SourceScope = "shared"
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def payout_prob(self) -> float:
        """Probability the bet pays as a winner (win, or rescued)."""
        return self.p_win + self.rescue

    @property
    def ev(self) -> float:
        """Expected return per unit staked, minus the stake (0.05 = +5%)."""
        return self.price * self.payout_prob - 1.0


# --- fair probabilities -------------------------------------------------------

def fair_1x2(home: float, draw: float, away: float) -> tuple[float, float, float] | None:
    """Pinnacle's 1X2 prices -> fair (P(home), P(draw), P(away)), power method.

    All three prices are required: the margin belongs to the market, so a
    partial set cannot be de-vigged and returns None (never the raw price)."""
    prices = [home, draw, away]
    if any(p is None or not math.isfinite(p) or p <= 1.0 for p in prices):
        return None
    fair, _margin = devigd_multi(prices)
    probs = [1.0 / f for f in fair]
    total = sum(probs)
    return tuple(p / total for p in probs)  # type: ignore[return-value]


# --- rescue table -------------------------------------------------------------

@dataclass(frozen=True)
class Bucket:
    lo: float
    hi: float
    rescue: float
    n: int = 0
    source: str = "initial"


@dataclass(frozen=True)
class RescueTable:
    buckets: tuple[Bucket, ...]
    default: float = FALLBACK_RESCUE
    key: str = "fair_win_probability"
    path: str | None = None
    loaded: bool = True             # False when the file could not be read

    def rate(self, p_win: float) -> float:
        """The rescue rate for a selection with this fair win probability.
        Buckets are [lo, hi); the last bucket also takes p == its hi."""
        for i, b in enumerate(self.buckets):
            last = i == len(self.buckets) - 1
            if b.lo <= p_win < b.hi or (last and p_win == b.hi):
                return b.rescue
        return self.default

    def bucket_of(self, p: float) -> Bucket | None:
        for i, b in enumerate(self.buckets):
            last = i == len(self.buckets) - 1
            if b.lo <= p < b.hi or (last and p == b.hi):
                return b
        return None

    def to_json(self, *, notes: str | None = None) -> dict:
        out = {
            "version": 1,
            "key": self.key,
            "measure": "P(the selection goes two goals up AND does not win), "
                       "per match, unconditional",
            "default": self.default,
            "buckets": [
                {"lo": b.lo, "hi": b.hi, "rescue": b.rescue, "n": b.n, "source": b.source}
                for b in self.buckets
            ],
        }
        if notes:
            out["notes"] = notes
        return out


def parse_rescue_table(data: dict, path: str | None = None) -> RescueTable:
    """Validate a rescue table document. Raises ValueError on a bad one:
    buckets must be ordered, non-overlapping, inside [0, 1], with a rescue
    rate in [0, 1)."""
    raw = data.get("buckets") or []
    buckets = []
    for b in raw:
        bucket = Bucket(lo=float(b["lo"]), hi=float(b["hi"]), rescue=float(b["rescue"]),
                        n=int(b.get("n", 0) or 0), source=str(b.get("source", "initial")))
        if not (0.0 <= bucket.lo < bucket.hi <= 1.0):
            raise ValueError(f"bad bucket bounds {bucket.lo}-{bucket.hi}")
        if not (0.0 <= bucket.rescue < 1.0):
            raise ValueError(f"bad rescue rate {bucket.rescue}")
        buckets.append(bucket)
    buckets.sort(key=lambda b: b.lo)
    for a, b in zip(buckets, buckets[1:]):
        if b.lo < a.hi:
            raise ValueError(f"buckets overlap at {b.lo}")
    default = float(data.get("default", FALLBACK_RESCUE))
    if not (0.0 <= default < 1.0):
        raise ValueError(f"bad default rescue rate {default}")
    return RescueTable(buckets=tuple(buckets), default=default,
                       key=str(data.get("key", "fair_win_probability")), path=path)


def load_rescue_table(path: str | Path | None = None) -> RescueTable:
    """Read the table. A missing or invalid file is NOT fatal: the engine
    falls back to the flat FALLBACK_RESCUE with `loaded=False`, which the
    diagnostics route reports, so a broken file is visible, not silent."""
    p = Path(path) if path else DEFAULT_TABLE_PATH
    try:
        return parse_rescue_table(json.loads(p.read_text()), path=str(p))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return RescueTable(buckets=(), default=FALLBACK_RESCUE, path=f"{p} ({exc})",
                           loaded=False)


# --- edge and staking ---------------------------------------------------------

def edge(p_win: float, rescue: float, price: float) -> float:
    """The 2-up edge in probability units: P(win) + P(rescue) - 1/price."""
    return p_win + rescue - 1.0 / price


def price_fixture(*, event_id: str, home: str, away: str, kickoff: datetime,
                  pinnacle: tuple[float, float, float], bet365: tuple[float, float, float],
                  table: RescueTable, competition: str = "") -> list[Candidate]:
    """Both sides of one fixture priced as 2-up candidates (the draw has no
    2-up and is never one). Returns [] when Pinnacle's 1X2 is incomplete.
    Every side is returned whatever its edge; filtering is the caller's."""
    fair = fair_1x2(*pinnacle)
    if fair is None:
        return []
    p_home, _p_draw, p_away = fair
    out = []
    for side, team, p, price in (("home", home, p_home, bet365[0]),
                                 ("away", away, p_away, bet365[2])):
        if price is None or price <= 1.0:
            continue
        r = table.rate(p)
        out.append(Candidate(
            event_id=str(event_id), event=f"{home} v {away}", home=home, away=away,
            kickoff=kickoff, selection=team, side=side, price=float(price),
            p_win=p, rescue=r, edge=edge(p, r, price), competition=competition,
            fair_price=1.0 / p if p > 0 else None,
        ))
    return out


def passes_filters(c: Candidate, params: Params = Params()) -> str | None:
    """None if the candidate may be bet, else the reason it may not."""
    if not (params.min_price <= c.price <= params.max_price):
        return "price outside band"
    if c.edge < params.min_edge:
        return "edge below floor"
    return None
