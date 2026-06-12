from .base import OddsProvider, MatchQuery, OddsResult


class MockOddsProvider(OddsProvider):
    """Test double. Keyed by (match_name, market) → fair_odds."""

    def __init__(self, fixture: dict[tuple[str, str], float] | None = None):
        self._fixture = fixture or {}

    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        results = []
        for q in queries:
            fair = self._fixture.get((q.match_name, q.market))
            results.append(OddsResult(match_id=q.match_id, fair_odds=fair))
        return results
