"""Acceptance tests for the laddered corners/cards model (app/pricing/counts).

The package is INACTIVE — nothing in the running app imports it. These tests are
what stands in for production use until it is switched on.

READ THE ROUND-TRIP TESTS CORRECTLY. Tests 1-4 generate a ladder from a known
negative binomial and recover its parameters. That proves the ARITHMETIC is
right. It says nothing whatsoever about whether football corners are negative
binomial — the model is being tested against its own assumption. The only
pre-settlement measure of real accuracy is leave-one-out on real ladders
(shadow.py), which is why that exists.
"""
import pytest

from app.pricing.counts.cards import (
    COUNT_SIMPLE, POINTS_10_25, detect_unit, fit_cards,
)
from app.pricing.counts.config import CORNERS
from app.pricing.counts.devig import METHODS, overround, synthesise_missing_side
from app.pricing.counts.dist import CountDist
from app.pricing.counts.errors import LadderError
from app.pricing.counts.fit import (
    DISPERSION_PENALTY_WEIGHT, calibrate_dispersion_weight, fit_ladder,
)
from app.pricing.counts.ladder import Ladder, Rung
from app.pricing.counts.lines import fair_quarter_odds, model_probability
from app.pricing.counts.price import price_line
from app.pricing.counts.shadow import calibration_table, leave_one_out

TRUE_LAM, TRUE_D = 10.2, 1.15


def _truth() -> CountDist:
    return CountDist(TRUE_LAM, TRUE_D, CORNERS.grid_max)


def _ladder(lines, margin=1.07, league="soccer_epl", fixture="f1") -> Ladder:
    """A ladder a book with `margin` overround would print for the true dist."""
    truth = _truth()
    rungs = []
    for line in lines:
        p = model_probability(truth, line, over=True)
        rungs.append(Rung(
            line, over=1.0 / (p * margin), under=1.0 / ((1 - p) * margin)
        ))
    return Ladder(fixture, "corners_ft", rungs, league=league)


FULL_LINES = [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5]


# --- 1. Round trip on a full ladder ----------------------------------------

def test_round_trip_recovers_the_generating_parameters():
    """Acceptance test 1: lam within +/-0.1, D within +/-0.03.

    A correctness check on the arithmetic, NOT an accuracy claim.
    """
    fit = fit_ladder(_ladder(FULL_LINES))
    assert abs(fit.lam - TRUE_LAM) <= 0.1
    assert abs(fit.dispersion - TRUE_D) <= 0.03
    # A nine-rung ladder identifies dispersion on its own; the prior must be
    # dropped entirely rather than quietly shrinking the answer toward it.
    assert not fit.dispersion_from_prior
    assert not fit.exactly_identified


def test_a_full_ladder_is_well_conditioned():
    """A low condition number means mean and dispersion are separately pinned.

    High would mean the ladder fixes some combination of the two while each is
    nearly free — a fit that looks precise and is not.
    """
    fit = fit_ladder(_ladder(FULL_LINES))
    assert fit.condition_number < 100


# --- 2. Sparse ladders lean on the prior, and say so -----------------------

def test_sparse_ladder_holds_dispersion_near_the_prior():
    """Acceptance test 2: two rungs cannot identify D, so it must not wander.

    Perturbed antisymmetrically by +/-0.005 absolute probability, fitted D must
    stay within +/-0.05 of the prior.
    """
    prior = CORNERS.d_prior("soccer_epl")
    truth = _truth()
    for bump in (0.005, -0.005):
        rungs = []
        for line, sign in ((8.5, 1.0), (9.5, -1.0)):
            p = model_probability(truth, line, over=True) + sign * bump
            rungs.append(Rung(line, over=1.0 / (p * 1.05),
                              under=1.0 / ((1 - p) * 1.05)))
        fit = fit_ladder(Ladder("sparse", "corners_ft", rungs, league="soccer_epl"))
        assert abs(fit.dispersion - prior) <= 0.05
        assert fit.dispersion_from_prior


def test_the_dispersion_penalty_weight_still_meets_its_criterion():
    """The constant is calibrated, not chosen — re-derive it if the fit changes."""
    required = calibrate_dispersion_weight(CORNERS, prior=1.19, tolerance=0.05)
    assert DISPERSION_PENALTY_WEIGHT >= required


def test_sparse_tail_band_is_wider_than_the_spec_assumed():
    """A DELIBERATE deviation from the build spec, recorded rather than fudged.

    The spec expects an under-5.5 band within 12% of fair off a {8.5, 9.5}
    ladder. It is ~23%: that line sits three counts below the lowest rung, and
    on a sparse ladder the dispersion sweep is +/-0.10 because D is a stale
    league average rather than this fixture's market. A tail that far out
    genuinely moves that much.

    Narrowing the sweep to hit 12% would make the model look more confident
    than it is, on exactly the prices where confidence is least warranted. The
    band is left honest and the number is pinned here so the disagreement is
    visible rather than rediscovered.
    """
    ladder = _ladder([8.5, 9.5])
    fit = fit_ladder(ladder)
    width = price_line(fit, 5.5, ladder).band_width("under")
    assert width > 0.12
    assert width < 0.40


def test_band_widens_with_distance_from_the_ladder():
    """The gradient that matters: inside is nearly free, outside is guesswork."""
    ladder = _ladder([8.5, 9.5])
    fit = fit_ladder(ladder)
    widths = [
        price_line(fit, line, ladder).band_width("under")
        for line in (8.5, 7.5, 6.5, 5.5)
    ]
    assert widths == sorted(widths), "band must not narrow as the line moves out"


# --- 3. Integer lines push -------------------------------------------------

def test_integer_line_probabilities_partition_with_the_push():
    """Acceptance test 3: over + under + push == 1 at every integer line."""
    fit = fit_ladder(_ladder(FULL_LINES))
    for line in range(5, 14):          # inside the priceable range
        priced = price_line(fit, float(line))
        total = priced.p_over + priced.p_under + priced.p_push
        assert total == pytest.approx(1.0, abs=1e-9)
        assert priced.p_push > 0, "an integer line must be able to push"


def test_integer_line_constraint_round_trips():
    """The devigged ratio must reproduce the input it was built from.

    An integer line's devig constrains P(N>L)/(P(N>L)+P(N<L)) — a ratio, not a
    level. Treating it as a level overstates the over side by the whole push.
    """
    truth = _truth()
    target = model_probability(truth, 10.0, over=True)
    rungs = [Rung(10.0, over=1.0 / (target * 1.05),
                  under=1.0 / ((1 - target) * 1.05))]
    for line in (8.5, 12.5):
        p = model_probability(truth, line, over=True)
        rungs.append(Rung(line, over=1.0 / (p * 1.05), under=1.0 / ((1 - p) * 1.05)))

    fit = fit_ladder(Ladder("int", "corners_ft", rungs, league="soccer_epl"))
    assert model_probability(fit.dist, 10.0, over=True) == pytest.approx(target, abs=1e-3)


# --- 4. Quarter lines are a half-stake pair --------------------------------

def test_quarter_line_is_the_half_stake_combination():
    """Acceptance test 4: to 1e-9.

    A 9.25 over is half a stake on 9.0 (which pushes on exactly 9) and half on
    9.5. Its fair price is the odds making that split bet break even.
    """
    dist = _truth()
    odds = fair_quarter_odds(dist, 9.25, over=True)
    # Leg A: over 9.0 — wins N>=10, pushes N==9. Leg B: over 9.5 — wins N>=10.
    expected_return = 0.5 * (
        (odds * dist.sf(10) + dist.pmf(9)) + (odds * dist.sf(10))
    )
    assert expected_return == pytest.approx(1.0, abs=1e-9)


def test_quarter_line_sits_between_its_neighbours():
    dist = _truth()
    over_9 = 1.0 / model_probability(dist, 9.0, over=True)
    over_95 = 1.0 / model_probability(dist, 9.5, over=True)
    quarter = fair_quarter_odds(dist, 9.25, over=True)
    assert min(over_9, over_95) <= quarter <= max(over_9, over_95)


# --- 5. Cards: refuse what cannot be honestly priced -----------------------

def test_points_ladders_are_refused_not_approximated():
    """A count distribution cannot describe a lumpy 10/25 support.

    Fitting one anyway produces prices that look reasonable and are wrong in a
    structured way — worst at the tail lines where the edges appear.
    """
    points = Ladder("c2", "cards_ft", [Rung(35.5, 2.0, 1.8), Rung(45.5, 3.2, 1.35)])
    assert detect_unit(points) == POINTS_10_25
    with pytest.raises(LadderError, match="booking-points"):
        fit_cards(points)


def test_card_count_markets_price_but_flag_the_unit_assumption():
    """Whether a red counts as one card or two is a red-rate-sized bias."""
    counts = Ladder("c1", "cards_ft", [
        Rung(2.5, 1.7, 2.1), Rung(3.5, 2.4, 1.55),
        Rung(4.5, 3.6, 1.28), Rung(5.5, 5.5, 1.14),
    ])
    assert detect_unit(counts) == COUNT_SIMPLE
    fit = fit_cards(counts)
    assert fit.lam > 0
    assert any(f.startswith("card_unit_assumed") for f in fit.flags)


# --- Devig -----------------------------------------------------------------

@pytest.mark.parametrize("method", sorted(METHODS))
def test_every_devig_method_returns_a_probability_pair(method):
    p_over, p_under = METHODS[method](1.91, 1.95)
    assert p_over + p_under == pytest.approx(1.0, abs=1e-12)
    assert 0 < p_over < 1 and 0 < p_under < 1


def test_devig_methods_disagree_which_is_why_there_is_a_band():
    """If they all agreed the band would be pointless. They do not."""
    spread = [METHODS[m](1.75, 2.20)[0] for m in sorted(METHODS)]
    assert max(spread) - min(spread) > 1e-4


def test_a_one_sided_rung_is_reconstructed_and_down_weighted():
    """A synthesised price is not an observation and must not weigh like one."""
    made = synthesise_missing_side(1.91, True, 0.04)
    assert made > 1.0
    assert overround(1.91, made) == pytest.approx(0.04, abs=1e-9)

    ladder = _ladder(FULL_LINES)
    one_sided = list(ladder.rungs)
    one_sided[0] = Rung(one_sided[0].line, over=one_sided[0].over, under=None)
    fit = fit_ladder(Ladder("s", "corners_ft", one_sided, league="soccer_epl"))
    assert fit.constraints[0].synthetic_side
    # Still recovers the truth: one reconstructed rung must not drag the fit.
    assert abs(fit.lam - TRUE_LAM) <= 0.2


# --- Flags that stop a good-looking fit being mistaken for evidence --------

def test_an_exactly_identified_fit_is_flagged():
    """Two constraints and two free parameters fit perfectly by arithmetic.

    Without the flag, zero residuals read as confirmation the model is right.
    """
    fit = fit_ladder(_ladder([8.5, 9.5]))
    assert fit.exactly_identified
    # And it says so ALONGSIDE dispersion_from_prior, because the two mean
    # different things: the ladder cannot check the fit, AND part of the answer
    # is a stale league average. Residuals are near zero but not exactly so —
    # the prior penalty pulls the fit fractionally off the exact solution,
    # which is the penalty doing its job.
    assert fit.dispersion_from_prior
    assert max(abs(r) for r in fit.residuals) < 1e-3


def test_a_non_monotone_ladder_is_priced_but_flagged():
    """Survival must fall as the line rises. Where it does not, a rung is stale.

    Fit anyway — the residual points at the culprit, which is the edge — but
    say the fit is reconciling a contradiction.
    """
    ladder = _ladder(FULL_LINES)
    rungs = list(ladder.rungs)
    rungs[4] = Rung(rungs[4].line, over=1.10, under=8.0)   # absurdly short over
    fit = fit_ladder(Ladder("bad", "corners_ft", rungs, league="soccer_epl"))
    assert "non_monotone_survival" in fit.flags


def test_an_out_of_line_rung_is_findable_even_when_the_fit_is_good():
    """The within-book stale-line signal, which is the actual edge."""
    ladder = _ladder(FULL_LINES)
    rungs = list(ladder.rungs)
    stale = model_probability(_truth(), 10.5, over=True) + 0.08
    rungs[5] = Rung(10.5, over=1.0 / (stale * 1.07), under=1.0 / ((1 - stale) * 1.07))
    fit = fit_ladder(Ladder("stale", "corners_ft", rungs, league="soccer_epl"))
    flagged = [c.line for c, _ in fit.inconsistent_rungs()]
    assert 10.5 in flagged


def test_a_ladder_with_no_usable_rung_raises():
    with pytest.raises(LadderError):
        fit_ladder(Ladder("empty", "corners_ft", [Rung(9.5, None, None)]))


# --- The extrapolation guard ----------------------------------------------

def test_a_line_inside_the_ladder_is_not_flagged_as_extrapolated():
    ladder = _ladder(FULL_LINES)
    fit = fit_ladder(ladder)
    assert not price_line(fit, 10.5, ladder).extrapolated


def test_a_line_beyond_the_ladder_records_its_distance():
    """A short ladder, priced above it but still inside the hard bounds."""
    ladder = _ladder([7.5, 8.5, 9.5, 10.5])
    fit = fit_ladder(ladder)
    priced = price_line(fit, 13.5, ladder)
    assert priced.extrapolated
    assert priced.beyond_ladder == pytest.approx(2.0, abs=0.01)


def test_edge_requires_clearing_the_whole_band_not_the_midpoint():
    """The default that stops noise being reported as signal."""
    ladder = _ladder(FULL_LINES)
    fit = fit_ladder(ladder)
    priced = price_line(fit, 10.5, ladder)
    just_over_fair = priced.fair_over + 0.005
    assert priced.edge_vs(just_over_fair, "over", require_band=False) is not None
    assert priced.edge_vs(just_over_fair, "over", require_band=True) is None


def test_extrapolated_prices_demand_a_bigger_edge():
    """The tails are where the model is weakest AND where the fat edges appear.

    A quote beating fair by a little, two counts outside the ladder, must not
    register — most of that apparent edge is tail-shape error.
    """
    ladder = _ladder([7.5, 8.5, 9.5, 10.5])
    fit = fit_ladder(ladder)
    priced = price_line(fit, 13.5, ladder)
    modest = priced.fair_over * 1.02
    assert priced.edge_vs(modest, "over", True, CORNERS) is None
    generous = priced.band_over[1] * 1.20
    assert priced.edge_vs(generous, "over", True, CORNERS) is not None


# --- Shadow mode -----------------------------------------------------------

def test_leave_one_out_runs_and_buckets_by_distance():
    report = leave_one_out(_ladder(FULL_LINES))
    assert len(report.errors) == len(FULL_LINES)
    assert report.by_distance()
    # Near zero here BY CONSTRUCTION: the ladder was generated from the very
    # distribution being fitted. Only real ladders make this number mean
    # anything, which is the whole reason shadow mode exists.
    assert report.mae() < 1e-4


def test_leave_one_out_refuses_a_ladder_too_short_to_measure():
    report = leave_one_out(_ladder([8.5, 9.5]))
    assert "too_few_rungs_to_measure" in report.flags
    assert report.errors == []


def test_calibration_harness_runs_before_any_settlements_exist():
    assert calibration_table([]) == {}


# --- Numerical budget ------------------------------------------------------

def test_a_fit_is_fast_enough_to_run_per_fixture():
    """Spec budget is 50 ms per ladder; pure Python lands well inside it."""
    import time
    ladder = _ladder(FULL_LINES)
    start = time.perf_counter()
    for _ in range(5):
        fit_ladder(ladder)
    assert (time.perf_counter() - start) / 5 < 0.05


# --- Hard priceable bounds (Callum's call) --------------------------------

def test_corners_are_only_priced_between_4_5_and_13_5():
    """A blunter guard than the extrapolation penalty, and separate from it.

    The penalty makes a distant price harder to bet; these bounds mean it is
    never produced. Out there the distribution is being asked about a region no
    ladder constrains and no book quotes, so the number would be arithmetic
    wearing the clothes of a price.
    """
    ladder = _ladder(FULL_LINES)
    fit = fit_ladder(ladder)

    for line in (4.5, 9.5, 13.5):
        assert price_line(fit, line, ladder).fair_over > 1.0

    for line in (3.5, 4.0, 14.5, 20.5):
        with pytest.raises(LadderError, match="priceable range"):
            price_line(fit, line, ladder)


def test_cards_are_only_priced_up_to_6_5():
    """A 0-5 card match with headroom. Above that is a red-card tail the ladder
    cannot speak to."""
    from app.pricing.counts.config import CARDS
    from app.pricing.counts.price import is_priceable

    assert is_priceable(6.5, CARDS)
    assert is_priceable(0.5, CARDS)
    assert not is_priceable(7.5, CARDS)
    assert not is_priceable(0.0, CARDS)
    # And the corners range does not leak into cards.
    assert not is_priceable(9.5, CARDS)


def test_the_bounds_limit_pricing_not_fitting():
    """A rung outside the range is still a good constraint.

    Excluding it would throw away information that makes every price inside the
    range better — the bound is on what will be QUOTED, not on what is read.
    """
    # 4.5 is deliberately absent: at lam=10.2 an over-4.5 is a 0.97 shot, and
    # applying a 7% margin to that gives sub-1.0 decimal odds — a book no real
    # bookmaker would print. The out-of-range rung here is 14.5.
    ladder = _ladder([6.5, 7.5, 9.5, 13.5, 14.5])
    fit = fit_ladder(ladder)
    assert len(fit.constraints) == 5, "every rung must inform the fit"
    assert abs(fit.lam - TRUE_LAM) <= 0.2
    # ...but the out-of-range line still cannot be priced.
    with pytest.raises(LadderError, match="priceable range"):
        price_line(fit, 14.5, ladder)
