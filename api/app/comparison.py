def compute_value(your_odds: float, fair_odds: float) -> float:
    """Edge as a fraction. +0.10 means 10% value in your favour."""
    return round(your_odds / fair_odds - 1, 4)
