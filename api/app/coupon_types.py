"""The seven coupon types — the app's top-level entities.

Each type is a standing market, not an accumulator: it owns a filtered list of
fixtures with the syndicate's prices against fair odds. A photo of a given
coupon feeds that type's list. See SPEC §3.

Single source of truth: the API serves this to the frontend so the dropdown and
the upload form cannot drift apart.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CouponType:
    code: str
    label: str
    # Selection template — how a pick on this coupon reads once the team is
    # filled in. Pricing resolves the market from the pick text, so this is
    # also what OCR should be producing.
    selection: str
    # Fixtures are only worth listing when the price is short enough to be
    # worth taking. None = no filter, rank on value alone.
    max_fair_odds: float | None = None


COUPON_TYPES: tuple[CouponType, ...] = (
    CouponType("TTS1", "Team to Score 1", "{team} to Score 1"),
    CouponType("TTS2", "Team to Score 2", "{team} to Score 2"),
    CouponType("TTS3", "Team to Score 3", "{team} to Score 3"),
    CouponType("WO15", "Win and Over 1.5", "{team} Win and Over 1.5"),
    CouponType("WO25", "Win and Over 2.5", "{team} Win and Over 2.5"),
    # GG Bonus is a screen: only fixtures where both teams scoring is likely
    # enough to be worth taking (fair odds <= 1.80), shortest first (SPEC §8.5).
    CouponType("GGB", "GG Bonus", "GG Bonus", max_fair_odds=1.80),
    CouponType("GGND", "GG No Draw", "GG No Draw"),
)

BY_CODE = {t.code: t for t in COUPON_TYPES}


def get(code: str) -> CouponType | None:
    return BY_CODE.get(code)


def is_valid(code: str) -> bool:
    return code in BY_CODE
