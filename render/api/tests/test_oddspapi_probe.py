"""The read-only OddsPapi probe: the catalogue by type, what a book prices in a
tournament with player props apart, and a listed raw endpoint. Hermetic."""
from fastapi.testclient import TestClient

from app.cards.oddspapi import OddsPapiClient, OddsPapiPaddySource
from app.cards.oddspapi_probe import book_summary, catalogue_summary, probe
from app.main import app

CATALOGUE = [
    {"marketId": 1, "marketName": "Over Under Full Time", "playerProp": False, "sportId": 10,
     "handicap": 2.5, "period": "fulltime", "marketType": "totals",
     "outcomes": [{"outcomeId": 1, "outcomeName": "Over"}, {"outcomeId": 2, "outcomeName": "Under"}]},
    {"marketId": 50, "marketName": "Player Shots On Target", "playerProp": True, "sportId": 10,
     "handicap": 0.5, "period": "fulltime", "marketType": "player-shots-on-target",
     "outcomes": [{"outcomeId": 50, "outcomeName": "Over"}, {"outcomeId": 51, "outcomeName": "Under"}]},
    {"marketId": 90, "marketName": "Player Points", "playerProp": True, "sportId": 11,
     "handicap": 20.5, "period": "fulltime", "marketType": "player-points", "outcomes": []},
]
FIXTURES = [{
    "fixtureId": "f1",
    "bookmakerOdds": {"pinnacle": {"markets": {
        "1": {"outcomes": {"1": {"players": {"0": {"price": 1.9, "active": True}}},
                           "2": {"players": {"0": {"price": 1.9, "active": True}}}}},
        "50": {"outcomes": {"50": {"players": {"77": {"price": 2.1, "active": True},
                                               "78": {"price": 3.0, "active": True}}},
                            "51": {"players": {"77": {"price": 1.7, "active": False}}}}},
        "999": {"outcomes": {}},
    }}},
}, {"fixtureId": "f2", "bookmakerOdds": {}}]


class Client:
    requests_today = 0

    def __init__(self):
        self.calls = []

    def get(self, path, **params):
        self.calls.append((path, params))
        return [{"tournamentId": 132, "tournamentName": "NBA"},
                {"tournamentId": 17, "tournamentName": "Premier League"}]

    def catalogue(self):
        return CATALOGUE

    def resolve_book(self, wanted):
        return wanted + "+30"

    def odds_by_tournaments(self, ids, bookmaker):
        self.calls.append(("odds", ids, bookmaker))
        return FIXTURES


def test_the_catalogue_groups_player_props_by_sport_and_type():
    out = catalogue_summary(CATALOGUE, 10, True)
    assert out["entries_by_sport"] == {"10": 2, "11": 1}
    assert [(g["type"], g["lines"], g["outcomes"]) for g in out["groups"]] == [
        ("player-shots-on-target", [0.5], ["Over", "Under"])]
    assert len(catalogue_summary(CATALOGUE, None, False)["groups"]) == 3


def test_a_book_summary_counts_players_and_sides_under_a_variant_slug():
    out = book_summary(FIXTURES, CATALOGUE, "pinnacle+30")
    assert (out["fixtures_listed"], out["fixtures_with_prices"]) == (2, 1)
    assert out["markets_not_in_catalogue"] == 1
    props, totals = out["market_types"]
    assert (props["type"], props["player_prop"], props["players"]) == (
        "player-shots-on-target", True, 2)
    # Over is priced; Under's one player is suspended.
    assert props["sides_priced"] == {"1": 1}
    assert props["sample"]["marketId"] == "50"
    assert (totals["type"], totals["players"], totals["sides_priced"]) == ("totals", 0, {"2": 1})


def test_probe_reads_tournaments_a_book_and_only_listed_raw_paths():
    c = Client()
    assert probe(c, "tournaments", sport=11, find="nba")["rows"] == [
        {"tournamentId": 132, "tournamentName": "NBA"}]
    assert probe(c, "tournaments")["error"]
    out = probe(c, "book", book="pinnacle", tournament="17,8")
    assert out["tournaments"] == [17, 8] and ("odds", [17, 8], "pinnacle+30") in c.calls
    assert probe(c, "book")["error"]
    assert "raw reads one of" in probe(c, "raw", path="/account")["error"]
    assert probe(c, "raw", path="/players", params="fixtureId=f1", limit=1)["rows"] == 2
    assert c.calls[-1] == ("/players", {"fixtureId": "f1"})
    assert probe(c, "nope")["error"]


def test_the_route_says_when_oddspapi_is_not_configured_and_reports_errors():
    old = getattr(app.state, "cards_client", None)
    try:
        app.state.cards_client = None
        assert "not configured" in TestClient(app).get("/paddy/oddspapi-probe").json()["error"]
        src = OddsPapiPaddySource(OddsPapiClient("x"))
        src._client.catalogue = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        app.state.cards_client = src
        assert TestClient(app).get("/paddy/oddspapi-probe").json() == {
            "error": "boom", "requests_today": 0}
    finally:
        app.state.cards_client = old
