"""De-vig utilities — power method.

The bookmaker margin is removed by solving for the exponent k such that the
de-vigged probabilities sum to exactly 1:

    q_i = 1 / price_i                  (implied probability, with vig)
    find k:  sum(q_i ** k) = 1         (k > 1 when there is overround)
    fair_prob_i  = q_i ** k
    fair_price_i = price_i ** k

Unlike the simpler multiplicative method (fair = price × total_impl), the
power method removes relatively more vig from longshots and less from
favourites, matching how books actually distribute their margin
(favourite–longshot bias). For symmetric prices the two methods agree.

`margin` returned everywhere is the raw overround (sum(1/price) − 1) — a
property of the bookmaker's prices, independent of the de-vig method.
"""


def devigd_multi(prices: list[float]) -> tuple[list[float], float]:
    """Remove vig from an n-outcome market via the power method.
    Returns (fair_prices, margin) where margin = total_implied_probability - 1.
    """
    impl = [1 / p for p in prices]
    margin = sum(impl) - 1

    def overround(k: float) -> float:
        return sum(q ** k for q in impl) - 1.0

    # sum(q**k) is strictly decreasing in k (0 < q < 1): bracket, then bisect.
    lo = hi = 1.0
    if overround(1.0) > 0.0:
        hi = 2.0
        while overround(hi) > 0.0 and hi < 1e6:
            hi *= 2.0
    elif overround(1.0) < 0.0:
        lo = 0.5
        while overround(lo) < 0.0 and lo > 1e-9:
            lo /= 2.0

    while hi - lo > 1e-13:
        mid = (lo + hi) / 2.0
        if overround(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0

    return [p ** k for p in prices], margin


def devigd(price_a: float, price_b: float) -> tuple[float, float, float]:
    """Remove vig from a two-outcome market (power method).
    Returns (fair_a, fair_b, margin) where margin = total_implied_probability - 1.
    """
    fair, margin = devigd_multi([price_a, price_b])
    return fair[0], fair[1], margin
