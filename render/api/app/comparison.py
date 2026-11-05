import re


def compute_value(your_odds: float, fair_odds: float) -> float:
    """Edge as a fraction. +0.10 means 10% value in your favour."""
    return round(your_odds / fair_odds - 1, 4)


def value_tier(value: float | None) -> str | None:
    """Tier for a leg's value: 'hi' ≥ +6%, 'mid' ≥ +4%, 'lo' > 0, else None.

    Cutoffs mirror the frontend badge tiers (TrackerPage.valueTier).
    """
    if value is None or value <= 0:
        return None
    if value >= 0.06:
        return "hi"
    if value >= 0.04:
        return "mid"
    return "lo"


_COMPOUND_RE = re.compile(r"\bwin\b.*\b(?:over|under)\s+[\d.]+", re.IGNORECASE)


def is_compound_pick(pick: str) -> bool:
    """True for combined win+totals picks (e.g. "Win and Over 2.5").

    Their fair odds are h2h × totals — an independence approximation, since
    match result and goal totals are correlated. Surfaced so the UI can badge
    these values as approximate.
    """
    return bool(_COMPOUND_RE.search(pick))
