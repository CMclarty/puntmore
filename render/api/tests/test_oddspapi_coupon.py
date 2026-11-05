"""OddsPapi coupon provider: the catalogue normaliser + end-to-end grid pricing
off a bulk board, via the provider-agnostic GridCouponProvider."""
from app.cards.oddspapi import coupon_markets, SOCCER
from app.providers.base import MatchQuery
from app.providers.oddspapi_coupon import OddsPapiCouponProvider


def _mkt(mid, mtype, line, outs):
    return {"marketId": mid, "marketType": mtype, "sportId": SOCCER, "period": "fulltime",
            "handicap": line, "outcomes": [{"outcomeId": o, "outcomeName": n} for o, n in outs]}


def test_coupon_markets_maps_1x2_totals_and_team_totals():
    cat = [
        _mkt("m", "1x2", 0.0, [("1", "1"), ("2", "X"), ("3", "2")]),
        _mkt("t", "totals", 2.5, [("o", "Over"), ("u", "Under")]),
        _mkt("h", "teamtotals-team1", 1.5, [("o", "Over"), ("u", "Under")]),
        _mkt("a", "teamtotals-team2", 1.0, [("o", "Over"), ("u", "Under")]),
        _mkt("x", "spreads", -1.5, [("o", "Home"), ("u", "Away")]),   # ignored
    ]
    cm = coupon_markets(cat)
    assert cm["m"] == ("moneyline", 0.0, {"1": 1, "2": 0, "3": 2})   # 1=home X=draw 2=away
    assert cm["t"] == ("totals", 2.5, {"o": 1, "u": 0})              # over=1 under=0
    assert cm["h"][0] == "home_totals" and cm["a"][0] == "away_totals"
    assert "x" not in cm                                              # spreads not a grid input


def _price(p):
    return {"players": {"0": {"price": p, "active": True}}}


class _FakeClient:
    """Minimal OddsPapiClient: a fixed catalogue + one fixture's Pinnacle board."""
    def __init__(self, cat, board):
        self._cat, self._board = cat, board

    def catalogue(self): return self._cat
    def participants(self): return []
    def tournaments(self): return []
    def resolve_book(self, wanted): return "pinnacle"
    def odds_by_tournaments(self, ids, bookmaker="pinnacle"): return self._board


def test_provider_prices_gg_off_a_fake_board(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "oddspapi_tournament_ids", "17")   # pure-digit → no resolve needed

    cat = [
        _mkt("m", "1x2", 0.0, [("1", "1"), ("2", "X"), ("3", "2")]),
        _mkt("t15", "totals", 1.5, [("o", "Over"), ("u", "Under")]),
        _mkt("t25", "totals", 2.5, [("o", "Over"), ("u", "Under")]),
        _mkt("t35", "totals", 3.5, [("o", "Over"), ("u", "Under")]),
    ]
    markets = {
        "m":   {"outcomes": {"1": _price(1.80), "2": _price(3.60), "3": _price(4.50)}},
        "t15": {"outcomes": {"o": _price(1.30), "u": _price(3.60)}},
        "t25": {"outcomes": {"o": _price(2.00), "u": _price(1.85)}},
        "t35": {"outcomes": {"o": _price(3.50), "u": _price(1.32)}},
    }
    board = [{
        "fixtureId": "f1", "tournamentId": 17,
        "participant1Name": "Alpha FC", "participant2Name": "Beta FC",
        "startTime": "2026-10-24T19:00:00.000Z",
        "bookmakerOdds": {"pinnacle": {"markets": markets}},
    }]

    p = OddsPapiCouponProvider(_FakeClient(cat, board))
    r = p.get_fair_odds([MatchQuery(1, "Alpha v Beta", "GG", None)])[0]
    assert r.matched_fixture == "Alpha FC vs Beta FC"
    assert r.fair_odds is not None and 1.1 < r.fair_odds < 6.0   # a sane derived GG price
    assert p.last_pass["board"] == 1 and p.last_pass["priced"] == 1
