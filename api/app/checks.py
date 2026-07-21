"""Per-leg sanity checks (SPEC §7).

OCR auto-confirms, so nothing human looks at a row before it is saved and
priced. These checks are the safety net: they never block a save, they attach
flag codes to the leg so the UI can badge it with ⚠ and the syndicate can
eyeball the ones worth eyeballing.

Flags are advisory. A flagged leg still prices and still alerts — the flag says
"this one is worth a look", not "this one is wrong".
"""
import re

# Plausible odds range per market shape. OCR misreads decimals far more often
# than it invents matches: "1.85" scanned as "185" or "18.5" is the common
# failure, and a per-market range catches it where a global range would not.
# Ordered — first pattern that matches the pick wins.
_ODDS_RANGES: list[tuple[re.Pattern, tuple[float, float]]] = [
    (re.compile(r"\bwin\b.*\b(?:over|under)\s+[\d.]+", re.I), (1.5, 26.0)),
    (re.compile(r"\bboth teams to score\b|\bbtts\b", re.I), (1.2, 6.0)),
    (re.compile(r"\b(?:over|under)\s+[\d.]+", re.I), (1.1, 21.0)),
    (re.compile(r"\bdraw no bet\b|\bdnb\b", re.I), (1.05, 11.0)),
    (re.compile(r"\bto score\b", re.I), (1.1, 21.0)),
]
_DEFAULT_ODDS_RANGE = (1.01, 51.0)

# Above this edge the price is far more likely to be a bad OCR read or a wrong
# fixture match than a real 15%+ overlay. Provisional, same standing as the 8%
# derived threshold — replace with a measured number when the backtest lands
# (PLAN open item 3).
VALUE_PLAUSIBILITY_CAP = 0.15

# Mirrors the provider's own confident-match bar. Below it the fixture was
# matched but loosely enough that a human should confirm it is the right game.
MATCH_CONFIDENCE_FLOOR = 0.75

FLAG_ODDS_RANGE = "odds_range"
FLAG_NO_MATCH = "no_match"
FLAG_LOW_CONFIDENCE = "low_confidence"
FLAG_VALUE_IMPLAUSIBLE = "value_implausible"


def odds_range_for(pick: str) -> tuple[float, float]:
    for pattern, bounds in _ODDS_RANGES:
        if pattern.search(pick):
            return bounds
    return _DEFAULT_ODDS_RANGE


def evaluate_leg(
    pick: str,
    odds: float | None,
    *,
    value: float | None = None,
    matched_fixture: str | None = None,
    match_score: float | None = None,
) -> list[str]:
    """Flag codes for one leg. Empty list means nothing looked wrong."""
    flags: list[str] = []

    # No odds is normal on flat-payout coupons, not an anomaly.
    if odds is not None:
        low, high = odds_range_for(pick)
        if odds < low or odds > high:
            flags.append(FLAG_ODDS_RANGE)

    if matched_fixture is None:
        flags.append(FLAG_NO_MATCH)
    elif match_score is not None and match_score < MATCH_CONFIDENCE_FLOOR:
        flags.append(FLAG_LOW_CONFIDENCE)

    if value is not None and abs(value) > VALUE_PLAUSIBILITY_CAP:
        flags.append(FLAG_VALUE_IMPLAUSIBLE)

    return flags


def encode_flags(flags: list[str]) -> str | None:
    """Flags -> storage form. None (not "") so an unflagged leg reads as null."""
    return ",".join(flags) if flags else None


def decode_flags(raw: str | None) -> list[str]:
    return [f for f in (raw or "").split(",") if f]
