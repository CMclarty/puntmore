from .base import OddsProvider, MatchQuery, OddsResult


class MockOddsProvider(OddsProvider):
    """Test double. Keyed by (match_name, market) → fair_odds."""

    def __init__(
        self,
        fixture: dict[tuple[str, str], float] | None = None,
        matched: dict[str, tuple[str, float]] | None = None,
    ):
        self._fixture = fixture or {}
        # match_name -> (fixture name, confidence). Absent means no match, which
        # is what drives the no_match leg flag.
        self._matched = matched or {}

    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        results = []
        for q in queries:
            fair = self._fixture.get((q.match_name, q.market))
            fixture, score = self._matched.get(q.match_name, (None, None))
            results.append(OddsResult(
                match_id=q.match_id,
                fair_odds=fair,
                matched_fixture=fixture,
                match_score=score,
            ))
        return results
