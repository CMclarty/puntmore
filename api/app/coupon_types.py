"""The seven coupon types — the app's top-level entities.

Each type is a standing market, not an accumulator: it owns a filtered list of
fixtures with fair odds against the coupon's own terms. A photo of a given
coupon feeds that type's list. See SPEC §3.

Two shapes exist, and they price completely differently:

  PRICED   (TTS1/2/3, WO15/WO25) — each fixture carries its own printed odds.
           Value is per selection: your price against fair.

  LADDER   (GG Bonus, GG No Draw) — no odds are printed per fixture. Getting N
           selections correct pays a fixed return read off a printed ladder
           ("All 3 correct 9/2"). Value belongs to the COMBINATION, never to a
           row, and the best N is the N shortest fair prices.

Single source of truth: the API serves this to the frontend so the dropdown and
the upload form cannot drift apart.
"""
from dataclasses import dataclass, field
from math import prod


@dataclass(frozen=True)
class CouponType:
    code: str
    label: str
    # Selection template — how a pick on this coupon reads once the team is
    # filled in. Pricing resolves the market from the pick text, so this is
    # also what a row's market becomes when the coupon prints none.
    selection: str
    # Which period codes apply (coupon_periods.py). The priced coupons run to a
    # fixed weekly schedule; the GG coupons are issued midweek/weekend on dates
    # that move, so they carry their own.
    period_codes: tuple[str, ...] = ("MON_WED", "WED_FRI", "SAT", "SUN")
    # False when the coupon prints no odds per fixture and pays off a ladder.
    prices_each_selection: bool = True
    # Fallback ladder for when OCR cannot read the printed one. The ladder is
    # printed on each issue and CAN change, so the coupon's own reading always
    # wins — this only stops a bad scan leaving a coupon unvaluable.
    default_ladder: dict[int, float] = field(default_factory=dict)
    # Fixtures are only worth listing when the price is short enough to be
    # worth taking. None = no filter, rank on value alone.
    max_fair_odds: float | None = None


# Ladder observed on the Betfred GG Bonus midweek issue, 20–23 July 2026.
# Fractional as printed: 9/2, 9/1, 16/1, 28/1, 50/1, 100/1, 175/1, 300/1,
# 500/1, 800/1, 1500/1. The 14- and 15-fold rungs were cut off by the photo
# edge and are deliberately absent rather than guessed.
_GG_LADDER = {
    3: 5.5, 4: 10.0, 5: 17.0, 6: 29.0, 7: 51.0,
    8: 101.0, 9: 176.0, 10: 301.0, 11: 501.0, 12: 801.0, 13: 1501.0,
}

_VARIABLE = ("MIDWEEK", "WEEKEND")

COUPON_TYPES: tuple[CouponType, ...] = (
    CouponType("TTS1", "Team to Score 1", "{team} to Score 1"),
    CouponType("TTS2", "Team to Score 2", "{team} to Score 2"),
    CouponType("TTS3", "Team to Score 3", "{team} to Score 3"),
    CouponType("WO15", "Win and Over 1.5", "{team} Win and Over 1.5"),
    CouponType("WO25", "Win and Over 2.5", "{team} Win and Over 2.5"),
    CouponType(
        "GGB", "GG Bonus", "GG Bonus",
        period_codes=_VARIABLE, prices_each_selection=False,
        default_ladder=_GG_LADDER, max_fair_odds=1.80,
    ),
    CouponType(
        "GGND", "GG No Draw", "GG No Draw",
        period_codes=_VARIABLE, prices_each_selection=False,
        default_ladder=_GG_LADDER,
    ),
)

BY_CODE = {t.code: t for t in COUPON_TYPES}


def get(code: str) -> CouponType | None:
    return BY_CODE.get(code)


def is_valid(code: str) -> bool:
    return code in BY_CODE


def combination_value(fair_odds: list[float], payout: float) -> float | None:
    """Edge on a combination: the fixed return against its fair price.

    An N-fold's fair price is the product of its legs' fair odds, so a printed
    return is value whenever that product is below it. Returns None if any leg
    is unpriced — a combination is only as knowable as its worst leg.
    """
    if not fair_odds or payout is None or any(f is None or f <= 0 for f in fair_odds):
        return None
    return round(payout / prod(fair_odds) - 1, 4)


def best_combination(fair_odds: list[float], ladder: dict[int, float]) -> dict | None:
    """The best-value combination available across every rung of the ladder.

    Longer combinations pay more but multiply in more risk, and which wins is
    not obvious from either side — so every rung the coupon offers is evaluated
    and the best is returned, rather than assuming the 3-fold.

    `fair_odds` is every priced fixture on the coupon; the best N-fold is always
    the N SHORTEST, since that minimises the product the fixed return is
    measured against.
    """
    priced = sorted(f for f in fair_odds if f is not None and f > 0)
    if not priced or not ladder:
        return None

    best = None
    for picks, payout in sorted(ladder.items()):
        if picks > len(priced):
            break  # not enough fixtures on the coupon to fill this rung
        value = combination_value(priced[:picks], payout)
        if value is None:
            continue
        if best is None or value > best["value"]:
            best = {
                "picks": picks,
                "payout": payout,
                "fair": round(prod(priced[:picks]), 4),
                "value": value,
            }
    return best
