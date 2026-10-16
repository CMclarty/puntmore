"""Every empirically sourced constant, with its provenance and staleness.

Since Oct 2026 most of these are MEASURED against outcome data or derived by
simulation through this model (each says which, and how). They are gathered
here rather than scattered through the model so that replacing one is one edit
and so that nobody has to guess which numbers are measured and which are
inherited. Pinnacle-ladder measurements from the live shadow mode
(cards/shadow_store.py) should replace them as they accumulate.
"""
from dataclasses import dataclass, field

# Grid ceilings. Truncation is a real approximation: mass above the ceiling is
# renormalised away, so the ceiling must sit far enough above any plausible
# count that the discarded mass is negligible.
CORNERS_MAX = 80
CARDS_MAX = 40

# Dispersion (D = variance/mean) priors, per league. They matter ONLY on a
# sparse ladder — with four or more rungs spanning three or more counts the
# ladder identifies D on its own and the prior is dropped entirely (see fit.py).
# On a two-rung ladder a league average is doing real work in the price, which
# is why `FitResult.flags` reports it rather than letting it pass silently.
#
# PROVENANCE (Oct 2026, measured): D of the outcome around a sharp pre-match
# expectation (league-season level, rolling team ratings, the 1X2 and
# over/under 2.5 prices), football-data.co.uk matches 2016/17 onwards via the
# public xgabora/club-football-match-data dataset — 64,582 matches for corners,
# 69,325 for cards. Analysis scripts are not in the repo; the method and the
# numbers are in CLAUDE.md. Because a bookmaker's expectation is sharper than
# that model's, these are UPPER bounds on what a Pinnacle ladder should imply
# — slightly wide, which is the safe side for a prior.
#
# Corners replace the HKJC 2016-21 sample (EPL 1.14, La Liga 1.10, Serie A 1.25,
# default 1.19), which ran high everywhere it could be checked.
CORNERS_D_PRIORS: dict[str, float] = {
    "soccer_epl": 1.11,
    "soccer_efl_champ": 1.12,
    "soccer_england_league1": 1.17,
    "soccer_spain_la_liga": 1.10,
    "soccer_italy_serie_a": 1.19,
    "soccer_germany_bundesliga": 1.12,
    "soccer_france_ligue_one": 1.13,
    "soccer_portugal_primeira_liga": 1.17,
    # No outcome data for European competition here; HKJC's figure kept.
    "soccer_uefa_champs_league": 1.30,
}
CORNERS_D_PRIOR_DEFAULT = 1.14

# Cards, counted yellows + reds (a red as one card). Measured as above: 1.10
# across all leagues — the old placeholder happened to be right on average —
# but leagues differ far more than for corners, so per-league values matter.
CARDS_D_PRIORS: dict[str, float] = {
    "soccer_epl": 1.09,
    "soccer_efl_champ": 1.05,
    "soccer_england_league1": 1.04,
    "soccer_spain_la_liga": 1.22,
    "soccer_italy_serie_a": 1.04,
    "soccer_germany_bundesliga": 1.18,
    "soccer_france_ligue_one": 1.06,
    "soccer_portugal_primeira_liga": 1.32,
}
CARDS_D_PRIOR_DEFAULT = 1.10

# Match goals: slightly UNDER-dispersed given the market (COM nu ~ 1.03, i.e.
# D ~ 0.97, fitted to 156,532 Bet365 over/under 2.5 markets 2005-2026, chosen
# on 2005-15 and confirmed on 2016+), with 9% of P(0) moved from exactly one
# goal to 0-0 (GOALS_ZERO_EXCESS). Together they reproduce every total from 0
# to 6 goals to within half a percentage point; the old NB at D = 1.05 put
# O5.5 ~17% too likely. Pinnacle's goals ladder is usually deep enough to
# identify D itself, so the prior is the fallback and the shape is what counts.
GOALS_MAX = 20
GOALS_D_PRIOR_DEFAULT = 0.97
GOALS_ZERO_EXCESS = 0.09


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

    # Devig method used for the point estimate. POWER, measured (devig.py):
    # the best-calibrated on 156,667 two-way totals markets and on Pinnacle-
    # like 2% margins; multiplicative overstated every long side. The band
    # still sweeps devig.BAND_METHODS.
    default_devig: str = "power"

    # Fraction of P(0) moved from 1 to 0 after the shape is built (dist.py).
    # Only total goals need it (GOALS below).
    zero_excess: float = 0.0

    # Overround outside this range is flagged — not rejected. A book quoting a
    # 0.2% margin or a 20% one is telling you something about that rung.
    min_overround: float = 0.005
    max_overround: float = 0.15

    # |z| above this marks a rung as inconsistent with the rest of its own
    # ladder. This is the within-book stale-line signal and is the thing most
    # likely to be a real edge, so it must survive a good overall fit.
    residual_z_threshold: float = 2.5

    # HARD BOUNDS ON WHAT WILL BE PRICED AT ALL (Callum's call, Aug 2026).
    #
    # A separate mechanism from the extrapolation penalty below, and a blunter
    # one on purpose. The penalty makes a distant price harder to bet; these
    # bounds mean some prices are never produced. Outside them the count
    # distribution is being asked about a region no ladder constrains and no
    # sensible market quotes — the number would be arithmetic rather than a
    # price, and a confident-looking figure on a 20.5 corner line is exactly
    # the kind of thing that gets bet by accident.
    #
    # Corners 4.5-13.5 covers the range real ladders and real books quote.
    # Cards 0.5-6.5 covers a 0-5 card match with headroom.
    #
    # These bound PRICING, not FITTING: a rung outside the range is still a
    # perfectly good constraint and makes the fit better. It is being ASKED for
    # a price out there that is refused.
    min_priceable_line: float = 4.5
    max_priceable_line: float = 13.5

    # How far outside the quoted range a line may sit before its price is
    # treated as extrapolation. Measured in counts, not rungs, so it means the
    # same thing on a sparse ladder as on a dense one.
    extrapolation_grace: float = 1.0

    # The extra bar for a line beyond the quoted range, in PROBABILITY per
    # count beyond the grace, converted to edge on the side being bet:
    #
    #     edge -= extrapolation_prob_penalty * beyond / p(side wins)
    #
    # A probability error of e moves a price by about e/p relatively, so the
    # same tail-shape error is ~1% on a 1.20 favourite and ~6% on its 6.0
    # opposite. The flat 2%-of-edge per count it replaced (Aug 2026, never
    # measured) charged both sides alike: too much on short favourites, too
    # little on long shots. 0.002 is MEASURED by simulation through this fit
    # (api/tools/extrapolation_sim.py, Oct 2026): it covers the true price on
    # 91% of extrapolated favourites and all long shots, where the flat rule
    # charged favourites ~2.9% of edge for ~0.3% of need.
    extrapolation_prob_penalty: float = 0.002

    def d_prior(self, league: str | None) -> float:
        return self.d_priors.get(league or "", self.d_prior_default)


CORNERS = ModelConfig()
CARDS = ModelConfig(
    grid_max=CARDS_MAX,
    d_priors=dict(CARDS_D_PRIORS),
    d_prior_default=CARDS_D_PRIOR_DEFAULT,
    # A 0-5 card match with headroom. Anything above 6.5 is a red-card tail the
    # ladder cannot speak to.
    min_priceable_line=0.5,
    max_priceable_line=6.5,
)
GOALS = ModelConfig(
    grid_max=GOALS_MAX,
    d_priors={},
    d_prior_default=GOALS_D_PRIOR_DEFAULT,
    zero_excess=GOALS_ZERO_EXCESS,
    # 0.5-5.5 is the range books post a match-goals ladder over; past six
    # goals is a tail no ladder speaks to.
    min_priceable_line=0.5,
    max_priceable_line=5.5,
)
