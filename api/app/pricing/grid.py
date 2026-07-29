"""Derived score-grid model for compound markets (SPEC §8).

Win-and-Over has no direct price. It MUST NOT be priced as moneyline × over:
the result and the total are positively correlated (a winning team scores more),
so the product overstates fair odds and understates value — measurably, by
roughly 19% on a typical fixture.

Instead, fit a scoreline distribution to the de-vigged market probabilities and
read the compound probability off it. Because "home win" and "over 2.5" are read
off the SAME grid, the correlation falls out of the structure rather than being
modelled explicitly.

Model: independent Poisson with a Dixon–Coles low-score correction.

    P(h, a) = Poisson(h; λ_home) · Poisson(a; λ_away) · τ(h, a)

    τ(0,0) = 1 − λ_home·λ_away·ρ      τ(0,1) = 1 + λ_home·ρ
    τ(1,0) = 1 + λ_away·ρ             τ(1,1) = 1 − ρ
    τ(h,a) = 1 otherwise

ρ corrects the well-known Poisson failure on 0-0/1-0/0-1/1-1, which shows up as
under-predicting draws. Setting ρ = 0 recovers plain independent Poisson.

Identifiability: 3 free parameters (λ_home, λ_away, ρ). bettingiscool supplies
1X2 (2 independent probabilities), a totals ladder (~7 lines) and team totals
for both sides — roughly 10 constraints, so the fit is over-determined and the
hold-one-out check in the tests is meaningful. On a source offering only 1X2
plus a single totals line there would be exactly 3 constraints, the fit would be
exactly identified, and neither the residual nor hold-one-out would tell you
anything.

Half lines only (x.5). Asian quarter and integer lines are out of scope — the
provider filters them out before they reach the fit.

Pure Python on purpose: an 11×11 grid with 3 parameters does not justify adding
numpy/scipy to the container image. The optimiser below is Nelder–Mead.
"""
from math import exp, log, factorial

MAX_GOALS = 10          # grid truncated at 10 goals per team (SPEC §8.1)
_GRID_RANGE = range(MAX_GOALS + 1)


# --- model ---

def _poisson(k: int, lam: float) -> float:
    return exp(-lam) * lam ** k / factorial(k)


def _tau(h: int, a: int, lh: float, la: float, rho: float) -> float:
    """Dixon–Coles low-score correction. Returns 1 outside the 2×2 corner."""
    if h == 0 and a == 0:
        return 1.0 - lh * la * rho
    if h == 0 and a == 1:
        return 1.0 + lh * rho
    if h == 1 and a == 0:
        return 1.0 + la * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def score_grid(lh: float, la: float, rho: float = 0.0) -> dict[tuple[int, int], float]:
    """Joint scoreline distribution, truncated and renormalised to sum to 1."""
    home = [_poisson(h, lh) for h in _GRID_RANGE]
    away = [_poisson(a, la) for a in _GRID_RANGE]
    grid = {}
    for h in _GRID_RANGE:
        for a in _GRID_RANGE:
            # A τ that drives a cell negative is not a valid correction; clamp
            # to zero rather than emitting negative probability mass.
            p = home[h] * away[a] * max(_tau(h, a, lh, la, rho), 0.0)
            grid[(h, a)] = p
    total = sum(grid.values())
    if total <= 0:
        raise ValueError("degenerate grid")
    return {k: v / total for k, v in grid.items()}


# --- reading probabilities off a grid ---

def p_home_win(grid) -> float:
    return sum(p for (h, a), p in grid.items() if h > a)


def p_draw(grid) -> float:
    return sum(p for (h, a), p in grid.items() if h == a)


def p_away_win(grid) -> float:
    return sum(p for (h, a), p in grid.items() if h < a)


def p_over(grid, line: float) -> float:
    """P(total goals over `line`).

    HALF LINES ONLY (x.5). Asian quarter and integer lines are out of scope and
    are filtered out before they reach here (see the provider's _is_half_line):
    quarter lines split the stake and integer lines push, neither of which this
    handles. Passing one in returns the next half-line's answer, silently.
    """
    return sum(p for (h, a), p in grid.items() if h + a > line)


def p_team_over(grid, line: float, home: bool) -> float:
    """P(one team's goals over `line`). Half lines only — see p_over."""
    return sum(p for (h, a), p in grid.items() if (h if home else a) > line)


def p_win_and_over(grid, line: float, home: bool = True) -> float:
    """The compound this module exists for: P(side wins ∧ total > line)."""
    if home:
        return sum(p for (h, a), p in grid.items() if h > a and h + a > line)
    return sum(p for (h, a), p in grid.items() if a > h and h + a > line)


def p_win_to_nil(grid, home: bool = True) -> float:
    """P(side wins ∧ concedes nothing) — Win to Nil.

    Derived off the grid for the same reason Win-and-Over is: the result and
    the opponent's goal count are correlated, so moneyline × P(clean sheet)
    overstates it. Any win to nil is a win, so this is strictly the h>0,a==0
    corner of the win region (0-0 is a draw and excluded by h > a).
    """
    if home:
        return sum(p for (h, a), p in grid.items() if h > a and a == 0)
    return sum(p for (h, a), p in grid.items() if a > h and h == 0)


def p_btts(grid) -> float:
    """P(both teams score) — GG. No vendor market for this, so it is derived."""
    return sum(p for (h, a), p in grid.items() if h >= 1 and a >= 1)


def p_gg_no_draw(grid) -> float:
    """P(both teams score ∧ the result is not a draw) — GG No Draw.

    BTTS minus the score draws (1-1, 2-2, …). NEVER BTTS × (1 − P(draw)):
    both-teams-scoring and the result are correlated through this same grid,
    which is the identical trap that makes moneyline × over wrong for
    Win-and-Over (SPEC §8.5).
    """
    return sum(p for (h, a), p in grid.items() if h >= 1 and a >= 1 and h != a)


# --- constraints ---

# A constraint is (kind, line, target_probability). `line` is unused for 1X2.
KINDS = ("home_win", "draw", "away_win", "over", "home_over", "away_over")


def model_prob(grid, kind: str, line: float | None) -> float:
    if kind == "home_win":
        return p_home_win(grid)
    if kind == "draw":
        return p_draw(grid)
    if kind == "away_win":
        return p_away_win(grid)
    if kind == "over":
        return p_over(grid, line)
    if kind == "home_over":
        return p_team_over(grid, line, home=True)
    if kind == "away_over":
        return p_team_over(grid, line, home=False)
    raise ValueError(f"unknown constraint kind: {kind}")


def sse(params, constraints) -> float:
    """Squared error between model and market probabilities."""
    lh, la, rho = params
    if lh <= 0.01 or la <= 0.01 or lh > 12 or la > 12 or abs(rho) > 0.9:
        return 1e6
    try:
        grid = score_grid(lh, la, rho)
    except ValueError:
        return 1e6
    return sum(
        (model_prob(grid, kind, line) - target) ** 2
        for kind, line, target in constraints
    )


# --- optimiser (Nelder–Mead, pure Python) ---

def _nelder_mead(f, x0, step=0.3, tol=1e-10, max_iter=2000):
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        pt = list(x0)
        pt[i] += step if pt[i] == 0 else step * abs(pt[i])
        simplex.append(pt)
    scores = [f(p) for p in simplex]

    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: scores[i])
        simplex = [simplex[i] for i in order]
        scores = [scores[i] for i in order]
        if abs(scores[-1] - scores[0]) < tol:
            break

        centroid = [sum(p[i] for p in simplex[:-1]) / n for i in range(n)]
        worst = simplex[-1]

        refl = [centroid[i] + (centroid[i] - worst[i]) for i in range(n)]
        f_refl = f(refl)
        if scores[0] <= f_refl < scores[-2]:
            simplex[-1], scores[-1] = refl, f_refl
            continue
        if f_refl < scores[0]:
            exp_pt = [centroid[i] + 2.0 * (centroid[i] - worst[i]) for i in range(n)]
            f_exp = f(exp_pt)
            simplex[-1], scores[-1] = (
                (exp_pt, f_exp) if f_exp < f_refl else (refl, f_refl)
            )
            continue
        contr = [centroid[i] + 0.5 * (worst[i] - centroid[i]) for i in range(n)]
        f_contr = f(contr)
        if f_contr < scores[-1]:
            simplex[-1], scores[-1] = contr, f_contr
            continue
        best = simplex[0]
        for i in range(1, n + 1):
            simplex[i] = [best[j] + 0.5 * (simplex[i][j] - best[j]) for j in range(n)]
            scores[i] = f(simplex[i])

    best_i = min(range(n + 1), key=lambda i: scores[i])
    return simplex[best_i], scores[best_i]


def fit_grid(constraints, fit_rho: bool = True):
    """Fit (λ_home, λ_away, ρ) to de-vigged market probabilities.

    `constraints` is a list of (kind, line, target_probability). Returns
    (lambda_home, lambda_away, rho, rmse). RMSE is the fit residual — with an
    over-determined constraint set it is a genuine diagnostic; watch it rather
    than assuming the parametric form fits.
    """
    if len(constraints) < 2:
        raise ValueError("need at least 2 constraints to fit a grid")

    def objective(p):
        return sse((p[0], p[1], p[2] if fit_rho else 0.0), constraints)

    best, best_score = None, None
    # Multi-start: Nelder–Mead is local, and the seeds bracket realistic
    # scoring rates rather than trusting one arbitrary starting point.
    for lh0, la0 in ((1.5, 1.1), (1.0, 1.0), (2.0, 1.5), (1.2, 1.8)):
        params, score = _nelder_mead(objective, [lh0, la0, 0.0 if fit_rho else 0.0])
        if best_score is None or score < best_score:
            best, best_score = params, score

    lh, la, rho = best[0], best[1], (best[2] if fit_rho else 0.0)
    rmse = (best_score / len(constraints)) ** 0.5
    return lh, la, rho, rmse
