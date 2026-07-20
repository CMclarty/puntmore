"""Per-leg sanity checks (V5 auto-confirm safety net)."""
from app import checks


# --- odds range per market ---

def test_plausible_odds_unflagged():
    assert checks.evaluate_leg(
        "Win and Over 2.5", 3.40, value=0.03,
        matched_fixture="Arsenal vs Chelsea", match_score=0.98,
    ) == []


def test_decimal_misread_flagged():
    # "3.40" scanned as "340" — the classic OCR failure this check exists for.
    flags = checks.evaluate_leg(
        "Win and Over 2.5", 340.0,
        matched_fixture="Arsenal vs Chelsea", match_score=0.98,
    )
    assert checks.FLAG_ODDS_RANGE in flags


def test_range_is_per_market_not_global():
    # 8.0 is unremarkable on Win-and-Over but implausible on BTTS.
    assert checks.FLAG_ODDS_RANGE not in checks.evaluate_leg(
        "Win and Over 2.5", 8.0, matched_fixture="x", match_score=0.9
    )
    assert checks.FLAG_ODDS_RANGE in checks.evaluate_leg(
        "Both Teams to Score", 8.0, matched_fixture="x", match_score=0.9
    )


def test_unknown_market_uses_default_range():
    assert checks.odds_range_for("HT/FT PSG/PSG") == (1.01, 51.0)


# --- fixture matching ---

def test_no_match_flagged():
    flags = checks.evaluate_leg("Win", 2.0, matched_fixture=None)
    assert checks.FLAG_NO_MATCH in flags
    # No match means no confidence flag on top — one problem, one flag.
    assert checks.FLAG_LOW_CONFIDENCE not in flags


def test_loose_match_flagged_low_confidence():
    flags = checks.evaluate_leg(
        "Win", 2.0, matched_fixture="Newmarket vs North Lakes United", match_score=0.63
    )
    assert checks.FLAG_LOW_CONFIDENCE in flags


def test_confident_match_unflagged():
    flags = checks.evaluate_leg(
        "Win", 2.0, matched_fixture="Arsenal vs Chelsea", match_score=0.97
    )
    assert flags == []


# --- value plausibility ---

def test_implausible_value_flagged_both_directions():
    for value in (0.42, -0.42):
        flags = checks.evaluate_leg(
            "Win", 2.0, value=value, matched_fixture="x", match_score=0.9
        )
        assert checks.FLAG_VALUE_IMPLAUSIBLE in flags


def test_ordinary_edge_not_flagged():
    flags = checks.evaluate_leg(
        "Win", 2.0, value=0.09, matched_fixture="x", match_score=0.9
    )
    assert checks.FLAG_VALUE_IMPLAUSIBLE not in flags


def test_unpriced_leg_has_no_value_flag():
    flags = checks.evaluate_leg("Win", 2.0, value=None, matched_fixture=None)
    assert checks.FLAG_VALUE_IMPLAUSIBLE not in flags


# --- encoding ---

def test_encode_empty_is_none_not_blank():
    # Null, so an unflagged leg is distinguishable in the DB from "" noise.
    assert checks.encode_flags([]) is None


def test_flags_round_trip():
    flags = [checks.FLAG_ODDS_RANGE, checks.FLAG_NO_MATCH]
    assert checks.decode_flags(checks.encode_flags(flags)) == flags


def test_decode_none_is_empty():
    assert checks.decode_flags(None) == []
