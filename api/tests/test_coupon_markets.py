"""The seven coupon types price correctly (SPEC §3, §8.5)."""
from datetime import date

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

def test_types_exist_with_stable_codes():
    from app.coupon_types import COUPON_TYPES
    assert [t.code for t in COUPON_TYPES] == [
        "TTS1", "TTS2", "TTS3", "WO15", "WO25", "WTN", "GGB", "GGND", "HILLS",
    ]


def test_priced_and_ladder_are_independent_shapes():
    """A coupon may print prices, pay a ladder, or both.

    HILLS is the reason these are two flags: the William Hill "Both score no
    draw" sheet prints a price on every fixture AND a ladder for N correct.
    Collapsing them back into one boolean silently discards half of it.
    """
    from app.coupon_types import get
    priced_only = get("TTS1")
    assert priced_only.prices_each_selection and not priced_only.pays_ladder

    ladder_only = get("GGND")
    assert not ladder_only.prices_each_selection and ladder_only.pays_ladder

    both = get("HILLS")
    assert both.prices_each_selection and both.pays_ladder


def test_hills_carries_its_own_ladder_not_betfreds():
    """Ladders are per bookmaker. Hill's and Betfred's agree at 3 and 4 correct
    and then diverge sharply — sharing one default would misprice long folds."""
    from app.coupon_types import get
    hills, betfred = get("HILLS").default_ladder, get("GGND").default_ladder
    assert hills[3] == betfred[3] == 5.5
    assert hills[4] == betfred[4] == 10.0
    assert hills[5] == 21.0 and betfred[5] == 17.0
    assert hills[12] == 2724.0 and betfred[12] == 801.0


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

def test_gg_coupons_pay_off_a_ladder_not_a_single_price():
    """Any N correct pays a printed rung — 3 is just the first of them."""
    from app.coupon_types import get
    for code in ("GGB", "GGND"):
        t = get(code)
        assert t.prices_each_selection is False
        # Observed on the Betfred midweek issue: 9/2, 9/1, 16/1 ...
        assert t.default_ladder[3] == 5.5
        assert t.default_ladder[4] == 10.0
        assert t.default_ladder[8] == 101.0
        assert len(t.default_ladder) >= 10
    # The other five print a price on every fixture instead.
    assert get("TTS2").prices_each_selection is True
    assert get("TTS2").default_ladder == {}


def test_gg_coupons_run_variable_windows():
    """GG issues move; the priced coupons run the fixed weekly schedule."""
    from app.coupon_types import get
    assert get("GGB").period_codes == ("MIDWEEK", "WEEKEND")
    assert get("TTS2").period_codes == ("MON_WED", "WED_FRI", "SAT", "SUN")


def test_best_combination_searches_every_rung():
    """The 3-fold is not assumed — longer folds often pay better."""
    from app.coupon_types import best_combination, get
    fairs = [1.55, 1.60, 1.62, 1.68, 1.72, 1.75, 1.80, 1.85, 1.90, 1.95]
    best = best_combination(fairs, get("GGB").default_ladder)
    assert best["picks"] > 3          # a longer fold wins on this spread
    assert best["value"] > 0
    assert best["fair"] < best["payout"]


def test_best_combination_uses_the_shortest_prices():
    """An N-fold is best built from the N likeliest, minimising the product."""
    from app.coupon_types import best_combination, combination_value
    ladder = {3: 5.5}
    fairs = [1.5, 1.6, 1.7, 4.0, 8.0]
    best = best_combination(fairs, ladder)
    assert best["fair"] == pytest.approx(1.5 * 1.6 * 1.7, abs=1e-3)


def test_best_combination_skips_rungs_the_coupon_cannot_fill():
    from app.coupon_types import best_combination
    # Only two priced fixtures — a 3-fold is impossible.
    assert best_combination([1.6, 1.7], {3: 5.5}) is None


def test_best_combination_none_without_a_ladder():
    from app.coupon_types import best_combination
    assert best_combination([1.6, 1.7, 1.8], {}) is None


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
    """A per-row edge is meaningless with no per-row price.

    A ladder coupon's rows price (fair odds are knowable) but keep value None —
    the edge lives on the COMBINATION, not the row.
    """
    from app.models import Leg
    from app.pricing.legs import price_legs
    from app.providers.mock_odds import MockOddsProvider

    leg = Leg(match_name="A vs B", pick="GG Bonus", odds=None, rank=0)
    leg.id = 1
    price_legs([leg], MockOddsProvider({("A vs B", "GG Bonus"): 1.80}))
    assert leg.fair_odds == 1.80
    assert leg.value is None


# --- coupon periods (the fixed weekly schedule) ---

def test_fixed_and_variable_periods():
    from app.coupon_periods import FIXED_PERIODS, VARIABLE_PERIODS
    assert [p.code for p in FIXED_PERIODS] == ["MON_WED", "WED_FRI", "SAT", "SUN"]
    # GG issues print their own dates, so these carry no weekday schedule.
    assert [p.code for p in VARIABLE_PERIODS] == ["MIDWEEK", "WEEKEND"]
    assert all(p.variable and not p.weekdays for p in VARIABLE_PERIODS)


def test_variable_periods_have_no_derivable_dates():
    """They must come from the coupon's fixtures, never from the calendar."""
    from app.coupon_periods import date_range
    assert date_range("MIDWEEK", date(2026, 7, 20)) is None
    assert date_range("WEEKEND", date(2026, 7, 20)) is None


def test_wednesday_belongs_to_two_windows():
    """Mon–Wed and Wed–Fri overlap on Wednesday — how they are issued."""
    from app.coupon_periods import get
    assert 2 in get("MON_WED").weekdays
    assert 2 in get("WED_FRI").weekdays


@pytest.mark.parametrize("code,upload,expected", [
    # Uploaded on its normal day, week of Mon 20 Jul 2026.
    ("MON_WED", date(2026, 7, 20), (date(2026, 7, 20), date(2026, 7, 22))),
    ("WED_FRI", date(2026, 7, 22), (date(2026, 7, 22), date(2026, 7, 24))),
    ("SAT",     date(2026, 7, 24), (date(2026, 7, 25), date(2026, 7, 25))),
    ("SUN",     date(2026, 7, 24), (date(2026, 7, 26), date(2026, 7, 26))),
])
def test_period_resolves_to_the_right_dates(code, upload, expected):
    from app.coupon_periods import date_range
    assert date_range(code, upload) == expected


def test_uploading_mid_window_keeps_that_window():
    """A Mon–Wed coupon uploaded on Tuesday still covers Mon–Wed, not next week."""
    from app.coupon_periods import date_range
    assert date_range("MON_WED", date(2026, 7, 21)) == (
        date(2026, 7, 20), date(2026, 7, 22)
    )


def test_saturday_coupon_uploaded_on_the_day_covers_that_day():
    from app.coupon_periods import date_range
    assert date_range("SAT", date(2026, 7, 25)) == (
        date(2026, 7, 25), date(2026, 7, 25)
    )


def test_upload_day_preselects_its_period():
    from app.coupon_periods import suggest
    assert suggest(date(2026, 7, 20)) == "MON_WED"   # Monday
    assert suggest(date(2026, 7, 22)) == "WED_FRI"   # Wednesday
    assert suggest(date(2026, 7, 24)) == "SAT"       # Friday -> Saturday first


# --- kickoffs: coupons print a weekday, never a date ---

@pytest.mark.parametrize("text,expected", [
    ("MON 17:00", (2026, 7, 20, 17, 0)),
    ("TUE 19:45", (2026, 7, 21, 19, 45)),
    ("THU 01:30", (2026, 7, 23, 1, 30)),
    ("SAT 15:00", (2026, 7, 25, 15, 0)),
])
def test_kickoff_resolves_forward_from_the_upload(text, expected):
    from app.coupon_periods import parse_kickoff
    got = parse_kickoff(text, date(2026, 7, 20))   # a Monday
    assert (got.year, got.month, got.day, got.hour, got.minute) == expected


@pytest.mark.parametrize("bad", [None, "", "garbage", "MON 99:00", "XYZ 10:00", "17:00"])
def test_unparseable_kickoff_is_none_not_a_guess(bad):
    """A wrong kickoff would misorder the list and mislabel urgency."""
    from app.coupon_periods import parse_kickoff
    assert parse_kickoff(bad, date(2026, 7, 20)) is None


def test_variable_window_comes_from_the_fixtures():
    """The observed coupon printed Mon 20 – Thu 23; the fixtures agree."""
    from app.coupon_periods import parse_kickoff, range_from_kickoffs
    ref = date(2026, 7, 20)
    kickoffs = [parse_kickoff(t, ref) for t in
                ("MON 17:00", "WED 20:00", "THU 01:30", "TUE 19:45")]
    assert range_from_kickoffs([k.date() for k in kickoffs]) == (
        date(2026, 7, 20), date(2026, 7, 23)
    )


# --- the payout ladder is read off the coupon, not configured ---

def test_ladder_parses_printed_fractions():
    from app.providers.claude_ocr import _parse_ladder
    assert _parse_ladder([
        {"picks": 3, "fractional": "9/2"},
        {"picks": 13, "fractional": "1,500/1"},   # thousands separator
    ]) == {3: 5.5, 13: 1501.0}


def test_unreadable_ladder_rungs_are_dropped_not_guessed():
    """A wrong rung misvalues every combination that uses it."""
    from app.providers.claude_ocr import _parse_ladder
    assert _parse_ladder([
        {"picks": 3, "fractional": "9/2"},
        {"picks": 4, "fractional": "??"},
        {"picks": "x", "fractional": "9/1"},
        {"picks": 5, "fractional": "1/0"},
    ]) == {3: 5.5}
    assert _parse_ladder(None) == {}


# --- landing page: which issues of a type are live ---

def test_types_report_their_live_and_upcoming_issues(db_session_factory=None):
    """The landing page needs per-ISSUE status, not just per-type. Two windows
    of one type can be live at once, which is the whole point."""
    from datetime import date, timedelta
    from app.models import Coupon, Leg

    from tests.test_coupons import TestSession, test_engine  # reuse the harness
    from app.database import Base
    Base.metadata.create_all(bind=test_engine)
    db = TestSession()
    try:
        today = date.today()
        # Mon–Wed ending today and Wed–Fri starting today — BOTH live.
        a = Coupon(type_code="TTS2", period="MON_WED", match_day=today - timedelta(days=2),
                   period_ends=today, status="confirmed")
        b = Coupon(type_code="TTS2", period="WED_FRI", match_day=today,
                   period_ends=today + timedelta(days=2), status="confirmed")
        old = Coupon(type_code="TTS2", period="SAT", match_day=today - timedelta(days=9),
                     period_ends=today - timedelta(days=9), status="confirmed")
        db.add_all([a, b, old])
        db.flush()
        db.add_all([
            Leg(coupon_id=a.id, match_name="A vs B", pick="x", odds=2.0, fair_odds=1.9, rank=0),
            Leg(coupon_id=b.id, match_name="C vs D", pick="y", odds=2.0, rank=0),
        ])
        db.commit()

        assert a.period_status == "active"
        assert b.period_status == "active"
        assert old.period_status == "past"
        # A leg reads its window straight off its coupon — one definition.
        assert db.query(Leg).filter(Leg.coupon_id == a.id).one().period_status == "active"
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)
