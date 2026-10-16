"""Pricing any line off a fitted ladder, with an honest uncertainty band.

THE BAND IS THE POINT, not decoration. The edges this tool hunts are a few
percent; the disagreement between devig methods alone is of the same order. A
point estimate quoted to two decimal places implies a precision the model does
not have, so every price carries the range it could plausibly take:

    full ladder   4 devig methods x D in {fit, fit +/- 0.03}   = 12 refits
    sparse ladder 4 devig methods x D in {prior, prior +/- 0.10} = 12 refits

The sparse band is wider because the dispersion is a stale league average rather
than this fixture's market, and the price should say so.

EXTRAPOLATION IS TRACKED SEPARATELY FROM THE BAND. A line beyond Pinnacle's
quoted range is not merely less certain — it is a different KIND of number. The
band covers parameter uncertainty within the model; it says nothing about the
distribution being the wrong shape in a tail nothing constrains. `beyond_ladder`
records the distance in counts, and `edge_vs` raises the bar accordingly, because
the biggest apparent edges will appear exactly there and most of them will be
tail-shape error rather than a soft price.
"""
from dataclasses import dataclass, field

from .config import ModelConfig
from .errors import LadderError
from .devig import BAND_METHODS
from .dist import CountDist, MIN_DISPERSION
from .fit import FitResult, fit_ladder
from .ladder import Ladder
from .lines import HALF, INTEGER, QUARTER, line_kind, model_probability

# Odds are quoted to two decimal places, so that is what is returned. The fit
# runs at full float64 precision; only the presented price is rounded.
_DP = 2

# Floor on the side probability the extrapolation penalty divides by, so a
# near-impossible side cannot produce an unbounded penalty.
_MIN_SIDE_P = 0.02


def _round(value: float) -> float:
    return round(value, _DP)


@dataclass
class FairPrice:
    line: float
    kind: str
    p_over: float
    p_under: float
    p_push: float
    fair_over: float
    fair_under: float
    # (low, high) fair odds across the refit sweep, per side.
    band_over: tuple[float, float] = (0.0, 0.0)
    band_under: tuple[float, float] = (0.0, 0.0)
    # Counts beyond the quoted range; 0.0 means inside it.
    beyond_ladder: float = 0.0
    flags: list[str] = field(default_factory=list)

    @property
    def extrapolated(self) -> bool:
        return self.beyond_ladder > 0.0

    def band_width(self, side: str = "over") -> float:
        """Band width as a fraction of the fair price — the honest error bar."""
        lo, hi = self.band_over if side == "over" else self.band_under
        fair = self.fair_over if side == "over" else self.fair_under
        if fair <= 0:
            return float("inf")
        return (hi - lo) / fair

    def edge_vs(
        self,
        quote_odds: float,
        side: str = "over",
        require_band: bool = True,
        config: ModelConfig | None = None,
        allow_negative: bool = False,
    ) -> float | None:
        """Edge against a book's quote, or None if it does not clear the bar.

        `require_band=True` (the default, and it should stay the default) demands
        the quote beat the WHOLE band rather than the midpoint. On a point
        estimate a 3% edge against a price whose band is +/-5% is noise wearing
        a number; against the band's far edge it is at least a claim the model
        can support.

        Beyond the quoted range the bar rises further: a probability error of
        `extrapolation_prob_penalty` per count of distance, converted to edge on
        THIS side (divided by its win probability). Tail-shape error is a
        probability error, so it costs a long shot far more than the short
        favourite opposite it — one flat percentage charged both alike.

        `allow_negative` returns a below-fair edge instead of None, for callers
        that apply their own floor. The acca offers need it: a 1.30 leg at ~94%
        of fair still belongs in a +EV insured 5-fold, and a hard floor at fair
        here silently overruled the min_edge the Paddy page asked for (Oct 2026).
        The band and the extrapolation penalty apply exactly as before.
        """
        if quote_odds is None or quote_odds <= 1.0:
            return None
        fair = self.fair_over if side == "over" else self.fair_under
        if fair <= 0:
            return None

        reference = fair
        if require_band:
            lo, hi = self.band_over if side == "over" else self.band_under
            reference = hi          # the least favourable fair price in the band
        edge = quote_odds / reference - 1.0

        if config is not None and self.beyond_ladder > 0:
            p_side = self.p_over if side == "over" else self.p_under
            edge -= (config.extrapolation_prob_penalty * self.beyond_ladder
                     / max(p_side, _MIN_SIDE_P))
        return edge if (edge > 0 or allow_negative) else None


def _price_from(dist: CountDist, line: float) -> tuple[float, float, float]:
    """(p_over, p_under, p_push) for any line type."""
    kind = line_kind(line)
    if kind == HALF:
        p_over = model_probability(dist, line, over=True)
        return p_over, 1.0 - p_over, 0.0
    if kind == INTEGER:
        k = int(round(line))
        push = dist.pmf(k)
        win = dist.sf(k + 1)
        lose = dist.cdf(k - 1)
        return win, lose, push
    # Quarter lines have no single probability: the fair split-bet price is the
    # primitive, and 1/price is the closest thing to one.
    p_over = model_probability(dist, line, over=True)
    p_under = model_probability(dist, line, over=False)
    return p_over, p_under, 0.0


def _fair_odds(dist: CountDist, line: float) -> tuple[float, float]:
    """Fair decimal odds, which is NOT 1/probability once a push exists.

    A stake on an integer line is returned when the count lands exactly on it,
    so the bet is only at risk on the non-push outcomes:

        EV = o * P(win) + 1 * P(push) = 1   =>   o = (1 - P(push)) / P(win)

    Using 1/P(win) instead prices the push as a loss and quotes the line too
    long — by the whole push probability, which on a central integer line is
    the single most likely outcome.
    """
    kind = line_kind(line)
    if kind == QUARTER:
        from .lines import fair_quarter_odds
        return fair_quarter_odds(dist, line, True), fair_quarter_odds(dist, line, False)
    p_over, p_under, p_push = _price_from(dist, line)
    over = (1.0 - p_push) / p_over if p_over > 0 else float("inf")
    under = (1.0 - p_push) / p_under if p_under > 0 else float("inf")
    return over, under


def _band_dispersions(fit: FitResult) -> list[float]:
    if fit.dispersion_from_prior:
        prior = fit.config.d_prior(None)
        spread = 0.10
        base = fit.dispersion
    else:
        spread = 0.03
        base = fit.dispersion
    return [max(base + delta, MIN_DISPERSION) for delta in (0.0, spread, -spread)]


def is_priceable(line: float, config: ModelConfig) -> bool:
    """Is this line inside the range the model is allowed to quote at all?"""
    return config.min_priceable_line <= line <= config.max_priceable_line


def price_line(fit: FitResult, line: float, ladder: Ladder | None = None) -> FairPrice:
    """Fair price for any line, with the band from refitting under uncertainty.

    `ladder` is needed for the band: the sweep refits under each devig method,
    which requires the original quotes. Without it the price is still correct
    but the band collapses to the dispersion sweep alone, and is flagged.
    """
    # Refused outright, before anything is computed. Beyond these bounds the
    # distribution is being extrapolated into a region no ladder constrains and
    # no book quotes; the result would be arithmetic wearing the clothes of a
    # price. Raising rather than returning a flagged number is deliberate — a
    # flag can be ignored by a caller looping over lines, and this one must not
    # be.
    if not is_priceable(line, fit.config):
        raise LadderError(
            f"line {line} is outside the priceable range "
            f"{fit.config.min_priceable_line}-{fit.config.max_priceable_line}; "
            f"the model is not trusted out there and will not quote it"
        )

    dist = fit.dist
    p_over, p_under, p_push = _price_from(dist, line)
    over, under = _fair_odds(dist, line)

    lo_line, hi_line = fit.quoted_range
    grace = fit.config.extrapolation_grace
    if line < lo_line - grace:
        beyond = (lo_line - grace) - line
    elif line > hi_line + grace:
        beyond = line - (hi_line + grace)
    else:
        beyond = 0.0

    flags = list(fit.flags)
    overs, unders = [over], [under]

    for d in _band_dispersions(fit):
        methods = [fit.config.default_devig] if ladder is None else list(BAND_METHODS)
        for method in methods:
            try:
                if ladder is None:
                    candidate = CountDist(fit.lam, d, fit.config.grid_max,
                                          fit.config.zero_excess)
                else:
                    refit = fit_ladder(ladder, fit.config, method=method, d_override=d)
                    candidate = refit.dist
                o, u = _fair_odds(candidate, line)
            except Exception:
                continue
            overs.append(o)
            unders.append(u)

    if ladder is None:
        flags.append("band_without_devig_sweep")

    return FairPrice(
        line=line,
        kind=line_kind(line),
        # NOT rounded: these must still partition to exactly 1, and rounding
        # three values independently breaks that. Only the presented odds are
        # taken to two decimal places.
        p_over=p_over,
        p_under=p_under,
        p_push=p_push,
        fair_over=_round(over),
        fair_under=_round(under),
        band_over=(_round(min(overs)), _round(max(overs))),
        band_under=(_round(min(unders)), _round(max(unders))),
        beyond_ladder=round(beyond, 2),
        flags=flags,
    )
