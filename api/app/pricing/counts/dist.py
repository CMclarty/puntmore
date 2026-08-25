"""The count distribution: how likely each possible number of corners/cards is.

Parameterised by the MEAN (lambda) and the DISPERSION D = variance/mean, rather
than the negative binomial's usual (n, p). Two reasons: D is directly
interpretable (D = 1 is Poisson, D > 1 means more spread than Poisson), and it
is the quantity a ladder does or does not identify, so priors and diagnostics
are naturally expressed in it.

    Negative binomial with mean L and variance L*D:
        kappa = L / (D - 1)
        q     = kappa / (kappa + L)
        P(0)     = q ^ kappa
        P(k+1)   = P(k) * (kappa + k) / (k + 1) * (1 - q)

    Poisson is the D -> 1 limit and is used below 1 + 1e-6, where the negative
    binomial's kappa diverges and the recursion loses precision.

The recursion is used rather than a closed-form pmf with gamma functions because
it is exact in floating point for the ranges here, allocates one list, and needs
no special functions.
"""
from math import exp, log

from .errors import LadderError

_POISSON_LIMIT = 1.0 + 1e-6


class CountDist:
    """A count distribution on 0..grid_max, with its tail mass renormalised away.

    Truncation matters: the grid ceiling must sit far above any plausible count,
    or the renormalisation quietly inflates every probability. The defaults (80
    corners, 40 cards) are far enough out that discarded mass is negligible.
    """

    __slots__ = ("lam", "dispersion", "grid_max", "_pmf", "_cdf")

    def __init__(self, lam: float, dispersion: float, grid_max: int):
        if lam <= 0:
            raise LadderError(f"mean must be positive, got {lam}")
        if dispersion < 1.0:
            # Under-dispersion is not modelled: no count distribution in this
            # family produces it, and silently clamping would hide a bad fit.
            raise LadderError(f"dispersion must be >= 1, got {dispersion}")
        self.lam = lam
        self.dispersion = dispersion
        self.grid_max = grid_max
        self._pmf = self._build()
        self._cdf = self._cumulative()

    def _build(self) -> list[float]:
        lam, d, n = self.lam, self.dispersion, self.grid_max
        if d < _POISSON_LIMIT:
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
        return [p / total for p in out]

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
