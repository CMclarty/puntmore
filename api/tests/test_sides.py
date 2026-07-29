"""Both sides of every team-specific market get priced.

Regression cover for a live mispricing: the provider resolved no team at all,
so moneyline always took outcome index 1 (home) and p_win_and_over was
hardcoded home=True. An away selection was priced as its opponent — and since
the home side is usually the favourite, that overstates value, inventing edge
rather than hiding it.
"""
from app.providers.base import MatchQuery
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


# --- the side is resolved from the PICK, not the fixture name ---

class _StubProvider(BettingIsCoolProvider):
    """Provider with the network stubbed, to exercise get_fair_odds itself."""

    def __init__(self, fixture, odds_rows):
        super().__init__("test-key")
        self._fixture = fixture
        self._rows = odds_rows

    def _fixtures(self):
        return [self._fixture]

    def _odds(self, event_id):
        return self._rows


def test_side_comes_from_the_pick_not_the_match_name():
    """Caught only against live data: match_name names BOTH teams.

    Resolving the side from "PSG vs Lyon" scores alike against both runners and
    yields None, so every team-specific leg went unpriced. The team is named in
    the pick, so that is what must be matched.
    """
    fixture = {
        "event_id": 1, "league_name": "Ligue 1",
        "runner_home": "PSG", "runner_away": "Lyon",
        "starts": "2026-07-22T19:00:00",
    }
    rows = [{
        "period": 0, "market": "moneyline", "line": None,
        "odds0": 3.60, "odds1": 1.90, "odds2": 4.20,
    }]
    p = _StubProvider(fixture, rows)

    home, away = p.get_fair_odds([
        MatchQuery(match_id=1, match_name="PSG vs Lyon", market="PSG Win"),
        MatchQuery(match_id=2, match_name="PSG vs Lyon", market="Lyon Win"),
    ])

    assert home.side is True
    assert away.side is False
    assert home.fair_odds is not None and away.fair_odds is not None
    assert home.fair_odds < away.fair_odds        # favourite prices shorter
    # Both sides ride along on every result regardless of which was picked.
    assert home.fair_odds_home == away.fair_odds_home
    assert home.fair_odds_away == away.fair_odds_away


# --- Fixture matching: alias table + per-side comparison ---

def test_alias_canonicalises_both_directions():
    """The coupon's English exonym and the board's local name must land on the
    same string — no amount of fuzzy matching bridges Red Star / Crvena Zvezda."""
    from app.normalise import normalise_team
    assert normalise_team("Red Star Belgrade") == normalise_team("Crvena Zvezda")
    assert normalise_team("Sangju") == normalise_team("Gimcheon Sangmu")
    assert normalise_team("MP Mikkeli") == normalise_team("Mikkelin Palloilijat")


def test_per_side_rescues_a_one_sided_name_difference():
    """Whole-string averages a perfect side into a mismatched one and loses the
    fixture. Scoring side-by-side keeps it."""
    from app.providers.bettingiscool import BettingIsCoolProvider as P
    board = ["Crvena Zvezda vs Larne", "Some Other vs Fixture"]
    got = P._best_match("Red Star Belgrade v Larne", board)
    assert got is not None and got[0] == "Crvena Zvezda vs Larne"


def test_per_side_takes_the_weaker_side_so_one_match_cannot_carry_it():
    """min(), not max() or mean: both teams must independently look right, or a
    coincidental overlap on one name drags in an unrelated fixture."""
    from app.providers.bettingiscool import BettingIsCoolProvider as P
    # Away side identical, home side unrelated -> the weak side governs.
    assert P._pair_score(("arsenal", "larne"), ("chelsea", "larne")) < 0.5


def test_unrelated_fixtures_are_still_rejected():
    """The guard that matters most: never invent a match."""
    from app.providers.bettingiscool import BettingIsCoolProvider as P
    board = ["Newmarket vs North Lakes United", "Crvena Zvezda vs Larne"]
    assert P._best_match("Nonexistent FC vs Fake United", board) is None
