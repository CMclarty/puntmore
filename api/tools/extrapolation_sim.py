"""How wrong is a price read off a fitted ladder, beyond the ladder's own lines?

Derives `ModelConfig.extrapolation_prob_penalty` (pricing/counts/config.py) and
reports the pipeline's accuracy, by simulation through the PRODUCTION fit:

  1. Draw a "true" count distribution for a match — mean, dispersion and SHAPE
     from the ranges the outcome data supports (Oct 2026 study: ~107k matches of
     corners/cards, ~157k of goals with over/under prices). The shape is drawn
     from families that fit the data equally well (negative binomial,
     compound-Poisson "clustered" corners, Poisson-lognormal), because which of
     them the truth follows is exactly what a ladder cannot tell you.
  2. Print the ladder a Pinnacle-like book would quote around its centre: a
     margin loaded power-style or Shin-style (the book's allocation is not
     known exactly), prices rounded to 2 decimals.
  3. Fit it with fit_ladder / price_line exactly as the sweep does, and price
     lines 1-3 counts beyond the ladder (and inside it, as a control).
  4. Record how far the TRUE fair price sits outside the band's least
     favourable end — the edge the band failed to protect — per side, as a
     probability per count beyond the grace.

The penalty is the 90th percentile of that shortfall: with it, the band plus
penalty covers the true price on at least 9 in 10 extrapolated lines.

    .venv/bin/python tools/extrapolation_sim.py [ladders_per_market] [seed]

Pure Python on purpose (the app has no numpy): what it measures is the code
that runs live.
"""
import math
import random
import sys
from dataclasses import dataclass

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])

from app.pricing.counts.config import CARDS, CORNERS, GOALS          # noqa: E402
from app.pricing.counts.dist import CountDist                         # noqa: E402
from app.pricing.counts.errors import LadderError                     # noqa: E402
from app.pricing.counts.fit import fit_ladder                         # noqa: E402
from app.pricing.counts.ladder import Ladder, Rung                    # noqa: E402
from app.pricing.counts.lines import line_kind, model_probability, QUARTER, fair_quarter_odds  # noqa: E402
from app.pricing.counts.price import price_line, is_priceable        # noqa: E402


class Pmf:
    """A plain pmf with the cdf/sf/pmf interface lines.py expects."""

    def __init__(self, probs):
        s = sum(probs)
        self.p = [x / s for x in probs]
        self.grid_max = len(self.p) - 1
        run, self.c = 0.0, []
        for x in self.p:
            run += x
            self.c.append(run)

    def pmf(self, k):
        return self.p[k] if 0 <= k <= self.grid_max else 0.0

    def cdf(self, k):
        if k < 0:
            return 0.0
        return 1.0 if k >= self.grid_max else self.c[k]

    def sf(self, k):
        if k <= 0:
            return 1.0
        return 0.0 if k > self.grid_max else 1.0 - self.c[k - 1]


def polya_aeppli(mean, d, n):
    """Compound Poisson with geometric cluster sizes (clustered corners)."""
    pi = (d - 1.0) / (d + 1.0)
    theta = mean * (1.0 - pi)
    f = [0.0] + [(1.0 - pi) * pi ** (j - 1) for j in range(1, n + 1)]
    p = [math.exp(-theta)]
    for k in range(1, n + 1):
        p.append(theta / k * sum(j * f[j] * p[k - j] for j in range(1, k + 1)))
    return p


def poisson_lognormal(mean, d, n, nodes=41):
    """Poisson mixed over a lognormal rate, variance matched: D = 1 + mean*(e^s2 - 1)."""
    s2 = math.log(1.0 + (d - 1.0) / mean)
    s = math.sqrt(s2)
    p = [0.0] * (n + 1)
    tot = 0.0
    for i in range(nodes):
        z = -4.0 + 8.0 * i / (nodes - 1)
        w = math.exp(-0.5 * z * z)
        lam = mean * math.exp(s * z - s2 / 2.0)
        cur = math.exp(-lam)
        for k in range(n + 1):
            p[k] += w * cur
            cur *= lam / (k + 1)
        tot += w
    return [x / tot for x in p]


def vig(p_over, margin, style, rng):
    """Prices a book loading `margin` would print for a fair two-way p."""
    p_over = min(max(p_over, 1e-4), 1 - 1e-4)
    if style == "power":
        lo, hi = 0.3, 1.0
        for _ in range(80):
            k = (lo + hi) / 2
            if p_over ** k + (1 - p_over) ** k > 1 + margin:
                lo = k
            else:
                hi = k
        qo, qu = p_over ** k, (1 - p_over) ** k
    else:
        # Shin's book (Shin 1993): with insider share z, implied probability
        # q_i = a_i * sum_j a_j, a_i = sqrt(z p_i + (1 - z) p_i^2). The booksum
        # (sum a)^2 rises with z; solve z for the requested margin.
        def book(z):
            ao = math.sqrt(z * p_over + (1 - z) * p_over ** 2)
            au = math.sqrt(z * (1 - p_over) + (1 - z) * (1 - p_over) ** 2)
            return ao * (ao + au), au * (ao + au)
        lo, hi = 0.0, 0.5
        for _ in range(80):
            z = (lo + hi) / 2
            qo, qu = book(z)
            if qo + qu > 1 + margin:
                hi = z
            else:
                lo = z
        qo, qu = book((lo + hi) / 2)
    o, u = 1.0 / qo, 1.0 / qu
    return round(o, 2), round(u, 2)


MARKETS = {
    "corners": dict(config=CORNERS, mean=(8.5, 12.0), d=(1.05, 1.30),
                    families=("nb", "polya", "pln"), margin=(0.035, 0.065)),
    "cards": dict(config=CARDS, mean=(3.5, 5.5), d=(0.92, 1.15),
                  families=("nb", "polya", "pln"), margin=(0.04, 0.07)),
    "goals": dict(config=GOALS, mean=(2.2, 3.2), d=(0.94, 1.03),
                  families=("model",), margin=(0.025, 0.05)),
}


def true_dist(market, spec, rng):
    mean = rng.uniform(*spec["mean"])
    d = rng.uniform(*spec["d"])
    fam = rng.choice(spec["families"])
    n = spec["config"].grid_max
    if fam == "model" or d < 1.0:
        ze = rng.uniform(0.05, 0.13) if market == "goals" else 0.0
        return Pmf(CountDist(mean, d, n, ze)._pmf), mean, d, "hybrid"
    if fam == "nb":
        return Pmf(CountDist(mean, d, n)._pmf), mean, d, fam
    if fam == "polya":
        return Pmf(polya_aeppli(mean, d, n)), mean, d, fam
    return Pmf(poisson_lognormal(mean, d, n)), mean, d, fam


def ladder_lines(market, mean):
    c = math.floor(mean) + 0.5
    if market == "corners":
        return [c - 2, c - 1, c, c + 1, c + 2]
    if market == "cards":
        return [c - 1.5, c - 0.5, c + 0.5, c + 1.5, c + 1.0]
    return [1.5, 2.25, 2.5, 2.75, 3.5]


def fair_two_way(dist, line):
    """The fair over probability a book devigs to, per line type."""
    if line_kind(line) == QUARTER:
        return 1.0 / fair_quarter_odds(dist, line, True)
    return model_probability(dist, line, over=True)


def true_fair_odds(dist, line, over):
    kind = line_kind(line)
    if kind == QUARTER:
        return fair_quarter_odds(dist, line, over)
    k = math.floor(line)
    if kind == "half":
        p = dist.sf(k + 1) if over else dist.cdf(k)
        return 1.0 / p
    push = dist.pmf(int(line))
    p = dist.sf(int(line) + 1) if over else dist.cdf(int(line) - 1)
    return (1.0 - push) / p


@dataclass
class Case:
    market: str
    beyond: float
    side: str
    p_side: float
    shortfall: float       # true_fair/band_hi - 1 (>0: band failed to cover)
    point_err: float       # model fair / true fair - 1
    family: str


def run(n_per_market=150, seed=1):
    rng = random.Random(seed)
    cases: list[Case] = []
    for market, spec in MARKETS.items():
        cfg = spec["config"]
        done = 0
        while done < n_per_market:
            dist, mean, d, fam = true_dist(market, spec, rng)
            style = "power" if rng.random() < 0.7 else "shin"
            rungs = []
            for line in ladder_lines(market, mean):
                o, u = vig(fair_two_way(dist, line), rng.uniform(*spec["margin"]), style, rng)
                if o > 1.01 and u > 1.01:
                    rungs.append(Rung(line, over=o, under=u))
            ladder = Ladder(f"sim{done}", market, rungs, league=None)
            try:
                fit = fit_ladder(ladder, cfg)
            except LadderError:
                continue
            lo, hi = fit.quoted_range
            targets = [lo + 0.5 if line_kind(lo) != "half" else lo + 1.0]
            targets += [lo - 1.0 - j for j in range(0, 4)] + [hi + 1.0 + j for j in range(0, 4)]
            for line in targets:
                if not is_priceable(line, cfg) or line_kind(line) not in ("half", "integer", "quarter"):
                    continue
                try:
                    fp = price_line(fit, line, ladder)
                except LadderError:
                    continue
                for side in ("over", "under"):
                    tf = true_fair_odds(dist, line, side == "over")
                    band_hi = fp.band_over[1] if side == "over" else fp.band_under[1]
                    fair = fp.fair_over if side == "over" else fp.fair_under
                    p_side = fp.p_over if side == "over" else fp.p_under
                    if band_hi <= 1.0 or tf <= 1.0 or not math.isfinite(tf):
                        continue
                    cases.append(Case(market, fp.beyond_ladder, side, p_side,
                                      tf / band_hi - 1.0, fair / tf - 1.0, fam))
            done += 1
    return cases


def quantile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def report(cases):
    print(f"{'market':8s} {'beyond':>6s} {'n':>5s} {'|point err| p50':>16s} {'p90':>7s} "
          f"{'band miss %':>11s} {'needed prob/count p90':>22s}")
    needed_all = []
    for market in MARKETS:
        for b in sorted({c.beyond for c in cases if c.market == market}):
            sel = [c for c in cases if c.market == market and c.beyond == b]
            if not sel:
                continue
            errs = [abs(c.point_err) for c in sel]
            miss = [c for c in sel if c.shortfall > 0]
            # probability-per-count needed to cover each shortfall
            need = [max(c.shortfall, 0.0) * c.p_side / b for c in sel] if b > 0 else []
            needed_all += need
            print(f"{market:8s} {b:6.1f} {len(sel):5d} {quantile(errs, .5):16.4f} {quantile(errs, .9):7.4f} "
                  f"{100 * len(miss) / len(sel):10.1f}% "
                  f"{(quantile(need, .9) if need else float('nan')):22.4f}")
    print(f"\nprobability penalty per count covering 90% of extrapolated sides: "
          f"{quantile(needed_all, .9):.4f}")
    return quantile(needed_all, .9)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    report(run(n, seed))
