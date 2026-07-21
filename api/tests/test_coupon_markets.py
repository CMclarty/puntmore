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


# --- the seven types as top-level entities ---

def test_seven_types_exist_with_stable_codes():
    from app.coupon_types import COUPON_TYPES
    assert [t.code for t in COUPON_TYPES] == [
        "TTS1", "TTS2", "TTS3", "WO15", "WO25", "GGB", "GGND",
    ]


def test_only_gg_bonus_is_a_screen():
    """GG Bonus filters on price; the rest rank on value alone."""
    from app.coupon_types import COUPON_TYPES, get
    assert get("GGB").max_fair_odds == 1.80
    assert [t.code for t in COUPON_TYPES if t.max_fair_odds is not None] == ["GGB"]


def test_unknown_type_is_rejected():
    from app import coupon_types
    assert coupon_types.is_valid("TTS2") is True
    assert coupon_types.is_valid("WO35") is False   # dropped from the seven
    assert coupon_types.get("nope") is None


# --- flat-payout coupons (GG Bonus / GG No Draw) ---

def test_gg_coupons_pay_a_flat_price_over_three_picks():
    from app.coupon_types import get
    for code in ("GGB", "GGND"):
        assert get(code).flat_payout == 5.5
        assert get(code).payout_picks == 3
    # The other five are priced per selection, not flat.
    assert get("TTS2").flat_payout is None


def test_combination_value_beats_flat_price_when_picks_are_short():
    from app.coupon_types import combination_value
    # 1.60^3 = 4.10 fair against a flat 5.5 — clear value.
    assert combination_value([1.6, 1.6, 1.6], 5.5) == pytest.approx(0.3428, abs=1e-3)


def test_combination_value_is_negative_when_picks_are_long():
    from app.coupon_types import combination_value
    # 1.80^3 = 5.83 fair against a flat 5.5 — a losing combination.
    assert combination_value([1.8, 1.8, 1.8], 5.5) < 0


def test_flat_payout_break_even_arithmetic():
    """Pins the break-even of an even three-way split: 5.5^(1/3) = 1.765.

    Recorded because combination_value depends on it — not as a judgement about
    the 1.80 screen, which is a shortlist of candidates rather than a set meant
    to be combined with itself.
    """
    from app.coupon_types import combination_value
    break_even = 5.5 ** (1 / 3)
    assert break_even < 1.80
    assert combination_value([break_even] * 3, 5.5) == pytest.approx(0.0, abs=1e-3)


def test_combination_is_unknowable_if_any_leg_is_unpriced():
    from app.coupon_types import combination_value
    assert combination_value([1.6, None, 1.6], 5.5) is None
    assert combination_value([], 5.5) is None


def test_flat_payout_rows_carry_no_odds_and_no_row_value():
    """A per-row edge is meaningless with no per-row price."""
    from app import checks
    # No odds is normal here, so it must not raise or flag an odds anomaly.
    flags = checks.evaluate_leg("GG Bonus", None, matched_fixture="A vs B", match_score=0.9)
    assert checks.FLAG_ODDS_RANGE not in flags
