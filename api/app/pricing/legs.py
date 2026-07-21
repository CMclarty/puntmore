"""Price a set of legs and (re)compute their sanity flags.

One implementation shared by the upload path, the on-demand coupon refresh and
the 10-minute scheduler loop, so the three can't drift on what "priced" means.
All of them read through the odds cache — this never adds vendor calls.
"""
from .. import checks
from ..comparison import compute_value
from ..models import Leg
from ..providers.base import MatchQuery
from ..time_utils import utcnow


def price_legs(legs: list[Leg], odds_provider) -> list[Leg]:
    """Update fair odds, value and flags in place. Returns the legs that priced.

    Legs the provider can't price keep their previous fair odds rather than
    being blanked — a transient miss shouldn't erase a good price. They are
    still re-flagged, so an unmatched row shows its ⚠ either way.
    """
    if not legs:
        return []

    queries = [
        MatchQuery(match_id=leg.id, match_name=leg.match_name, market=leg.pick)
        for leg in legs
    ]
    results = odds_provider.get_fair_odds(queries)
    result_map = {r.match_id: r for r in results}
    now = utcnow()

    priced = []
    for leg in legs:
        result = result_map.get(leg.id)
        if result and result.fair_odds is not None:
            leg.fair_odds = result.fair_odds
            leg.value = compute_value(leg.odds, result.fair_odds)
            leg.fair_odds_updated_at = now
            priced.append(leg)
        if result:
            leg.matched_fixture = result.matched_fixture
            leg.match_score = result.match_score
            if result.kickoff is not None:
                leg.kickoff = result.kickoff
            leg.fair_odds_home = result.fair_odds_home
            leg.fair_odds_away = result.fair_odds_away
            leg.side = (
                None if result.side is None else ("home" if result.side else "away")
            )

        leg.flags = checks.encode_flags(checks.evaluate_leg(
            leg.pick,
            leg.odds,
            value=leg.value,
            matched_fixture=leg.matched_fixture,
            match_score=leg.match_score,
        ))

    return priced
