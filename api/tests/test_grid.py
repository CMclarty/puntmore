"""Derived score-grid model (SPEC §8 / PLAN open item 1)."""
import pytest

from app.pricing.grid import (
    score_grid,
    fit_grid,
    p_home_win,
    p_draw,
    p_away_win,
    p_over,
    p_team_over,
    p_win_and_over,
    model_prob,
)


# --- grid basics ---

def test_grid_is_a_normalised_distribution():
    g = score_grid(1.6, 1.1, -0.05)
    assert sum(g.values()) == pytest.approx(1.0, abs=1e-12)
    assert all(p >= 0 for p in g.values())


def test_outcomes_partition_the_grid():
    g = score_grid(1.4, 1.2, 0.03)
    assert p_home_win(g) + p_draw(g) + p_away_win(g) == pytest.approx(1.0, abs=1e-12)


def test_rho_zero_is_plain_independent_poisson():
    g = score_grid(1.5, 1.2, 0.0)
    # Independence: P(2,1) == P(home=2) * P(away=1)
    p_h2 = sum(p for (h, _), p in g.items() if h == 2)
    p_a1 = sum(p for (_, a), p in g.items() if a == 1)
    assert g[(2, 1)] == pytest.approx(p_h2 * p_a1, rel=1e-9)


def test_rho_shifts_draw_probability():
    # The Dixon-Coles correction exists to fix Poisson's draw bias, so it must
    # actually move draws.
    base = p_draw(score_grid(1.5, 1.2, 0.0))
    lifted = p_draw(score_grid(1.5, 1.2, -0.10))
    assert lifted != pytest.approx(base, abs=1e-6)


# --- totals ---

def test_team_totals_are_monotonic_in_the_line():
    g = score_grid(1.8, 0.9, 0.0)
    assert p_team_over(g, 1.5, home=True) > p_team_over(g, 2.5, home=True)
    assert p_team_over(g, 0.5, home=False) > p_team_over(g, 1.5, home=False)


# --- the compound this module exists for ---

def test_win_and_over_exceeds_naive_product():
    # Result and total are positively correlated through the shared grid, so
    # the joint probability is HIGHER than the product => fair odds LOWER.
    # Naive multiplication overstates fair odds and understates value.
    g = score_grid(1.9, 0.9, 0.0)
    joint = p_win_and_over(g, 2.5, home=True)
    naive = p_home_win(g) * p_over(g, 2.5)
    assert joint > naive
    assert 1 / naive > 1 / joint


def test_win_and_over_is_a_subset_of_both_legs():
    g = score_grid(1.7, 1.0, -0.03)
    joint = p_win_and_over(g, 2.5, home=True)
    assert joint <= p_home_win(g)
    assert joint <= p_over(g, 2.5)


def test_away_side_compound():
    g = score_grid(0.9, 1.9, 0.0)
    assert p_win_and_over(g, 2.5, home=False) > p_win_and_over(g, 2.5, home=True)


# --- fitting ---

def _constraints_from(g, lines=(1.5, 2.5, 3.5, 4.5)):
    cons = [("home_win", None, p_home_win(g)), ("draw", None, p_draw(g))]
    cons += [("over", L, p_over(g, L)) for L in lines]
    return cons


def test_fit_recovers_known_parameters():
    g = score_grid(1.62, 1.10, -0.08)
    lh, la, rho, rmse = fit_grid(_constraints_from(g))
    assert lh == pytest.approx(1.62, abs=0.02)
    assert la == pytest.approx(1.10, abs=0.02)
    assert rho == pytest.approx(-0.08, abs=0.02)
    assert rmse < 1e-4


def test_fit_reproduces_its_own_constraints():
    g = score_grid(1.4, 1.3, 0.05)
    cons = _constraints_from(g)
    lh, la, rho, _ = fit_grid(cons)
    fitted = score_grid(lh, la, rho)
    for kind, line, target in cons:
        assert model_prob(fitted, kind, line) == pytest.approx(target, abs=0.01)


def test_hold_one_out_predicts_a_withheld_line():
    # Only meaningful because the constraint set is over-determined: 6
    # constraints, 3 parameters. On a source giving only 1X2 + one totals line
    # there would be nothing spare to withhold. Half lines only.
    g = score_grid(1.55, 1.15, -0.06)
    cons = _constraints_from(g, lines=(1.5, 2.5, 3.5, 4.5))
    held = cons[3]  # the 2.5 totals line
    kept = [c for c in cons if c is not held]

    lh, la, rho, _ = fit_grid(kept)
    predicted = model_prob(score_grid(lh, la, rho), held[0], held[1])
    assert predicted == pytest.approx(held[2], abs=0.02)


def test_fit_rejects_too_few_constraints():
    with pytest.raises(ValueError):
        fit_grid([("home_win", None, 0.5)])


def test_rmse_reports_misfit():
    # Mutually inconsistent constraints must surface as a large residual rather
    # than a confident wrong fit.
    bad = [
        ("home_win", None, 0.90),
        ("draw", None, 0.05),
        ("over", 2.5, 0.05),   # incompatible with a 0.90 home win rate
        ("over", 3.5, 0.99),   # and with the line below it
    ]
    _, _, _, rmse = fit_grid(bad)
    assert rmse > 0.05
