"""The coupon types — the app's top-level entities.

Each type is a standing market, not an accumulator: it owns a filtered list of
fixtures with fair odds against the coupon's own terms. A photo of a given
coupon feeds that type's list. See SPEC §3.

Two behaviours, and a coupon may have EITHER OR BOTH — they are separate
flags precisely because a real coupon showed up carrying both:

  PRICED   (prices_each_selection) — each fixture carries its own printed odds.
           Value is per selection: your price against fair.

  LADDER   (pays_ladder) — getting N selections correct pays a fixed return
           read off a printed ladder ("All 3 correct 9/2"). That value belongs
           to the COMBINATION, never to a row, and the best N is the N shortest
           fair prices.

TTS1/2/3 and WO15/WO25 are priced only. The Betfred GG Bonus / GG No Draw
coupons are ladder only — they genuinely print no per-fixture price. The
William Hill "Both score no draw" (HILLS) is BOTH: it prints a price on every
fixture AND a ladder, so either route is bettable off one sheet. These were one
boolean until that coupon proved the two are independent; folding them back
together would silently discard one half of it again.

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
    # Does the coupon print a price against each fixture? False means any
    # per-row number would be invented, so none is stored.
    prices_each_selection: bool = True
    # Does it pay a fixed return for N correct, off a printed ladder? Independent
    # of the above: a coupon can print prices AND pay a ladder.
    pays_ladder: bool = False
    # Fallback ladder for when OCR cannot read the printed one. The ladder is
    # printed on each issue and CAN change, so the coupon's own reading always
    # wins — this only stops a bad scan leaving a coupon unvaluable.
    default_ladder: dict[int, float] = field(default_factory=dict)
    # Fixtures are only worth listing when the price is short enough to be
    # worth taking. None = no filter, rank on value alone.
    max_fair_odds: float | None = None
    # The heading of this market's column block on a priced grid sheet, as
    # printed. One sheet prints several look-alike blocks side by side, so OCR
    # is told which one was photographed for rather than left to choose.
    board_label: str | None = None
    # Does that block print a separate Home and Away price per fixture? Those
    # coupons yield TWO priced legs per row, one per team.
    two_sided: bool = False


# LADDERS ARE PER BOOKMAKER AND PER ISSUE. Each type carries its own fallback,
# and the coupon's own scanned ladder always wins over it — these only stop a
# bad scan leaving a coupon unvaluable. Never share one between bookmakers: the
# Hill and Betfred boards below agree at 3 and 4 correct and then diverge to
# 3.4x apart by 12, so the wrong default misprices a long fold badly.
#
# Ladder observed on the Betfred GG Bonus midweek issue, 20–23 July 2026.
# Fractional as printed: 9/2, 9/1, 16/1, 28/1, 50/1, 100/1, 175/1, 300/1,
# 500/1, 800/1, 1500/1. The 14- and 15-fold rungs were cut off by the photo
# edge and are deliberately absent rather than guessed.
_GG_LADDER = {
    3: 5.5, 4: 10.0, 5: 17.0, 6: 29.0, 7: 51.0,
    8: 101.0, 9: 176.0, 10: 301.0, 11: 501.0, 12: 801.0, 13: 1501.0,
}

# Ladder printed on the William Hill "Both score no draw" issue of Sat 25 /
# Sun 26 July 2026. Fractional as printed: 9/2, 9/1, 20/1, 41/1, 84/1, 169/1,
# 339/1, 680/1, 1361/1, 2723/1 — the sheet stops at 12.
#
# Hill prints these as "Minimum odds", i.e. the return can settle ABOVE a rung.
# The model treats a rung as the exact return, so a Hills combination's value is
# a floor rather than a point estimate. Recorded here because that is what the
# coupon says, not adjusted for.
_WH_LADDER = {
    3: 5.5, 4: 10.0, 5: 21.0, 6: 42.0, 7: 85.0,
    8: 170.0, 9: 340.0, 10: 681.0, 11: 1362.0, 12: 2724.0,
}

_VARIABLE = ("MIDWEEK", "WEEKEND")

COUPON_TYPES: tuple[CouponType, ...] = (
    # The five priced types are all two-sided grids on the William Hill sheets:
    # one block per market, split into H and A columns (verified against the
    # 29–31 Jul 2026 "Team to score" and "Match winner" issues).
    CouponType("TTS1", "Team to Score 1", "{team} to Score 1",
               board_label="TEAM TO SCORE 1 OR MORE GOALS", two_sided=True),
    CouponType("TTS2", "Team to Score 2", "{team} to Score 2",
               board_label="TEAM TO SCORE 2 OR MORE GOALS", two_sided=True),
    CouponType("TTS3", "Team to Score 3", "{team} to Score 3",
               board_label="TEAM TO SCORE 3 OR MORE GOALS", two_sided=True),
    CouponType("WO15", "Win and Over 1.5", "{team} Win and Over 1.5",
               board_label="WIN & 2 OR MORE GOALS", two_sided=True),
    CouponType("WO25", "Win and Over 2.5", "{team} Win and Over 2.5",
               board_label="WIN & 3 OR MORE GOALS", two_sided=True),
    # The third block on Hill's "Match winner" sheet. Priced off the score grid
    # (win ∧ opponent scores 0), never moneyline × clean sheet.
    CouponType("WTN", "Win to Nil", "{team} Win to Nil",
               board_label="WIN TO NIL", two_sided=True),
    CouponType(
        "GGB", "GG Bonus", "GG Bonus",
        period_codes=_VARIABLE, prices_each_selection=False, pays_ladder=True,
        default_ladder=_GG_LADDER, max_fair_odds=1.80,
    ),
    CouponType(
        "GGND", "GG No Draw", "GG No Draw",
        period_codes=_VARIABLE, prices_each_selection=False, pays_ladder=True,
        default_ladder=_GG_LADDER,
    ),
    # William Hill's "Both score no draw". Same MARKET as GGND — the selection
    # string is identical so it prices through exactly the same path — but its
    # own type because the sheet is a different shape: it prints a price on
    # every fixture and its ladder is Hill's, not Betfred's.
    #
    # It runs Hill's WEEKLY schedule, not the GG coupons' variable midweek /
    # weekend issues: it is a Hill sheet like the priced grids above it, and is
    # issued on the same Mon–Wed / Wed–Fri / Sat / Sun windows. Only the Betfred
    # GG coupons print their own moving dates.
    CouponType(
        "HILLS", "Hills GGND", "GG No Draw",
        prices_each_selection=True, pays_ladder=True,
        default_ladder=_WH_LADDER,
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
