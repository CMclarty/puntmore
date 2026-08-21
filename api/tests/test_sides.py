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


# --- Team-name normalisation: accents, city names, club forms, squads ---

def test_accents_and_non_decomposing_letters_fold():
    """Coupons print ASCII; boards carry the real spelling. Ø, Æ, Ł and friends
    are distinct letters that NFKD leaves alone, so they need folding by hand."""
    from app.normalise import normalise_team as n
    assert n("Gyori ETO") == n("Győri ETO")
    assert n("Bodo/Glimt") == n("Bodø/Glimt")
    assert n("Malmo FF") == n("Malmö FF")
    assert n("Legia Warsaw") == n("Legia Warszawa")


def test_city_names_translate():
    """The club word is shared and only the city differs — one token map fixes a
    whole family of clubs rather than one alias each."""
    from app.normalise import normalise_team as n
    assert n("Sparta Prague") == n("Sparta Praha")
    assert n("Rapid Vienna") == n("Rapid Wien")
    assert n("Bayern Munich") == n("Bayern München")
    assert n("Dynamo Kiev") == n("Dynamo Kyiv")


def test_shortened_names_match_by_containment():
    """One source prints the short name, the other the full one."""
    from app.normalise import team_similarity as t
    assert t("Paide", "Paide Linnameeskond") >= 0.9
    assert t("Sutjeska", "Sutjeska Niksic") >= 0.9
    assert t("AGF Aarhus", "AGF") >= 0.9          # three-letter club identity
    assert t("AaB Aalborg", "AaB") >= 0.9


def test_word_order_does_not_break_a_match():
    from app.normalise import team_similarity as t
    assert t("ETO Gyor", "Gyori ETO") >= 0.9


def test_reserve_sides_never_match_the_first_team():
    """A B/reserve/youth side has its own, much longer prices. Matching a first
    team against one produced a confident wrong price on a live coupon."""
    from app.normalise import team_similarity as t
    assert t("Wisla Krakow", "Wisla Krakow II") == 0.0
    assert t("AaB Aalborg", "AaB II") == 0.0
    assert t("Real Madrid", "Real Madrid B") == 0.0
    assert t("Ajax", "Ajax U21") == 0.0


def test_different_clubs_sharing_a_name_stay_apart():
    from app.normalise import team_similarity as t
    assert t("Dinamo Zagreb", "Dinamo Tbilisi") < 0.72
    assert t("Inter Milan", "Inter Turku") < 0.72
    assert t("AGF Aarhus", "AGF Odense") < 0.72


def test_one_matching_side_cannot_carry_a_fixture():
    """The bug this replaced: a long shared name dragged the whole-string score
    over the line while the OTHER team was someone else entirely."""
    from app.providers.bettingiscool import BettingIsCoolProvider as P
    board = ["Brondby vs FC Nordsjaelland", "Brommapojkarna vs Malmo"]
    assert P._best_match("Horsens v Nordsjaelland", board) is None
    assert P._best_match("Brommapojkarna v Hammarby", board) is None
    # ...but the right fixture still matches when it is present.
    board.append("AC Horsens vs FC Nordsjaelland")
    got = P._best_match("Horsens v Nordsjaelland", board)
    assert got and got[0] == "AC Horsens vs FC Nordsjaelland"


# --- The side is resolved from the TEAM, not from the whole pick string ---
#
# Live symptom: a coupon came back with selections showing no fair odds at all.
# An unresolved side is deliberately unpriced, so anything that stops a side
# resolving stops the leg pricing — and the side was being scored by a raw
# character ratio over the WHOLE pick. The market suffix is most of that
# string, so on a short club name it outweighed the name itself.

def test_short_team_names_still_resolve():
    """"Lyon Win and Over 2.5" is 4 characters of team and 16 of market.

    Scored whole, that is 0.33 against "Lyon" — under any bar that also keeps
    unrelated names out — so the leg went unpriced. Scored as a team fragment
    it is an exact match.
    """
    assert _side_of("Lyon Win and Over 2.5", "Lyon", "Paris Saint-Germain") is True
    assert _side_of("Ajax Win and Over 2.5", "Ajax", "Go Ahead Eagles") is True
    assert _side_of("Nice Win to Nil", "Nice", "Winterthur") is True


def test_side_resolution_uses_the_alias_table():
    """The same names fixture matching bridges must resolve a side.

    The fixture only matched BECAUSE the alias table canonicalises these two,
    so a side resolver that cannot see the aliases rejects the very fixture the
    matcher just accepted.
    """
    assert _side_of("Red Star Belgrade to Score 2", "Crvena Zvezda", "Larne") is True
    assert _side_of("Larne to Score 2", "Crvena Zvezda", "Larne") is False


def test_side_resolution_survives_shortening_and_word_order():
    assert _side_of("Paide to Score 1", "Paide Linnameeskond", "Flora Tallinn") is True
    assert _side_of("ETO Gyor Win and Over 1.5", "Gyori ETO", "Ferencvaros") is True


def test_reserve_side_does_not_steal_the_first_team():
    """team_similarity zeroes a squad-marker mismatch, so this must stay clean
    even though the two names are otherwise identical."""
    assert _side_of("Wisla Krakow to Score 1", "Wisla Krakow", "Wisla Krakow II") is True


def test_team_fragment_stops_at_the_market():
    from app.providers.bettingiscool import _team_fragment
    assert _team_fragment("Lyon Win and Over 2.5") == "Lyon"
    assert _team_fragment("Kairat Almaty to Score 1") == "Kairat Almaty"
    assert _team_fragment("Nice Win to Nil") == "Nice"
    assert _team_fragment("Hannover 96 Win and Over 2.5") == "Hannover 96"
    # Market words only — names no side, so it must stay unresolved.
    assert _team_fragment("Win and Over 2.5") == ""


def test_a_club_ending_in_a_number_is_not_read_as_a_totals_line():
    """"Hannover 96" contains "over 96".

    Without a word boundary the over-line regex matched that FIRST, so the leg
    was derived at a 96-goal line — probability zero, no price, and a leg that
    looked like a fixture the board did not carry.
    """
    from app.providers.bettingiscool import _OVER_RE
    m = _OVER_RE.search("hannover 96 win and over 2.5")
    assert m is not None and m.group(1) == "2.5"


def test_short_home_name_prices_end_to_end():
    """The regression as the user saw it: a leg with no fair odds."""
    fixture = {
        "event_id": 7, "league_name": "Ligue 1",
        "runner_home": "Lyon", "runner_away": "Paris Saint-Germain",
        "starts": "2026-08-22T19:00:00",
    }
    rows = [{
        "period": 0, "market": "moneyline", "line": None,
        "odds0": 3.60, "odds1": 2.40, "odds2": 2.80,
    }]
    p = _StubProvider(fixture, rows)
    (home,) = p.get_fair_odds([
        MatchQuery(match_id=1, match_name="Lyon v Paris Saint-Germain",
                   market="Lyon Win"),
    ])
    assert home.side is True
    assert home.fair_odds is not None
