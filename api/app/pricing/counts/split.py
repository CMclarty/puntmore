"""A team's count as a binomial SPLIT of the match count.

Pinnacle prices a match's corners (or bookings) as a 4-5 rung ladder, and a
team's as a single line. Fitting a team ladder from one rung pins nothing. So
a team is modelled as a share of the match instead:

    T ~ the fitted match distribution            (pricing/counts/fit.py)
    H | T ~ Binomial(T, p),   A = T - H           p = the home side's share

Everything about a team then follows from T and ONE number, p — and p is
observable three ways, each a Pinnacle price on THIS fixture:

  * the home team's over/under line     P(H >= line + 0.5)
  * the away team's over/under line     P(A >= line + 0.5)
  * the corners/bookings handicap       P(H + hcap > A)

Where more than one is quoted they must agree; a contradiction is reported on
the estimate, never averaged away silently.

WHAT THIS BUYS. Team lines beyond the one Pinnacle quotes; a coherent "most
corners" three-way (H > A, equal, A > H) from the SAME joint law rather than
two counts assumed independent; and the handicap as a check on all of it. A
binomial split makes the two sides negatively correlated given the total, which
is what corner counts look like (possession swings), and gives a team a tighter
law than the match — dispersion 1 + p(D - 1), not D — as it should be.

WHAT IT ASSUMES. Each corner is the home side's with the same probability p,
independently. Real games cluster (a side on top wins several in a row), which
widens a team's law a little beyond binomial. The band carries the match fit's
dispersion range and the spread of p across its sources; that is where the
slack lives, and it is why the band — not the point — is what edges are
measured against (price.py).
"""
from dataclasses import dataclass, field

from .dist import CountDist
from .errors import LadderError
from .lines import HALF, INTEGER, is_half_line, line_kind
from .price import FairPrice, _round

P_MIN, P_MAX = 0.02, 0.98
# Two sources for p further apart than this are a contradiction worth saying.
DISAGREE = 0.06
# One source gives nothing to measure p's uncertainty against; this is the
# half-width used for the band then — a few percent of share, about a rung.
SOLO_HALF_WIDTH = 0.03


def _binomial_row(n: int, p: float) -> list[float]:
    """pmf of Binomial(n, p) for k = 0..n, by the forward recurrence."""
    q = 1.0 - p
    out = [0.0] * (n + 1)
    cur = q ** n
    for k in range(n + 1):
        out[k] = cur
        if k < n:
            cur = cur * (n - k) / (k + 1) * p / q
    return out


def _rows(total: CountDist):
    """(t, P(T = t)) for every total with any mass."""
    for t in range(total.grid_max + 1):
        pt = total.pmf(t)
        if pt > 1e-15:
            yield t, pt


class TeamSplit:
    """The joint law of (H, A) given the match law and the home share p."""

    __slots__ = ("total", "p", "_home", "_away")

    def __init__(self, total: CountDist, p: float):
        if not P_MIN <= p <= P_MAX:
            raise LadderError(f"share must be within {P_MIN}-{P_MAX}, got {p}")
        self.total = total
        self.p = p
        self._home = self._marginal(p)
        self._away = self._marginal(1.0 - p)

    def _marginal(self, p: float) -> list[float]:
        out = [0.0] * (self.total.grid_max + 1)
        for t, pt in _rows(self.total):
            for k, b in enumerate(_binomial_row(t, p)):
                out[k] += pt * b
        return out

    def pmf(self, k: int, home: bool = True) -> float:
        arr = self._home if home else self._away
        return arr[k] if 0 <= k < len(arr) else 0.0

    def mean(self, home: bool = True) -> float:
        arr = self._home if home else self._away
        return sum(k * v for k, v in enumerate(arr))

    def over(self, line: float, home: bool = True) -> float:
        """P(count >= line + 0.5) — a half line, so push-free by construction."""
        if not is_half_line(line):
            raise LadderError(f"team lines are x.5 only, got {line}")
        arr = self._home if home else self._away
        return sum(arr[int(line + 0.5):])

    def three_way(self) -> tuple[float, float, float]:
        """(P(H > A), P(H = A), P(A > H)) from the joint law."""
        more = equal = 0.0
        for t, pt in _rows(self.total):
            row = _binomial_row(t, self.p)
            more += pt * sum(row[k] for k in range(t + 1) if 2 * k > t)
            if t % 2 == 0:
                equal += pt * row[t // 2]
        return more, equal, max(0.0, 1.0 - more - equal)

    def handicap_cover(self, hcap: float) -> float:
        """P(H + hcap > A): the home side covers (home + hcap). Push-free for a
        half-integer handicap; an integer one also pushes when H + hcap == A."""
        return _cover(self.total, self.p, hcap)[0]


# --- p from what Pinnacle quotes ---------------------------------------------

def _bisect(f, target: float, increasing: bool, lo: float = P_MIN, hi: float = P_MAX) -> float:
    """p in [lo, hi] with f(p) == target, f monotone. Clamps at the ends: a
    quote outside the model's reach is reported by the caller, not fudged."""
    f_lo, f_hi = f(lo), f(hi)
    if increasing:
        if target <= f_lo:
            return lo
        if target >= f_hi:
            return hi
    else:
        if target >= f_lo:
            return lo
        if target <= f_hi:
            return hi
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        v = f(mid)
        if (v < target) == increasing:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-5:
            break
    return 0.5 * (lo + hi)


# THE PUSH RULE. Pinnacle quotes team lines and handicaps at integer values as
# well as halves. Those are OBSERVATIONS here, never bets (users only ever see
# .5 lines): on an integer line the exact count refunds both sides — Paddy's
# settlement rule and Pinnacle's — so the quote prices win against lose with
# the push taken out, and the model is matched on win / (win + lose), never on
# win alone. A quarter line is a split bet and is not read.

def _team_outcome(total: CountDist, p: float, line: float, home: bool) -> tuple[float, float]:
    """(P(win), P(push)) for the team's OVER at `line`."""
    share = p if home else 1.0 - p
    if is_half_line(line):
        k, push_at = int(line + 0.5), None
    elif line_kind(line) == INTEGER:
        k, push_at = int(line) + 1, int(line)
    else:
        raise LadderError(f"line {line} is not an observation the split reads")
    win = push = 0.0
    for t, pt in _rows(total):
        row = _binomial_row(t, share)
        win += pt * sum(row[k:])
        if push_at is not None and push_at <= t:
            push += pt * row[push_at]
    return win, push


def _cover(total: CountDist, p: float, hcap: float) -> tuple[float, float]:
    """(P(cover), P(push)) for home + hcap against away: cover when
    2H > T - hcap, push when 2H == T - hcap (integer handicaps only)."""
    if not (is_half_line(abs(hcap)) or line_kind(abs(hcap)) == INTEGER):
        raise LadderError(f"handicap {hcap} is not an observation the split reads")
    cover = push = 0.0
    for t, pt in _rows(total):
        row = _binomial_row(t, p)
        edge = t - hcap
        for k, b in enumerate(row):
            if 2 * k > edge:
                cover += pt * b
            elif 2 * k == edge:
                push += pt * b
    return cover, push


def _conditional(win: float, push: float) -> float:
    """win / (win + lose): what a push-excluding quote prices."""
    denom = 1.0 - push
    return win / denom if denom > 1e-12 else 0.5


def share_from_team_line(total: CountDist, line: float, p_over: float, home: bool = True) -> float:
    """The share at which the model's price for the team's OVER at `line`
    equals Pinnacle's devigged p_over — push-aware on an integer line."""
    return _bisect(lambda p: _conditional(*_team_outcome(total, p, line, home)),
                   p_over, increasing=home)


def share_from_handicap(total: CountDist, hcap: float, p_cover: float) -> float:
    """The share at which the model's cover price for home + hcap equals the
    devigged quote — push-aware on an integer handicap."""
    return _bisect(lambda p: _conditional(*_cover(total, p, hcap)), p_cover, increasing=True)


@dataclass(frozen=True)
class ShareEstimate:
    p: float
    lo: float
    hi: float
    sources: dict = field(default_factory=dict)   # name -> p it implies
    flags: list = field(default_factory=list)

    @property
    def disagree(self) -> bool:
        return "share_disagreement" in self.flags

    @property
    def kinds(self) -> set[str]:
        """Which kinds of quote pinned the share: home_line, away_line, handicap."""
        return {name.split("@", 1)[0] for name in self.sources}


def estimate_share(total: CountDist, *, home_lines=(), away_lines=(),
                   handicaps=()) -> ShareEstimate | None:
    """Combine EVERY share-bearing line Pinnacle quotes into one share with an
    honest range: every two-sided .5 line on either team and every half-integer
    handicap, each as (line_or_handicap, devigged probability). One observation
    gives a range of +/- SOLO_HALF_WIDTH; several give their spread, and a
    spread beyond DISAGREE is flagged — the quotes contradict each other and the
    price should be read with that in mind (or not at all). `sources` records
    the share each quote implies, keyed "kind@line".
    """
    sources: dict[str, float] = {}
    for line, q in home_lines:
        sources[f"home_line@{line:g}"] = share_from_team_line(total, line, q, home=True)
    for line, q in away_lines:
        sources[f"away_line@{line:g}"] = share_from_team_line(total, line, q, home=False)
    for hcap, q in handicaps:
        sources[f"handicap@{hcap:g}"] = share_from_handicap(total, hcap, q)
    if not sources:
        return None
    ps = list(sources.values())
    p = sum(ps) / len(ps)
    flags: list[str] = []
    if len(ps) == 1:
        lo, hi = p - SOLO_HALF_WIDTH, p + SOLO_HALF_WIDTH
        flags.append("share_from_one_source")
    else:
        lo, hi = min(ps), max(ps)
        if hi - lo > DISAGREE:
            flags.append("share_disagreement")
    lo, hi = max(P_MIN, lo), min(P_MAX, hi)
    return ShareEstimate(p=min(max(p, P_MIN), P_MAX), lo=lo, hi=hi, sources=sources, flags=flags)


# --- pricing a team line off the split ---------------------------------------

def price_team_line(totals: list[CountDist], share: ShareEstimate, line: float,
                    home: bool, quoted_range: tuple[float, float] | None = None,
                    grace: float = 1.0) -> FairPrice:
    """FairPrice for one team line. `totals[0]` is the match fit's point law,
    the rest its dispersion band; the band in p is the share estimate's range.
    The fair is the point; the band is every combination of the two."""
    if not is_half_line(line):
        raise LadderError(f"team lines are x.5 only, got {line}")
    point = TeamSplit(totals[0], share.p)
    p_over = point.over(line, home)
    p_under = 1.0 - p_over
    overs, unders = [], []
    for total in totals:
        for p in (share.lo, share.p, share.hi):
            s = point if (total is totals[0] and p == share.p) else TeamSplit(total, p)
            po = s.over(line, home)
            overs.append(1.0 / po if po > 1e-9 else float("inf"))
            unders.append(1.0 / (1.0 - po) if po < 1.0 - 1e-9 else float("inf"))
    beyond = 0.0
    if quoted_range is not None:
        lo_line, hi_line = quoted_range
        if line < lo_line - grace:
            beyond = (lo_line - grace) - line
        elif line > hi_line + grace:
            beyond = line - (hi_line + grace)
    return FairPrice(
        line=line, kind=HALF, p_over=p_over, p_under=p_under, p_push=0.0,
        fair_over=_round(1.0 / p_over) if p_over > 1e-9 else float("inf"),
        fair_under=_round(1.0 / p_under) if p_under > 1e-9 else float("inf"),
        band_over=(_round(min(overs)), _round(max(overs))),
        band_under=(_round(min(unders)), _round(max(unders))),
        beyond_ladder=round(beyond, 2),
        flags=["team_split", *share.flags],
    )
