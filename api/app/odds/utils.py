def devigd(price_a: float, price_b: float) -> tuple[float, float, float]:
    """Remove vig from a two-outcome market.
    Returns (fair_a, fair_b, margin) where margin = total_implied_probability - 1.
    """
    total_impl = (1 / price_a) + (1 / price_b)
    return price_a * total_impl, price_b * total_impl, total_impl - 1
