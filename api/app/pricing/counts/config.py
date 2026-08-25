"""Every empirically sourced constant, with its provenance and staleness.

All of these are placeholders meant to be replaced by Puntmore's own estimates
once settlements accumulate. They are gathered here rather than scattered
through the model so that replacing them is one edit and so that nobody has to
guess which numbers are measured and which are inherited.
"""
from dataclasses import dataclass, field

# Grid ceilings. Truncation is a real approximation: mass above the ceiling is
# renormalised away, so the ceiling must sit far enough above any plausible
# count that the discarded mass is negligible.
CORNERS_MAX = 80
CARDS_MAX = 40

# Dispersion (D = variance/mean) priors for corners, per league.
#
# PROVENANCE: HKJC 2016-21 sample, quoted in the build spec. STALE, and not
# re-derived here. They matter ONLY on a sparse ladder — with four or more rungs
# spanning three or more counts the ladder identifies D on its own and the prior
# is dropped entirely (see fit.py). On a two-rung ladder a five-year-old league
# average is doing real work in the price, which is why `FitResult.flags`
# reports it rather than letting it pass silently.
CORNERS_D_PRIORS: dict[str, float] = {
    "soccer_epl": 1.14,
    "soccer_spain_la_liga": 1.10,
    "soccer_italy_serie_a": 1.25,
    "soccer_uefa_champs_league": 1.30,
}
CORNERS_D_PRIOR_DEFAULT = 1.19

# Cards: NO published priors exist. 1.10 is a placeholder, not a measurement.
# The lookup is injectable so a caller can supply the median D fitted from full
# ladders in the same league once enough have been seen.
CARDS_D_PRIOR_DEFAULT = 1.10


@dataclass(frozen=True)
class ModelConfig:
    """Tunables for one market family."""

    grid_max: int = CORNERS_MAX
    d_priors: dict[str, float] = field(default_factory=lambda: dict(CORNERS_D_PRIORS))
    d_prior_default: float = CORNERS_D_PRIOR_DEFAULT

    # A ladder identifies its own dispersion once it has this many usable rungs
    # spanning this many counts. Below either bar the prior penalty is applied.
    min_rungs_for_dispersion: int = 4
    min_span_for_dispersion: float = 3.0

    # Devig method used for the point estimate. Multiplicative is the default
    # for two-way totals on the one published corner-market study; the other
    # three exist to widen the uncertainty band, not to be chosen between.
    default_devig: str = "multiplicative"

    # Overround outside this range is flagged — not rejected. A book quoting a
    # 0.2% margin or a 20% one is telling you something about that rung.
    min_overround: float = 0.005
    max_overround: float = 0.15

    # |z| above this marks a rung as inconsistent with the rest of its own
    # ladder. This is the within-book stale-line signal and is the thing most
    # likely to be a real edge, so it must survive a good overall fit.
    residual_z_threshold: float = 2.5

    # How far outside the quoted range a line may sit before its price is
    # treated as extrapolation. Measured in counts, not rungs, so it means the
    # same thing on a sparse ladder as on a dense one.
    extrapolation_grace: float = 1.0

    # Extra edge demanded per count beyond the quoted range. The tails are where
    # the model is weakest AND where the biggest apparent edges appear, so the
    # bar rises with distance rather than staying flat.
    extrapolation_edge_penalty: float = 0.02

    def d_prior(self, league: str | None) -> float:
        return self.d_priors.get(league or "", self.d_prior_default)


CORNERS = ModelConfig()
CARDS = ModelConfig(
    grid_max=CARDS_MAX,
    d_priors={},
    d_prior_default=CARDS_D_PRIOR_DEFAULT,
)
