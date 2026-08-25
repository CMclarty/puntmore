"""Cards markets — the same pipeline, plus one thing that must not be guessed.

WHAT UNIT IS THE MARKET IN? Two conventions are in circulation and they are not
interchangeable:

    COUNT   the number of cards. Central lines around 3.5 - 5.5.
    POINTS  booking points, 10 per yellow and 25 per red. Central lines 35 - 55.

Detected from the central line: below 15 is a count market, at or above 15 is
points. That gap is wide enough to be unambiguous in practice.

POINTS MARKETS ARE REFUSED, NOT APPROXIMATED. A points ladder cannot be fitted
with a count distribution: the support is lumpy (multiples of 10 with 25-jumps),
so a smooth count model fitted to it produces prices that look reasonable and are
wrong in a structured way — worst at exactly the tail lines where the edges
appear. Pricing them properly needs yellows and reds modelled separately and
convolved onto a points grid, which is a different model and, more importantly,
one that cannot be validated until a real points sheet has been read: the
conventions differ on how a second yellow plus red is scored, and picking wrong
is a red-rate-sized bias. Refusing is the same rule the coupon tracker follows —
an unpriced market beats a confidently wrong one.

COUNT MARKETS still carry an ambiguity that IS priced through, with a flag:
whether a red counts as one card (`count_simple`) or two (`count_weighted`).
Metadata rarely says. The difference is a red-rate-sized bias, so it is fitted as
count_simple and FLAGGED rather than silently assumed.
"""
from .config import CARDS, ModelConfig
from .errors import LadderError
from .fit import FitResult, fit_ladder
from .ladder import Ladder

COUNT_SIMPLE = "count_simple"
COUNT_WEIGHTED = "count_weighted"
POINTS_10_25 = "points_10_25"

# Above this central line the market is booking points, not a card count.
POINTS_THRESHOLD = 15.0


def detect_unit(ladder: Ladder) -> str:
    """The unit a cards ladder is quoted in, from its central line."""
    if ladder.card_unit:
        return ladder.card_unit
    lines = [r.line for r in ladder.rungs if r.over or r.under]
    if not lines:
        raise LadderError(f"{ladder.fixture_id}: no usable rung")
    central = sorted(lines)[len(lines) // 2]
    return POINTS_10_25 if central >= POINTS_THRESHOLD else COUNT_SIMPLE


def fit_cards(ladder: Ladder, config: ModelConfig = CARDS) -> FitResult:
    """Fit a cards ladder, refusing the units this model cannot honestly price."""
    unit = detect_unit(ladder)

    if unit == POINTS_10_25:
        raise LadderError(
            f"{ladder.fixture_id}: booking-points ladder (central line >= "
            f"{POINTS_THRESHOLD}) — a count distribution cannot describe a lumpy "
            f"10/25 support. Needs a yellows+reds convolution, which is not built "
            f"because the second-yellow convention has not been read off a real "
            f"sheet. Refusing rather than returning a plausible wrong price."
        )

    result = fit_ladder(ladder, config)
    if not ladder.card_unit:
        # The caller asked us to detect, so say what was assumed. A red counted
        # as two cards shifts the whole distribution, and nothing downstream can
        # tell from the price alone.
        result.flags.append(f"card_unit_assumed:{COUNT_SIMPLE}")
    return result
