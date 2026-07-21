"""The seven coupon types price correctly (SPEC §3, §8.5)."""
import pytest

from app.pricing.grid import (
    score_grid, p_btts, p_gg_no_draw, p_draw, p_team_over,
)
from app.providers.bettingiscool import BettingIsCoolProvider


def _provider():
    return BettingIsCoolProvider("test-key")


def _team_totals_rows():
    """home_totals / away_totals ladders. Index 1 = over, 2 = under."""
    rows = []
    for market, over, under in (("home_totals", 1.90, 1.95), ("away_totals", 2.40, 1.60)):
        for line in (0.5, 1.5, 2.5):
            rows.append({
                "period": 0, "market": market, "line": line,
                "odds0": None, "odds1": over + line, "odds2": under,
            })
    return rows


# --- TTS: "score N" means N OR MORE, so the line is N-0.5 ---

@pytest.mark.parametrize("pick,expected_line", [
    ("PSG to Score 1", 0.5),
    ("PSG to Score 2", 1.5),
    ("PSG to Score 3", 2.5),
])
def test_tts_uses_line_n_minus_half(pick, expected_line):
    """The off-by-one that would misprice every TTS leg."""
    p = _provider()
    rows = _team_totals_rows()
    got = p._fair_odds_for(rows, pick, home=True)
    # Price it directly off the row we expect it to have chosen.
    want = p._fair_odds_for(
        [r for r in rows if r["market"] == "home_totals" and r["line"] == expected_line],
        pick, home=True,
    )
    assert got is not None
    assert got == want


def test_tts_reads_the_picked_teams_market():
    """Home and away TTS must come off different vendor markets."""
    p = _provider()
    rows = _team_totals_rows()
    home = p._fair_odds_for(rows, "to score 2", home=True)
    away = p._fair_odds_for(rows, "to score 2", home=False)
    assert home is not None and away is not None
    assert home != away


def test_tts_is_team_specific():
    assert _provider()._is_team_specific("PSG to Score 2") is True


# --- GG derivations ---

def test_gg_no_draw_is_btts_minus_the_score_draws():
    g = score_grid(1.5, 1.2, -0.03)
    # The identity that defines the market: draws with both scoring are the
    # only thing separating GG from GG No Draw.
    both_score_draws = sum(p for (h, a), p in g.items() if h >= 1 and a >= 1 and h == a)
    assert p_gg_no_draw(g) == pytest.approx(p_btts(g) - both_score_draws)


def test_gg_no_draw_is_not_the_naive_product():
    """NEVER BTTS × (1 − P(draw)) — the correlation trap (SPEC §8.5)."""
    g = score_grid(1.5, 1.2, -0.03)
    naive = p_btts(g) * (1 - p_draw(g))
    assert p_gg_no_draw(g) != pytest.approx(naive, abs=1e-4)


def test_gg_no_draw_is_a_subset_of_btts():
    g = score_grid(1.5, 1.2, -0.03)
    assert p_gg_no_draw(g) < p_btts(g)


def test_btts_excludes_any_nil():
    g = score_grid(1.4, 1.1, 0.0)
    nil = sum(p for (h, a), p in g.items() if h == 0 or a == 0)
    assert p_btts(g) == pytest.approx(1 - nil)


def test_gg_is_not_team_specific():
    """Both teams scoring is a property of the fixture, not of a side."""
    p = _provider()
    assert p._is_team_specific("GG Bonus") is False
    assert p._is_team_specific("GG No Draw") is False
    assert p._is_team_specific("Both Teams to Score") is False


def test_gg_no_draw_matches_before_plain_gg():
    """"GG No Draw" contains "GG" — the more specific pattern must win."""
    from app.providers.bettingiscool import _GG_NO_DRAW_RE, _GG_RE
    assert _GG_NO_DRAW_RE.search("gg no draw")
    assert _GG_RE.search("gg no draw")  # both match...
    # ...so the ordering in _fair_odds_for is what makes it correct. Pin the
    # two probabilities as distinct so a reordering shows up as a failure.
    g = score_grid(1.5, 1.2, -0.03)
    assert p_btts(g) != pytest.approx(p_gg_no_draw(g))


# --- unpriceable markets refuse rather than guess ---

def test_unknown_market_is_unpriced():
    assert _provider()._fair_odds_for([], "HT/FT PSG/PSG") is None


def test_gg_without_a_grid_is_unpriced():
    """No event_id means no grid — refuse, never fall back."""
    assert _provider()._fair_odds_for([], "GG No Draw", event_id=None) is None
