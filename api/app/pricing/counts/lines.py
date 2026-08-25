"""Turning quoted lines into constraints on the count distribution.

Three line types, and they are NOT the same constraint. Collapsing them is the
easy mistake — an integer line has a push, so its devigged price is a ratio
between two conditional outcomes, not a level.

    HALF LINE (x.5)      no push. P(N >= ceil(L)) = p_over.

    INTEGER LINE (x.0)   stake returned when N == L exactly. The devig is over
                         the two NON-PUSH outcomes, so it constrains a RATIO:
                             P(N > L) / (P(N > L) + P(N < L)) = p_over
                         Treating this as a level overstates the over side by
                         the whole push probability.

    QUARTER LINE (x.25)  half the stake on each adjacent line. The constraint is
                         not a probability at all: it is that the model's fair
                         price for the split bet matches the devigged quote. For
                         a stake of 1 split evenly at decimal odds o, the
                         expected return is
                             0.5 * (E1 + E2),  E_i = o * A_i + B_i
                         where A_i is the win probability of leg i and B_i its
                         push probability. Fair means that equals 1, so
                             o = (2 - (B1 + B2)) / (A1 + A2)
                         and the residual is taken in probability space as
                         1/o against the devigged 1/quote.
"""
from dataclasses import dataclass
from math import ceil, floor, isclose

from .dist import CountDist
from .errors import LadderError

HALF = "half"
INTEGER = "integer"
QUARTER = "quarter"


def line_kind(line: float) -> str:
    frac = round(line - floor(line), 4)
    if isclose(frac, 0.0, abs_tol=1e-9):
        return INTEGER
    if isclose(frac, 0.5, abs_tol=1e-9):
        return HALF
    if isclose(frac, 0.25, abs_tol=1e-9) or isclose(frac, 0.75, abs_tol=1e-9):
        return QUARTER
    raise LadderError(f"unsupported line: {line}")


@dataclass(frozen=True)
class Constraint:
    """One rung, reduced to something the fit can take a residual against."""

    line: float
    kind: str
    target: float          # devigged p_over, or 1/fair_odds for a quarter line
    overround: float
    synthetic_side: bool
    threshold: float       # count the line sits at, for distance weighting


def _leg(dist: CountDist, line: float, over: bool) -> tuple[float, float]:
    """(win probability, push probability) for one side of a half/integer line."""
    kind = line_kind(line)
    if kind == HALF:
        if over:
            return dist.sf(int(ceil(line))), 0.0
        return dist.cdf(int(floor(line))), 0.0
    if kind == INTEGER:
        k = int(round(line))
        if over:
            return dist.sf(k + 1), dist.pmf(k)
        return dist.cdf(k - 1), dist.pmf(k)
    raise LadderError(f"{line} is not a single line")


def model_probability(dist: CountDist, line: float, over: bool = True) -> float:
    """What the model says this line's devigged price should be.

    The value is comparable to a devigged quote for the SAME line type — which
    is the only comparison that means anything, since the three types are
    different quantities.
    """
    kind = line_kind(line)
    if kind == HALF:
        win, _ = _leg(dist, line, over)
        return win
    if kind == INTEGER:
        win, push = _leg(dist, line, over)
        lose = 1.0 - win - push
        denom = win + lose
        if denom <= 0:
            return 0.5
        return win / denom
    # Quarter: reduce to the fair price of the split bet, then invert.
    return 1.0 / fair_quarter_odds(dist, line, over)


def fair_quarter_odds(dist: CountDist, line: float, over: bool = True) -> float:
    """Fair decimal odds for a quarter line, as the half-stake pair it really is.

    A 9.25 over is half a stake on 9.0 and half on 9.5; the 9.0 leg pushes on
    exactly 9. Solving 0.5*(o*A1 + B1 + o*A2 + B2) = 1 gives the price below.
    """
    if line_kind(line) != QUARTER:
        raise LadderError(f"{line} is not a quarter line")
    lo, hi = line - 0.25, line + 0.25
    a1, b1 = _leg(dist, lo, over)
    a2, b2 = _leg(dist, hi, over)
    denom = a1 + a2
    if denom <= 0:
        raise LadderError(f"quarter line {line} has no winning outcome")
    return (2.0 - (b1 + b2)) / denom


def threshold_of(line: float) -> float:
    """The count a line separates, used for distance-from-centre weighting."""
    return float(line)
