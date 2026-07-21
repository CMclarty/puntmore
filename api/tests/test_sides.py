"""Both sides of every team-specific market get priced.

Regression cover for a live mispricing: the provider resolved no team at all,
so moneyline always took outcome index 1 (home) and p_win_and_over was
hardcoded home=True. An away selection was priced as its opponent — and since
the home side is usually the favourite, that overstates value, inventing edge
rather than hiding it.
"""
from app.providers.bettingiscool import (
    BettingIsCoolProvider, _side_of, SIDE_THRESHOLD,
)


def _provider():
    return BettingIsCoolProvider("test-key")


def _moneyline_rows():
    # draw / home / away — home is the favourite, as it usually is.
    return [{
        "period": 0, "market": "moneyline", "line": None,
        "odds0": 3.60, "odds1": 1.90, "odds2": 4.20,
    }]


# --- side resolution ---

def test_resolves_home_and_away_picks():
    assert _side_of("PSG Win and Over 2.5", "PSG", "Lyon") is True
    assert _side_of("Lyon Win and Over 2.5", "PSG", "Lyon") is False


def test_resolution_survives_name_variants():
    assert _side_of("Man Utd Win", "Manchester United", "Chelsea") is True


def test_ambiguous_side_is_unresolved_not_home():
    """The whole bug was defaulting to home. Ambiguity must return None."""
    assert _side_of("Win and Over 2.5", "PSG", "Lyon") is None


def test_near_identical_runners_are_unresolved():
    # Scores alike against both — no clear winner, so no guess.
    assert _side_of("United Win", "United", "United") is None


def test_missing_runner_names_are_unresolved():
    assert _side_of("PSG Win", None, "Lyon") is None
    assert _side_of("PSG Win", "PSG", None) is None


# --- both sides priced, and priced differently ---

def test_home_and_away_moneyline_differ():
    p = _provider()
    rows = _moneyline_rows()
    home = p._fair_odds_for(rows, "PSG Win", home=True)
    away = p._fair_odds_for(rows, "Lyon Win", home=False)

    assert home is not None and away is not None
    # The favourite must be the shorter price. If these ever match, the side
    # argument is being ignored again.
    assert home < away


def test_away_price_is_not_the_home_price():
    """Same pick text, opposite sides — the prices must not coincide."""
    p = _provider()
    rows = _moneyline_rows()
    home = p._fair_odds_for(rows, "Win", home=True)
    away = p._fair_odds_for(rows, "Win", home=False)
    assert home is not None and away is not None
    assert home != away


def test_match_totals_are_not_team_specific():
    """"Over 2.5" is a property of the game — both sides would be identical."""
    p = _provider()
    assert p._is_team_specific("Over 2.5") is False
    assert p._is_team_specific("PSG Win and Over 2.5") is True
    assert p._is_team_specific("Lyon Win") is True
