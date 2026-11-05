"""Removing a bookmaker's margin from a two-way market.

POWER IS THE POINT ESTIMATE (Oct 2026, measured). Tested on 156,667 Bet365
over/under 2.5 goals markets against their results, the logistic calibration
slope of each method's probabilities (1.00 = perfectly calibrated) was:

    multiplicative 1.109    shin 1.035    odds_ratio 1.030    power 0.998

and the ordering held in every margin band from 3% to 20%, and on the best
price across ~17 books (median margin 2%, Pinnacle-like): 1.059 / 1.032 /
1.031 / 1.019. Multiplicative removal spreads the margin evenly, but books load
it onto the longer price (the favourite-longshot pattern), so it overstates the
long side and understates the favourite — worst on exactly the lopsided end
rungs of a ladder, which set the tails. It was the default until Oct 2026 on
the strength of one small corner-market study.

THE BAND keeps the three margin-aware methods (`BAND_METHODS`), so devig
disagreement still widens the uncertainty band; multiplicative is out of the
band too, because it is demonstrably biased rather than plausibly right.

Each takes (over_odds, under_odds) as decimal prices and returns probabilities
summing to exactly 1.
"""
from math import sqrt

from .errors import LadderError

# Bisection bounds and tolerance. All three iterative methods are monotone in
# their single parameter over these ranges, so bisection is both sufficient and
# unconditionally stable — no derivatives, no failure to converge.
_TOL = 1e-12
_MAX_ITER = 200


def _implied(over_odds: float, under_odds: float) -> tuple[float, float]:
    if over_odds is None or under_odds is None:
        raise LadderError("both sides are required to remove a margin")
    if over_odds <= 1.0 or under_odds <= 1.0:
        raise LadderError(f"decimal odds must exceed 1.0, got {over_odds}/{under_odds}")
    return 1.0 / over_odds, 1.0 / under_odds


def overround(over_odds: float, under_odds: float) -> float:
    """The raw book margin: (1/over + 1/under) - 1."""
    q_over, q_under = _implied(over_odds, under_odds)
    return q_over + q_under - 1.0


def _bisect(f, lo: float, hi: float) -> float:
    """Root of a monotone f on [lo, hi]. Returns the bracket end on no sign change."""
    f_lo, f_hi = f(lo), f(hi)
    if f_lo == 0.0:
        return lo
    if f_hi == 0.0:
        return hi
    if f_lo * f_hi > 0:
        # No root in the bracket — the market is degenerate rather than the
        # solver being wrong. Return the end closest to a root; callers see the
        # result as a probability pair that still sums to 1.
        return lo if abs(f_lo) < abs(f_hi) else hi
    for _ in range(_MAX_ITER):
        mid = 0.5 * (lo + hi)
        f_mid = f(mid)
        if abs(f_mid) < _TOL or hi - lo < _TOL:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)


def multiplicative(over_odds: float, under_odds: float) -> tuple[float, float]:
    """Scale both implied probabilities by the same factor: p_i = q_i / sum(q).

    NOT the default (see the module docstring): it assumes the book loads its
    margin evenly across the two sides, which the outcome data contradicts.
    """
    q_over, q_under = _implied(over_odds, under_odds)
    total = q_over + q_under
    return q_over / total, q_under / total


def power(over_odds: float, under_odds: float) -> tuple[float, float]:
    """Solve for k where q_over^k + q_under^k = 1; p_i = q_i^k.

    Loads more margin onto the longer price, matching the favourite-longshot
    bias. THE DEFAULT, and the method the rest of Puntmore uses for 1X2
    (odds/utils.py): the best-calibrated of the four on two-way totals.
    """
    q_over, q_under = _implied(over_odds, under_odds)

    def f(k: float) -> float:
        return q_over ** k + q_under ** k - 1.0

    k = _bisect(f, 0.5, 3.0)
    p_over, p_under = q_over ** k, q_under ** k
    total = p_over + p_under
    return p_over / total, p_under / total


def shin(over_odds: float, under_odds: float) -> tuple[float, float]:
    """Shin's model: margin arises from a proportion z of insider money.

        p_i = (sqrt(z^2 + 4(1-z) q_i^2 / S) - z) / (2(1-z)),  S = sum(q)

    Solved for the z that makes the probabilities sum to 1.
    """
    q_over, q_under = _implied(over_odds, under_odds)
    total = q_over + q_under

    def p_of(q: float, z: float) -> float:
        if z <= 0.0:
            return q / total
        return (sqrt(z * z + 4.0 * (1.0 - z) * q * q / total) - z) / (2.0 * (1.0 - z))

    def f(z: float) -> float:
        return p_of(q_over, z) + p_of(q_under, z) - 1.0

    z = _bisect(f, 0.0, 0.35)
    p_over, p_under = p_of(q_over, z), p_of(q_under, z)
    s = p_over + p_under
    return p_over / s, p_under / s


def odds_ratio(over_odds: float, under_odds: float) -> tuple[float, float]:
    """Cheung's odds-ratio method: p_i = q_i / (c + q_i - c*q_i), solved for c.

    c = 1 recovers the raw implied probabilities, so c is a single number
    describing how the book's odds relate to fair ones across the whole market.
    """
    q_over, q_under = _implied(over_odds, under_odds)

    def p_of(q: float, c: float) -> float:
        return q / (c + q - c * q)

    def f(c: float) -> float:
        return p_of(q_over, c) + p_of(q_under, c) - 1.0

    c = _bisect(f, 1e-3, 100.0)
    p_over, p_under = p_of(q_over, c), p_of(q_under, c)
    total = p_over + p_under
    return p_over / total, p_under / total


METHODS = {
    "multiplicative": multiplicative,
    "power": power,
    "shin": shin,
    "odds_ratio": odds_ratio,
}


# Refitted under in price.py to widen the band. Multiplicative is excluded on
# the evidence above; it stays in METHODS for comparison and tests.
BAND_METHODS = ("power", "shin", "odds_ratio")


def devig(over_odds: float, under_odds: float, method: str = "power"):
    try:
        fn = METHODS[method]
    except KeyError:
        raise LadderError(f"unknown devig method: {method}") from None
    return fn(over_odds, under_odds)


def synthesise_missing_side(
    quoted_odds: float, quoted_is_over: bool, median_overround: float
) -> float:
    """The absent side of a one-sided rung, at half the ladder's median margin.

    A rung quoted on one side only carries no margin information of its own, so
    the ladder's median margin stands in — half of it applied to the missing
    side, on the assumption the book would have split its margin evenly. The
    resulting rung is marked `synthetic_side` and down-weighted to a quarter in
    the fit: it is a reconstruction, not an observation, and must never carry
    the same weight as a price the book actually published.
    """
    if quoted_odds is None or quoted_odds <= 1.0:
        raise LadderError("cannot synthesise from a missing or invalid price")
    q_quoted = 1.0 / quoted_odds
    # The pair must imply 1 + margin in total; the quoted side keeps its own
    # price and the remainder falls to the synthesised one.
    target_total = 1.0 + max(median_overround, 0.0)
    q_other = target_total - q_quoted
    if q_other <= 1e-6:
        raise LadderError("quoted side already exceeds the implied book")
    return 1.0 / q_other
