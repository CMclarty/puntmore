"""Where a slip's EV came from: which fair-price source, and the offer.

A slip's EV (aw26/offers.py) is fixed in pounds. It splits as

    offer  = EV − plain EV          the refund/boost's own worth (0 off-terms)
    plain  = S·(∏ odds / ∏ fair − 1)     the same legs as a straight acca

and the plain part is shared among the legs by their LOG edge,
ln(odds / fair): log edges add across an acca exactly as the legs multiply,
so a leg's share is its part of the acca's total edge. Each leg's share goes to
the source that priced it (service.fair_source: paddy | bb | ...). When the
legs' log edges cancel (no net edge) the plain part splits evenly by leg.

The split always sums back to the slip's EV to the penny; a share can be
negative (a leg taken below fair drags the acca down).
"""
from math import log, prod


def leg_edge(leg) -> float | None:
    """odds / fair − 1 for one leg, or None if either is missing."""
    if leg.odds is None or not leg.fair_odds:
        return None
    return leg.odds / leg.fair_odds - 1


def ev_by_source(slip) -> dict[str, float] | None:
    """{source: £} summing to slip.ev, or None while the slip is unpriced."""
    if slip.ev is None or not slip.legs or slip.stake is None:
        return None
    legs = list(slip.legs)
    if any(leg.odds is None or not leg.fair_odds for leg in legs):
        return None
    plain = slip.stake * (prod(leg.odds for leg in legs) / prod(leg.fair_odds for leg in legs) - 1)
    out: dict[str, float] = {}
    offer = slip.ev - plain
    if abs(offer) >= 0.005:
        out["offer"] = offer
    weights = [log(leg.odds / leg.fair_odds) for leg in legs]
    total = sum(weights)
    for leg, w in zip(legs, weights):
        share = w / total if abs(total) > 1e-12 else 1 / len(legs)
        src = leg.fair_source or "unknown"
        out[src] = out.get(src, 0.0) + plain * share
    rounded = {k: round(v, 2) for k, v in out.items()}
    # Pennies lost to rounding go on the biggest share, so the split reconciles.
    diff = round(slip.ev - sum(rounded.values()), 2)
    if diff:
        big = max(rounded, key=lambda k: abs(rounded[k]))
        rounded[big] = round(rounded[big] + diff, 2)
    return rounded


def add_up(slips) -> dict[str, float]:
    """ev_by_source summed over `slips` (unpriced slips add nothing)."""
    out: dict[str, float] = {}
    for slip in slips:
        for k, v in (ev_by_source(slip) or {}).items():
            out[k] = round(out.get(k, 0.0) + v, 2)
    return out
