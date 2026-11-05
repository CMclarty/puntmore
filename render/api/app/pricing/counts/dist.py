"""The count distribution: how likely each possible number of corners/cards/goals is.

Parameterised by the MEAN (lambda) and the DISPERSION D = variance/mean, rather
than any family's native parameters. D is directly interpretable (D = 1 is
Poisson, D > 1 more spread than Poisson, D < 1 less) and it is the quantity a
ladder does or does not identify, so priors and diagnostics are expressed in it.

TWO FAMILIES, JOINED AT POISSON (Oct 2026, measured — see config.py):

    D >= 1   negative binomial with mean L and variance L*D:
                 kappa = L / (D - 1),  q = kappa / (kappa + L)
                 P(0) = q ^ kappa,  P(k+1) = P(k) * (kappa + k) / (k + 1) * (1 - q)
             Fitted against ~53,000 held-out matches of corners it matched the
             observed tails 2-5 corners from the mean to within ~1% relative,
             as well as the compound-Poisson (clustered corners) and
             Poisson-lognormal alternatives — it stays.

    D < 1    Conway-Maxwell-Poisson, P(k) proportional to lambda_c^k / (k!)^nu
             with nu = 1/D (variance ~ mean/nu), solved for the requested MEAN.
             Total goals given the market are slightly UNDER-dispersed (nu ~
             1.03 fitted to ~157,000 matches; the old NB-only model could not
             go below Poisson at all, so a ladder saying "narrower than Poisson"
             was forced to D = 1 and its tails came out too fat). Yellow cards
             per team are under-dispersed too (Philipson, JRSS A 2026).

    Both reduce to Poisson at D = 1, so the family is continuous across it and
    the fit can move through Poisson without a jump.

`zero_excess` moves that fraction of P(0) from 1 to 0 after the shape is built.
Total goals carry ~9% more 0-0s than any smooth family predicts given the
market's own over/under price, taken from exactly-one-goal matches with 2+
untouched (measured, config.GOALS). Zero for corners and cards.

The recursion is used for the NB rather than a closed-form pmf with gamma
functions because it is exact in floating point for the ranges here, allocates
one list, and needs no special functions.
"""
from math import exp, lgamma, log

from .errors import LadderError

_POISSON_LIMIT = 1.0 + 1e-6
_COM_LIMIT = 1.0 - 1e-6

# Below this a count market is not plausible: nu = 1/D ~ 1.67 is already far
# narrower than any football count measured. Also the fit's lower bound.
MIN_DISPERSION = 0.6
MAX_DISPERSION = 4.0

_LGAMMA: dict[int, list[float]] = {}


def _log_factorials(n: int) -> list[float]:
    table = _LGAMMA.get(n)
    if table is None:
        table = [lgamma(k + 1.0) for k in range(n + 1)]
        _LGAMMA[n] = table
    return table


def _com_pmf(mean: float, nu: float, n: int) -> list[float]:
    """COM-Poisson on 0..n with the given MEAN, by Newton on log(lambda_c).

    d E[N] / d log(lambda_c) = Var[N], so each step is (E - mean) / Var. Started
    from the standard approximation E ~ lambda_c^(1/nu) - (nu - 1)/(2 nu).
    """
    lf = _log_factorials(n)
    t = nu * log(max(mean + (nu - 1.0) / (2.0 * nu), 1e-3))
    w: list[float] = []
    for _ in range(60):
        logw = [k * t - nu * lf[k] for k in range(n + 1)]
        top = max(logw)
        w = [exp(x - top) for x in logw]
        s = sum(w)
        e1 = sum(k * wk for k, wk in enumerate(w)) / s
        e2 = sum(k * k * wk for k, wk in enumerate(w)) / s
        var = max(e2 - e1 * e1, 1e-12)
        step = (e1 - mean) / var
        t -= max(min(step, 5.0), -5.0)
        if abs(step) < 1e-11:
            break
    s = sum(w)
    return [x / s for x in w]


class CountDist:
    """A count distribution on 0..grid_max, with its tail mass renormalised away.

    Truncation matters: the grid ceiling must sit far above any plausible count,
    or the renormalisation quietly inflates every probability. The defaults (80
    corners, 40 cards) are far enough out that discarded mass is negligible.
    """

    __slots__ = ("lam", "dispersion", "grid_max", "zero_excess", "_pmf", "_cdf")

    def __init__(self, lam: float, dispersion: float, grid_max: int,
                 zero_excess: float = 0.0):
        if lam <= 0:
            raise LadderError(f"mean must be positive, got {lam}")
        if not MIN_DISPERSION <= dispersion <= MAX_DISPERSION:
            # Outside this no football count has been measured; a fit landing
            # there is reconciling a broken ladder, and clamping would hide it.
            raise LadderError(
                f"dispersion must be within {MIN_DISPERSION}-{MAX_DISPERSION}, "
                f"got {dispersion}"
            )
        self.lam = lam
        self.dispersion = dispersion
        self.grid_max = grid_max
        self.zero_excess = zero_excess
        self._pmf = self._build()
        self._cdf = self._cumulative()

    def _build(self) -> list[float]:
        lam, d, n = self.lam, self.dispersion, self.grid_max
        if d < _COM_LIMIT:
            out = _com_pmf(lam, 1.0 / d, n)
        elif d < _POISSON_LIMIT:
            out = [exp(-lam)]
            cur = out[0]
            for k in range(1, n + 1):
                cur = cur * lam / k
                out.append(cur)
        else:
            kappa = lam / (d - 1.0)
            q = kappa / (kappa + lam)
            cur = exp(kappa * log(q))
            out = [cur]
            for k in range(n):
                cur = cur * (kappa + k) / (k + 1) * (1.0 - q)
                out.append(cur)
        total = sum(out)
        if total <= 0:
            raise LadderError("degenerate distribution")
        out = [p / total for p in out]
        if self.zero_excess and n >= 1:
            shift = min(self.zero_excess * out[0], out[1])
            out[0] += shift
            out[1] -= shift
        return out

    def _cumulative(self) -> list[float]:
        out, run = [], 0.0
        for p in self._pmf:
            run += p
            out.append(run)
        return out

    def pmf(self, k: int) -> float:
        """P(N == k)."""
        if k < 0 or k > self.grid_max:
            return 0.0
        return self._pmf[k]

    def cdf(self, k: int) -> float:
        """P(N <= k)."""
        if k < 0:
            return 0.0
        if k >= self.grid_max:
            return 1.0
        return self._cdf[k]

    def sf(self, k: int) -> float:
        """P(N >= k). The survival function, which is what a half line asks for."""
        if k <= 0:
            return 1.0
        if k > self.grid_max:
            return 0.0
        return 1.0 - self._cdf[k - 1]

    def mode(self) -> int:
        return max(range(self.grid_max + 1), key=lambda k: self._pmf[k])
