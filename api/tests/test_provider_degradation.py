"""What the FALLBACK odds provider cannot price — and how loudly it says so.

The bug this covers, as it was reported: "Puntmore is not pricing up
selections". A Team to Score 2 coupon opened with 154 fixtures and every one of
them blank. Nothing was wrong with the scan, the coupon or the fixture matching.

TheOddsApi's free tier serves h2h + totals for the whole fixture and nothing
else. It has no team totals and no score grid, so a TTS coupon on it is 100%
unpriced BY CONSTRUCTION — and the deploy falls back to it silently whenever
BETTINGISCOOL_API_KEY is unset. Every symptom of that (blank rows) is identical
to the symptom of a bad photo, which is why it took a screenshot to find.

So two things are pinned here: the provider refuses what it cannot price
instead of approximating it, and the app says which markets those are.
"""
from app.providers.odds_api import (
    TheOddsApiProvider, _extract_fair_odds, unsupported_reason,
)
from app.providers.base import MatchQuery
from app.coupon_types import COUPON_TYPES


def _event(home="Kairat", away="Omonia Nicosia"):
    return {
        "home_team": home, "away_team": away,
        "bookmakers": [{"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": home, "price": 2.10},
                {"name": away, "price": 3.60},
                {"name": "Draw", "price": 3.40},
            ]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "point": 2.5, "price": 1.95},
                {"name": "Under", "point": 2.5, "price": 1.90},
            ]},
        ]}],
    }


# --- the reported bug ---

def test_team_to_score_cannot_be_priced_by_the_fallback():
    """The screenshot: every selection on a TTS coupon blank.

    Not a scan failure and not a matching failure — the vendor serves no team
    totals, so there is no price to be had. It must be reported as such.
    """
    for pick in ("Kairat to Score 1", "Kairat to Score 2", "Kairat to Score 3"):
        assert _extract_fair_odds(_event(), pick) is None
        assert unsupported_reason(pick) == "Team to Score"


def test_gg_markets_cannot_be_priced_by_the_fallback():
    for pick in ("GG Bonus", "GG No Draw"):
        assert _extract_fair_odds(_event(), pick) is None
        assert unsupported_reason(pick) is not None


def test_win_to_nil_is_refused_not_priced_as_a_straight_win():
    """The dangerous one — a WRONG price, not a missing one.

    "Win to Nil" contains "win", so it fell through to the moneyline branch and
    came back with the straight-win price on a materially shorter bet. That
    reads as enormous value and fires Discord alerts. An unpriced leg is
    visible and correctable; a confident wrong one is not.
    """
    straight_win = _extract_fair_odds(_event(), "Kairat Win")
    assert straight_win is not None
    assert _extract_fair_odds(_event(), "Kairat Win to Nil") is None
    assert unsupported_reason("Kairat Win to Nil") == "Win to Nil"


# --- the always-home bug, in the provider that still had it ---

def test_away_selection_is_not_priced_as_the_home_team():
    """The live provider was fixed for this; the fallback still hardcoded home.

    Home is usually the favourite, so pricing an away selection as its opponent
    INVENTS value rather than hiding it.
    """
    home = _extract_fair_odds(_event(), "Kairat Win")
    away = _extract_fair_odds(_event(), "Omonia Nicosia Win")
    assert home is not None and away is not None
    assert home != away
    assert home < away          # the favourite prices shorter


def test_win_and_over_reads_the_picked_side_too():
    home = _extract_fair_odds(_event(), "Kairat Win and Over 2.5")
    away = _extract_fair_odds(_event(), "Omonia Nicosia Win and Over 2.5")
    assert home is not None and away is not None
    assert home < away


def test_bare_win_names_no_side_so_it_is_refused():
    """Matches the live provider: an unresolved side is unpriced, never home."""
    assert _extract_fair_odds(_event(), "Win") is None


def test_unresolvable_side_is_refused():
    """A pick naming neither runner must not fall back to a side."""
    assert _extract_fair_odds(_event(), "Some Other Club Win") is None


# --- the refusal is reported, not swallowed ---

def test_refusals_are_logged_once_per_pass_with_the_count(caplog):
    """154 identical failures must not be 154 log lines — nor zero.

    The count and the market name are the facts that identify this as the
    fallback provider rather than a broken coupon.
    """
    class _StubClient:
        def fetch(self, *a, **k):
            return [_event()]

    provider = TheOddsApiProvider(_StubClient())
    with caplog.at_level("WARNING"):
        results = provider.get_fair_odds([
            MatchQuery(i, "Kairat vs Omonia Nicosia", "Kairat to Score 2")
            for i in range(10)
        ])

    assert all(r.fair_odds is None for r in results)
    refusals = [r for r in caplog.records if "cannot price" in r.getMessage()]
    assert len(refusals) == 1
    msg = refusals[0].getMessage()
    assert "Team to Score" in msg and "10 selection(s)" in msg
    # It must name the remedy — the whole point is that the cause is a missing
    # env var, which no other symptom points at.
    assert "BETTINGISCOOL_API_KEY" in msg


def test_every_unpriceable_coupon_type_is_nameable():
    """What /api/ready and the startup warning list.

    If a coupon type's own selection text cannot be priced by this provider,
    that type is 100% blank on it, and the app must be able to say so by name.
    """
    blank = [
        t.label for t in COUPON_TYPES
        if unsupported_reason(t.selection) is not None
    ]
    # The three TTS grids and the three GG-family coupons.
    assert "Team to Score 1" in blank
    assert "Team to Score 2" in blank
    assert "Win to Nil" in blank
    assert "GG Bonus" in blank
    assert "GG No Draw" in blank
    # Win-and-Over still prices (naively) — it is not blank, so it must not be
    # listed as such.
    assert "Win and Over 2.5" not in blank
