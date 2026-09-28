"""Why a coupon selection is blank — and how loudly the app says so.

The bug this came from, as it was reported: "Puntmore is not pricing up
selections". A Team to Score 2 coupon opened with 154 fixtures and every one
blank, which looked exactly like a bad photo. The live provider now tallies
WHY each leg is blank (no board, no match, no odds, no market, no side), and
a deploy with no provider says so at /api/ready. (The TheOddsApi fallback that
caused that report was removed in Sept 2026.)
"""
from app.providers.bettingiscool import BettingIsCoolProvider
from app.providers.base import MatchQuery


# --- the LIVE provider says why a leg is blank, instead of just blanking it ---

class _BoardProvider(BettingIsCoolProvider):
    """bettingiscool with the network stubbed."""

    def __init__(self, board, odds_rows):
        super().__init__("test-key")
        self._board = board
        self._rows = odds_rows

    def _fixtures(self):
        return self._board

    def _odds(self, event_id):
        return self._rows


def _fixture(event_id=1, home="Kairat", away="Omonia Nicosia"):
    return {
        "event_id": event_id, "league_name": "Champions League",
        "runner_home": home, "runner_away": away,
        "starts": "2026-08-22T19:00:00",
    }


def _team_totals(line=1.5):
    return [
        {"period": 0, "market": "home_totals", "line": line,
         "odds0": None, "odds1": 1.90, "odds2": 1.95},
        {"period": 0, "market": "away_totals", "line": line,
         "odds0": None, "odds1": 2.40, "odds2": 1.60},
    ]


def _tts_queries(n=3):
    return [
        MatchQuery(i, "Kairat v Omonia Nicosia", "Kairat to Score 2")
        for i in range(n)
    ]


def test_empty_board_is_reported_as_such_not_as_no_match():
    """The quietest failure there was.

    Every slice answers 200 with nothing, so no request fails and nothing is
    logged — while every leg matches against an empty list and the whole coupon
    comes back blank, looking exactly like a bad scan.
    """
    p = _BoardProvider([], _team_totals())
    results = p.get_fair_odds(_tts_queries())
    assert all(r.fair_odds is None for r in results)
    assert p.last_pass["no_board"] == 3
    assert p.last_pass["no_match"] == 0      # NOT blamed on the fixture names
    assert p.last_pass["board"] == 0


def test_full_board_but_nothing_matches_is_no_match():
    p = _BoardProvider([_fixture(home="Some Club", away="Another Club")],
                       _team_totals())
    p.get_fair_odds(_tts_queries())
    assert p.last_pass["no_match"] == 3
    assert p.last_pass["no_board"] == 0
    assert p.last_pass["board"] == 1


def test_matched_but_market_not_posted_is_no_market():
    """Pinnacle posts the lines it chooses to.

    A TTS2 coupon needs team totals at 1.5; a board that only carries 0.5 is
    the vendor declining to price the market, not a broken coupon.
    """
    p = _BoardProvider([_fixture()], _team_totals(line=0.5))
    p.get_fair_odds(_tts_queries())
    assert p.last_pass["no_market"] == 3
    assert p.last_pass["no_match"] == 0


def test_everything_priced_is_reported_as_priced():
    p = _BoardProvider([_fixture()], _team_totals(line=1.5))
    results = p.get_fair_odds(_tts_queries())
    assert all(r.fair_odds is not None for r in results)
    assert p.last_pass["priced"] == 3
    assert p.last_pass["legs"] == 3


def test_unresolvable_side_is_reported_separately_from_a_missing_market():
    """A pick naming neither runner, on a market that IS priced."""
    p = _BoardProvider([_fixture()], _team_totals(line=1.5))
    p.get_fair_odds([
        MatchQuery(1, "Kairat v Omonia Nicosia", "Someone Else to Score 2"),
    ])
    assert p.last_pass["no_side"] == 1
    assert p.last_pass["no_market"] == 0


def test_the_pass_is_logged_once_and_names_the_counts(caplog):
    p = _BoardProvider([], _team_totals())
    with caplog.at_level("WARNING"):
        p.get_fair_odds(_tts_queries(10))
    passes = [r for r in caplog.records if "pricing pass" in r.getMessage()]
    assert len(passes) == 1
    assert "0/10 priced" in passes[0].getMessage()
