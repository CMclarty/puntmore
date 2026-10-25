"""The binomial split: a team's count as a share of the match count."""
import pytest

from app.pricing.counts.dist import CountDist
from app.pricing.counts.split import (
    DISAGREE, TeamSplit, estimate_share, price_team_line, share_from_handicap,
    share_from_team_line,
)

T = CountDist(10.5, 1.25, 80)       # a match-corners law around 10.5


def test_the_two_marginals_are_a_split_of_the_total():
    s = TeamSplit(T, 0.6)
    home = [s.pmf(k) for k in range(T.grid_max + 1)]
    away = [s.pmf(k, home=False) for k in range(T.grid_max + 1)]
    assert sum(home) == pytest.approx(1.0, abs=1e-9)
    assert sum(away) == pytest.approx(1.0, abs=1e-9)
    total_mean = sum(k * T.pmf(k) for k in range(T.grid_max + 1))
    assert s.mean() == pytest.approx(0.6 * total_mean, rel=1e-6)
    assert s.mean(home=False) == pytest.approx(0.4 * total_mean, rel=1e-6)


def test_a_team_is_tighter_than_the_match():
    """Binomial thinning: dispersion 1 + p(D-1), not D."""
    s = TeamSplit(T, 0.5)
    mean = s.mean()
    var = sum((k - mean) ** 2 * s.pmf(k) for k in range(T.grid_max + 1))
    assert var / mean == pytest.approx(1 + 0.5 * (1.25 - 1), rel=0.03)


def test_three_way_is_symmetric_at_half_and_sums_to_one():
    more, equal, less = TeamSplit(T, 0.5).three_way()
    assert more == pytest.approx(less, abs=1e-9)
    assert more + equal + less == pytest.approx(1.0, abs=1e-9)
    assert 0.1 < equal < more
    # The favourite's chance of "most corners" rises with its share.
    assert TeamSplit(T, 0.6).three_way()[0] > more


def test_handicap_cover_is_the_three_way_at_zero_and_monotone():
    s = TeamSplit(T, 0.55)
    more, equal, _ = s.three_way()
    # home -0.5: must win outright on corners.
    assert s.handicap_cover(-0.5) == pytest.approx(more, abs=1e-9)
    # home +0.5: wins or draws.
    assert s.handicap_cover(0.5) == pytest.approx(more + equal, abs=1e-9)
    assert s.handicap_cover(-1.5) < s.handicap_cover(-0.5) < s.handicap_cover(1.5)


def test_share_round_trips_from_each_source():
    s = TeamSplit(T, 0.58)
    assert share_from_team_line(T, 5.5, s.over(5.5, home=True), home=True) == pytest.approx(0.58, abs=2e-3)
    assert share_from_team_line(T, 4.5, s.over(4.5, home=False), home=False) == pytest.approx(0.58, abs=2e-3)
    assert share_from_handicap(T, -1.5, s.handicap_cover(-1.5)) == pytest.approx(0.58, abs=2e-3)


def test_sources_that_agree_give_a_tight_range_and_contradiction_is_flagged():
    s = TeamSplit(T, 0.58)
    # Every quote Pinnacle posts goes in: two home rungs, an away rung, two
    # handicaps — five observations of the one share.
    agreed = estimate_share(
        T, home_lines=[(5.5, s.over(5.5)), (6.5, s.over(6.5))],
        away_lines=[(4.5, s.over(4.5, home=False))],
        handicaps=[(-1.5, s.handicap_cover(-1.5)), (0.5, s.handicap_cover(0.5))],
    )
    assert agreed.p == pytest.approx(0.58, abs=3e-3)
    assert agreed.hi - agreed.lo < 0.01 and not agreed.disagree
    assert len(agreed.sources) == 5 and agreed.kinds == {"home_line", "away_line", "handicap"}
    assert set(agreed.sources) == {"home_line@5.5", "home_line@6.5", "away_line@4.5",
                                   "handicap@-1.5", "handicap@0.5"}

    # The home line says 0.58, the away line says the away side has 0.58 too.
    contradiction = estimate_share(
        T, home_lines=[(5.5, s.over(5.5))], away_lines=[(5.5, s.over(5.5))],
    )
    assert contradiction.disagree and contradiction.hi - contradiction.lo > DISAGREE

    solo = estimate_share(T, home_lines=[(5.5, s.over(5.5))])
    assert "share_from_one_source" in solo.flags and solo.hi - solo.lo == pytest.approx(0.06)
    assert estimate_share(T) is None


def test_a_team_line_is_priced_with_a_band_that_contains_the_point():
    share = estimate_share(T, home_lines=[(5.5, TeamSplit(T, 0.58).over(5.5))])
    band = [T, CountDist(10.5, 1.15, 80), CountDist(10.5, 1.35, 80)]
    fp = price_team_line(band, share, 6.5, home=True, quoted_range=(5.5, 5.5))
    assert fp.kind == "half" and fp.p_push == 0.0
    assert fp.fair_over == pytest.approx(1 / fp.p_over, abs=0.006)      # presented to 2 dp
    assert fp.band_over[0] <= fp.fair_over <= fp.band_over[1]
    assert fp.band_under[0] <= fp.fair_under <= fp.band_under[1]
    assert "team_split" in fp.flags and fp.beyond_ladder == 0.0     # 6.5 is within the grace
    far = price_team_line(band, share, 8.5, home=True, quoted_range=(5.5, 5.5))
    assert far.beyond_ladder == pytest.approx(2.0) and far.extrapolated
    with pytest.raises(Exception):
        price_team_line(band, share, 6.0, home=True)                  # pushes never priced
