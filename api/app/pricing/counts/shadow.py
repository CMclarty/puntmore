"""Measuring how wrong the model actually is, before any money depends on it.

The round-trip tests prove the arithmetic: fit data generated from the same
distribution and recover the parameters. They say NOTHING about whether football
corners behave like a negative binomial. Only two things can answer that, and one
of them needs settlement data nobody has yet.

The other is available immediately and costs nothing:

    LEAVE-ONE-RUNG-OUT — hide a line the book DID quote, refit on the rest,
    predict the hidden one, and compare. The book's own price is the ground
    truth. This is the honest measure of interpolation and extrapolation error,
    it runs on ladders already being fetched, and it risks no stake.

Bucketing the error by distance from the ladder's centre is the part that
matters: an average error hides the fact that inside the ladder is nearly free
and three counts out is guesswork. That gradient is the thing to look at before
trusting a tail price.
"""
from dataclasses import dataclass, field
from statistics import mean

from .config import CORNERS, ModelConfig
from .errors import LadderError
from .fit import fit_ladder
from .ladder import Ladder, Rung
from .lines import model_probability


@dataclass
class RungError:
    line: float
    predicted: float        # model's devigged p_over with this rung hidden
    actual: float           # the book's own devigged p_over
    distance: float         # counts from the remaining ladder's centre

    @property
    def error(self) -> float:
        return self.predicted - self.actual


@dataclass
class ShadowReport:
    fixture_id: str
    errors: list[RungError] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def mae(self) -> float:
        return mean(abs(e.error) for e in self.errors) if self.errors else 0.0

    def by_distance(self, edges=(1.0, 2.0, 3.0)) -> dict[str, float]:
        """MAE bucketed by how far the hidden rung sat from the ladder centre.

        The gradient across these buckets is the finding: if it is flat, the
        shape assumption is holding; if it climbs steeply, tail prices are not
        worth betting whatever their edge says.
        """
        buckets: dict[str, list[float]] = {}
        for err in self.errors:
            label = f">{edges[-1]:g}"
            for edge in edges:
                if err.distance <= edge:
                    label = f"<={edge:g}"
                    break
            buckets.setdefault(label, []).append(abs(err.error))
        return {k: mean(v) for k, v in sorted(buckets.items())}


def leave_one_out(
    ladder: Ladder, config: ModelConfig = CORNERS
) -> ShadowReport:
    """Refit without each rung in turn and predict it. The book is ground truth."""
    report = ShadowReport(fixture_id=ladder.fixture_id)
    usable = [r for r in ladder.rungs if r.over and r.under]
    if len(usable) < 4:
        # Below four rungs the reduced ladder cannot identify dispersion, so the
        # error measured would be the prior's, not the model's.
        report.flags.append("too_few_rungs_to_measure")
        return report

    for hidden in usable:
        rest = [r for r in usable if r.line != hidden.line]
        try:
            fit = fit_ladder(
                Ladder(ladder.fixture_id, ladder.market, rest, ladder.league),
                config,
            )
            predicted = model_probability(fit.dist, hidden.line, over=True)
        except LadderError:
            continue

        from .devig import devig
        actual, _ = devig(hidden.over, hidden.under, config.default_devig)
        centre = sum(r.line for r in rest) / len(rest)
        report.errors.append(RungError(
            line=hidden.line,
            predicted=predicted,
            actual=actual,
            distance=abs(hidden.line - centre),
        ))
    return report


def calibration_table(pairs: list[tuple]) -> dict:
    """Reliability by probability bucket, once settlements exist.

    `pairs` is [(FitResult, settled_count), ...]. Runs on an empty list without
    failing, because settlement data does not exist yet and this interface is
    here so the collection can start before the analysis does.
    """
    buckets: dict[str, list[tuple[float, int]]] = {}
    for fit, settled in pairs:
        for constraint in fit.constraints:
            predicted = model_probability(fit.dist, constraint.line, over=True)
            outcome = 1 if settled > constraint.line else 0
            label = f"{int(predicted * 10) * 10}-{int(predicted * 10) * 10 + 10}%"
            buckets.setdefault(label, []).append((predicted, outcome))

    return {
        label: {
            "n": len(rows),
            "predicted": mean(p for p, _ in rows),
            "observed": mean(o for _, o in rows),
        }
        for label, rows in sorted(buckets.items())
    }
