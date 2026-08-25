"""Fitting (mean, dispersion) to a ladder by weighted least squares.

IN PROBABILITY SPACE, never odds space. A fixed error in odds is a wildly
varying error in probability depending on the price — fitting in odds space
would quietly weight long prices far more heavily than short ones.

WEIGHTS COME FROM THE BOOK'S OWN MARGIN. Base weight is 1/overround, normalised
to mean 1 within the ladder. The reasoning: a book's margin at a rung is its own
confidence readout — it charges more where it knows less. This needs no
historical data and adapts per fixture, unlike a fixed weighting scheme.
Synthesised sides are multiplied by 0.25: a reconstructed price is not an
observation.

THE SPARSE FALLBACK IS THE HONEST PART. Two rungs cannot identify both a mean
and a dispersion — the fit would happily return any D that threads them. Below
four usable rungs, or a span under three counts, a penalty residual pulls D
toward a league prior, and the result is FLAGGED so the caller knows part of the
price is a stale league average rather than this fixture's market. Above those
bars the penalty is dropped entirely, because the ladder then genuinely says
something about dispersion.
"""
from dataclasses import dataclass, field
from math import exp, log, sqrt
from statistics import median

from ..grid import _nelder_mead          # same pure-Python optimiser as SPEC 8
from . import devig as devig_mod
from .config import ModelConfig, CORNERS
from .dist import CountDist
from .errors import LadderError
from .ladder import Ladder
from .lines import Constraint, line_kind, model_probability, QUARTER

# Penalty weight pinning D to its prior on a sparse ladder.
#
# CALIBRATED, not chosen: it must be large enough that perturbing two adjacent
# half-lines by +/-0.005 absolute probability cannot move fitted D more than
# +/-0.05 from the prior. `calibrate_dispersion_weight` below derives it, and a
# test asserts this constant still satisfies the criterion — so if the fit or
# the weighting changes, the number is re-derived rather than inherited.
DISPERSION_PENALTY_WEIGHT = 12.0


@dataclass
class FitResult:
    lam: float
    dispersion: float
    constraints: list[Constraint] = field(default_factory=list)
    residuals: list[float] = field(default_factory=list)
    z_scores: list[float] = field(default_factory=list)
    weighted_rms: float = 0.0
    condition_number: float = float("inf")
    flags: list[str] = field(default_factory=list)
    config: ModelConfig = CORNERS
    quoted_range: tuple[float, float] = (0.0, 0.0)

    @property
    def dist(self) -> CountDist:
        return CountDist(self.lam, self.dispersion, self.config.grid_max)

    @property
    def exactly_identified(self) -> bool:
        return "exactly_identified" in self.flags

    @property
    def dispersion_from_prior(self) -> bool:
        return "dispersion_from_prior" in self.flags

    def inconsistent_rungs(self) -> list[tuple[Constraint, float]]:
        """Rungs the book has priced out of line with its OWN ladder.

        This is the within-book stale-line signal, and it is the finding most
        likely to be a real edge — so it is reported whether or not the overall
        fit looks good. A ladder can fit beautifully overall and still have one
        rung nobody has updated.
        """
        bar = self.config.residual_z_threshold
        return [
            (c, z) for c, z in zip(self.constraints, self.z_scores) if abs(z) >= bar
        ]


def build_constraints(
    ladder: Ladder, method: str, config: ModelConfig
) -> tuple[list[Constraint], list[str]]:
    """Devig every usable rung into a typed constraint. Returns (constraints, flags)."""
    flags: list[str] = []

    two_sided = [r for r in ladder.rungs if r.over and r.under]
    margins = [devig_mod.overround(r.over, r.under) for r in two_sided]
    median_margin = median(margins) if margins else 0.04

    constraints: list[Constraint] = []
    for rung in ladder.rungs:
        over_odds, under_odds, synthetic = rung.over, rung.under, False
        if over_odds and not under_odds:
            under_odds = devig_mod.synthesise_missing_side(over_odds, True, median_margin)
            synthetic = True
        elif under_odds and not over_odds:
            over_odds = devig_mod.synthesise_missing_side(under_odds, False, median_margin)
            synthetic = True
        elif not over_odds and not under_odds:
            continue

        try:
            p_over, _ = devig_mod.devig(over_odds, under_odds, method)
            margin = devig_mod.overround(over_odds, under_odds)
        except LadderError:
            continue

        if not (config.min_overround <= margin <= config.max_overround):
            flags.append(f"overround_out_of_range:{rung.line}:{margin:.4f}")

        constraints.append(Constraint(
            line=rung.line,
            kind=line_kind(rung.line),
            target=p_over,
            overround=max(margin, 1e-4),
            synthetic_side=synthetic,
            threshold=float(rung.line),
        ))

    if not constraints:
        raise LadderError(f"{ladder.fixture_id}: no usable rung on the ladder")

    # A book's survival curve must fall as the line rises. Where it does not,
    # one of those rungs is stale — fit anyway (the residual will point at it)
    # but say so, because a non-monotone ladder means the fit is reconciling a
    # contradiction rather than describing a coherent view.
    ordered = sorted(constraints, key=lambda c: c.line)
    if any(b.target > a.target + 1e-9 for a, b in zip(ordered, ordered[1:])):
        flags.append("non_monotone_survival")

    return constraints, flags


def _weights(constraints: list[Constraint]) -> list[float]:
    raw = [1.0 / c.overround for c in constraints]
    mean = sum(raw) / len(raw)
    weights = [w / mean for w in raw]
    return [
        w * (0.25 if c.synthetic_side else 1.0)
        for w, c in zip(weights, constraints)
    ]


def _identifies_dispersion(constraints: list[Constraint], config: ModelConfig) -> bool:
    usable = [c for c in constraints if not c.synthetic_side]
    if len(usable) < config.min_rungs_for_dispersion:
        return False
    span = max(c.threshold for c in usable) - min(c.threshold for c in usable)
    return span >= config.min_span_for_dispersion


def _residual_vector(
    lam: float, d: float, constraints: list[Constraint], weights: list[float],
    config: ModelConfig, d_prior: float | None,
) -> list[float]:
    dist = CountDist(lam, d, config.grid_max)
    out = [
        w * (model_probability(dist, c.line, over=True) - c.target)
        for c, w in zip(constraints, weights)
    ]
    if d_prior is not None:
        out.append(DISPERSION_PENALTY_WEIGHT * (d - d_prior))
    return out


def fit_ladder(
    ladder: Ladder,
    config: ModelConfig = CORNERS,
    method: str | None = None,
    d_override: float | None = None,
) -> FitResult:
    """Fit mean and dispersion to a ladder.

    `d_override` pins the dispersion and fits the mean alone — used by the
    uncertainty band in price.py to sweep D without re-deriving it each time.
    """
    method = method or config.default_devig
    constraints, flags = build_constraints(ladder, method, config)
    weights = _weights(constraints)

    identifies = _identifies_dispersion(constraints, config)
    d_prior = None if identifies else config.d_prior(ladder.league)
    if d_prior is not None:
        flags.append("dispersion_from_prior")

    # A sensible starting mean: the ladder's centre of quoted lines.
    centre = sum(c.threshold for c in constraints) / len(constraints)
    start_d = d_override if d_override is not None else (d_prior or 1.15)

    def objective(x: list[float]) -> float:
        lam = exp(x[0])
        d = d_override if d_override is not None else 1.0 + exp(x[1])
        if lam <= 0 or lam > config.grid_max:
            return 1e9
        try:
            res = _residual_vector(lam, d, constraints, weights, config, d_prior)
        except LadderError:
            return 1e9
        return sum(r * r for r in res)

    best, best_score = None, None
    # Multi-start: Nelder-Mead is local, and a ladder centred far from the
    # truth can otherwise settle in a poor basin.
    for lam0 in (centre, centre * 0.75, centre * 1.25):
        x0 = [log(max(lam0, 0.5)), log(max(start_d - 1.0, 1e-3))]
        params, score = _nelder_mead(objective, x0)
        if best_score is None or score < best_score:
            best, best_score = params, score

    lam = exp(best[0])
    d = d_override if d_override is not None else 1.0 + exp(best[1])

    residuals = _residual_vector(lam, d, constraints, weights, config, d_prior)
    rung_residuals = residuals[: len(constraints)]

    free_params = 1 if d_override is not None else 2
    # The ladder alone cannot contradict the fit: as many free parameters as
    # observations, so the residuals carry no information about whether the
    # model is right. Downstream must not read a tidy fit here as evidence.
    #
    # Counted on the CONSTRAINTS, deliberately ignoring the dispersion penalty.
    # That penalty is a prior, not an observation — it stops D wandering, but it
    # cannot tell you the shape was wrong. A sparse ladder is therefore both
    # exactly_identified and dispersion_from_prior, and both are worth saying:
    # one means the data cannot check the fit, the other means part of the
    # answer came from a stale league average.
    if len(constraints) == free_params:
        flags.append("exactly_identified")

    rms = sqrt(sum(r * r for r in rung_residuals) / len(rung_residuals)) if rung_residuals else 0.0
    z_scores = [r / rms if rms > 0 else 0.0 for r in rung_residuals]

    return FitResult(
        lam=lam,
        dispersion=d,
        constraints=constraints,
        residuals=rung_residuals,
        z_scores=z_scores,
        weighted_rms=rms,
        condition_number=_condition_number(lam, d, constraints, weights, config, d_prior),
        flags=flags,
        config=config,
        quoted_range=(
            min(c.threshold for c in constraints),
            max(c.threshold for c in constraints),
        ),
    )


def _condition_number(lam, d, constraints, weights, config, d_prior) -> float:
    """Condition number of the Jacobian at the optimum.

    High means the two parameters are trading off against each other — the
    ladder pins some combination of mean and dispersion but not each one
    separately, so the fitted values look precise while being nearly arbitrary
    along one direction. Computed by finite difference; J^T J is 2x2 so its
    eigenvalues are closed-form and no linear algebra library is needed.
    """
    h = 1e-5
    try:
        base = _residual_vector(lam, d, constraints, weights, config, d_prior)
        d_lam = [
            (a - b) / (2 * h * lam)
            for a, b in zip(
                _residual_vector(lam * exp(h), d, constraints, weights, config, d_prior),
                _residual_vector(lam * exp(-h), d, constraints, weights, config, d_prior),
            )
        ]
        d_disp = [
            (a - b) / (2 * h)
            for a, b in zip(
                _residual_vector(lam, d + h, constraints, weights, config, d_prior),
                _residual_vector(lam, d - h, constraints, weights, config, d_prior),
            )
        ]
    except LadderError:
        return float("inf")

    a = sum(x * x for x in d_lam)
    b = sum(x * y for x, y in zip(d_lam, d_disp))
    c = sum(y * y for y in d_disp)
    trace, det = a + c, a * c - b * b
    disc = max(trace * trace / 4.0 - det, 0.0)
    hi = trace / 2.0 + sqrt(disc)
    lo = trace / 2.0 - sqrt(disc)
    if lo <= 1e-18:
        return float("inf")
    return sqrt(hi / lo)


def calibrate_dispersion_weight(
    config: ModelConfig = CORNERS, prior: float = 1.19, tolerance: float = 0.05
) -> float:
    """Smallest penalty weight meeting the sparse-ladder stability criterion.

    The criterion: on a two-rung ladder, perturbing the two rungs by +/-0.005
    absolute probability antisymmetrically must not move fitted D more than
    `tolerance` from the prior. Written as a function rather than a magic
    number so it can be re-derived whenever the fit or the weighting changes.
    """
    from .ladder import Ladder as _L, Rung as _R

    def fitted_d(weight: float, bump: float) -> float:
        global DISPERSION_PENALTY_WEIGHT
        saved = DISPERSION_PENALTY_WEIGHT
        DISPERSION_PENALTY_WEIGHT = weight
        try:
            dist = CountDist(9.0, prior, config.grid_max)
            rungs = []
            for line, sign in ((8.5, 1.0), (9.5, -1.0)):
                p = model_probability(dist, line, over=True) + sign * bump
                p = min(max(p, 0.01), 0.99)
                rungs.append(_R(line, over=1.0 / (p * 1.02), under=1.0 / ((1 - p) * 1.02)))
            result = fit_ladder(_L("calib", "corners_ft", rungs, league=None), config)
            return result.dispersion
        finally:
            DISPERSION_PENALTY_WEIGHT = saved

    for weight in (2.0, 4.0, 8.0, 12.0, 20.0, 40.0, 80.0):
        drift = max(abs(fitted_d(weight, b) - prior) for b in (0.005, -0.005))
        if drift <= tolerance:
            return weight
    return 80.0
