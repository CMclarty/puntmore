"""Props, the service and routes: the feed and diagnostics off the Paddy
source's boards, names from /players, the grant and the flag. Hermetic."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.props import service
from app.security import user_permissions

NOW = datetime.now(timezone.utc)

CATALOGUE = [
    {"marketId": 100, "marketType": "players-shotsongoal", "period": "result", "handicap": 0,
     "playerProp": True, "sportId": 10,
     "outcomes": [{"outcomeId": 1000, "outcomeName": "0"}, {"outcomeId": 1002, "outcomeName": "1+"},
                  {"outcomeId": 1003, "outcomeName": "2+"}]},
    {"marketId": 130, "marketType": "players-anytimegoalscorer", "period": "fulltime",
     "handicap": 0, "playerProp": True, "sportId": 10,
     "outcomes": [{"outcomeId": 1300, "outcomeName": "Yes"}, {"outcomeId": 1301, "outcomeName": "No"}]},
    {"marketId": 200, "marketType": "playertotals-shotsongoal", "period": "result",
     "handicap": 1.5, "playerProp": True, "sportId": 10,
     "outcomes": [{"outcomeId": 2000, "outcomeName": "Over"}, {"outcomeId": 2001, "outcomeName": "Under"}]},
    {"marketId": 1, "marketType": "1x2", "period": "fulltime", "playerProp": False, "sportId": 10,
     "outcomes": [{"outcomeId": 1, "outcomeName": "1"}]},
]

# Haaland (7): Paddy 2+ SoT at 3.6 and to score at 2.2; Saka (8): to score at 4.0.
PADDY = {"markets": {
    "100": {"outcomes": {"1003": {"players": {"7": {"price": 3.6, "active": True}}}}},
    "130": {"outcomes": {"1300": {"players": {"7": {"price": 2.2, "active": True},
                                              "8": {"price": 4.0, "active": True}}}}},
    "1": {"outcomes": {"1": {"players": {"0": {"price": 1.5, "active": True}}}}},
}}
PINNACLE = {"markets": {
    "200": {"outcomes": {"2000": {"players": {"7": {"price": 3.0, "active": True}}},
                         "2001": {"players": {"7": {"price": 1.38, "active": True}}}}},
    "130": {"outcomes": {"1300": {"players": {"7": {"price": 1.9, "active": True},
                                              "8": {"price": 3.0, "active": True}}},
                         "1301": {"players": {"7": {"price": 1.95, "active": True},
                                              "8": {"price": 1.4, "active": True}}}}},
}}
PLAYERS = [{"participantId": 17, "players": [{"playerId": 7, "playerName": "Haaland, Erling"}]},
           {"participantId": 42, "players": [{"playerId": 8, "playerName": "Saka, Bukayo"}]}]


class Client:
    requests_today = 0

    def __init__(self, players=PLAYERS, fair_slug="pinnacle+30"):
        self._players = players
        self._fair_slug = fair_slug
        self.player_calls = 0

    def catalogue(self):
        return CATALOGUE

    def cached_catalogue(self):
        return CATALOGUE

    resolves = 0

    def resolve_book(self, wanted):
        # The sweep resolves the fair book (/account); a props read never does.
        self.resolves += 1
        return self._fair_slug

    def players(self):
        self.player_calls += 1
        if isinstance(self._players, Exception):
            raise self._players
        return self._players


class Source:
    """What props reads off `OddsPapiPaddySource` after a sweep."""
    book = priced_book = "paddypower"

    def __init__(self, pinnacle=PINNACLE, pinnacle_key="pinnacle+30", client=None):
        self._client = client or Client()
        self.sweeps = 0
        self.priced_at = NOW - timedelta(minutes=3)
        self.last_sweep = {}
        self.boards = {}
        self.fixtures = []
        self._pinnacle, self._key = pinnacle, pinnacle_key

    def get_events(self):
        # The sweep fills the boards: never read before one has run.
        self.sweeps += 1
        books = {"paddypower": PADDY}
        if self._pinnacle is not None:
            books[self._key] = self._pinnacle
        self.boards = {
            "f1": {"fixtureId": "f1", "bookmakerOdds": books},
            "f2": {"fixtureId": "f2", "bookmakerOdds": books},
            "f0": {"fixtureId": "f0", "bookmakerOdds": books},
        }
        self.fixtures = [
            {"id": "f2", "home": "Man City", "away": "Arsenal", "start": NOW + timedelta(hours=5)},
            {"id": "f1", "home": "Chelsea", "away": "Spurs", "start": NOW + timedelta(hours=2)},
            # Listed at the sweep but kicked off since.
            {"id": "f0", "home": "Leeds", "away": "Hull", "start": NOW - timedelta(minutes=1)},
        ]
        fair = self._client._fair_slug
        self.last_sweep = {"source": "oddspapi",
                           "fair_source": f"oddspapi:{fair}" if fair else None,
                           "errors": [], "failed": 0}
        return []


@pytest.fixture(autouse=True)
def clean():
    old = getattr(app.state, "cards_client", None)
    service._NAMES_FAILED.clear()
    service._NAMES_PARSED.clear()
    yield
    app.state.cards_client = old
    service._NAMES_FAILED.clear()
    service._NAMES_PARSED.clear()


def get(path, source):
    app.state.cards_client = source
    return TestClient(app).get(path)


def test_the_feed_prices_both_shapes_soonest_first_best_value_first():
    src = Source()
    r = get("/props/feed", src)
    assert r.status_code == 200 and src.sweeps == 1
    items = r.json()
    assert [i["event_id"] for i in items] == ["f1", "f2"]          # f0 has kicked off
    first = items[0]
    assert set(first) == {"event_id", "match", "home", "away", "commence_time", "priced_at",
                          "best_value", "bets"}
    assert (first["match"], first["home"], first["away"]) == ("Chelsea vs Spurs", "Chelsea", "Spurs")
    assert first["commence_time"].endswith("Z") and first["priced_at"]
    bets = first["bets"]
    assert sorted(b["pick"] for b in bets) == [
        "Bukayo Saka to score", "Erling Haaland 2+ shots on target", "Erling Haaland to score"]
    assert [b["value"] for b in bets] == sorted((b["value"] for b in bets), reverse=True)
    assert first["best_value"] == bets[0]["value"]
    assert set(bets[0]) == {"player", "player_id", "stat", "n", "pick", "odds", "fair", "value",
                            "pinnacle_margin"}
    sot = next(b for b in bets if b["stat"] == "shotsongoal")
    assert (sot["player"], sot["player_id"], sot["n"], sot["odds"]) == ("Erling Haaland", "7", 2, 3.6)
    assert sot["fair"] < 3.6 and sot["pinnacle_margin"] > 0
    assert sot["value"] == pytest.approx(3.6 / sot["fair"] - 1, abs=1e-4)


def test_min_edge_cuts_the_list_and_is_bounded():
    src = Source()
    all_bets = [b for i in get("/props/feed", src).json() for b in i["bets"]]
    values = sorted({b["value"] for b in all_bets})
    floor = (values[0] + values[1]) / 2           # between the two lowest values
    kept = [b for i in get(f"/props/feed?min_edge={floor}", src).json() for b in i["bets"]]
    assert len(kept) == len([b for b in all_bets if b["value"] >= floor]) < len(all_bets)
    assert get("/props/feed?min_edge=0.6", src).status_code == 422


def test_names_come_flat_too_and_a_names_failure_leaves_those_players_out():
    flat = Source(client=Client(players=[{"playerId": "7", "playerName": "Haaland, Erling"}]))
    picks = {b["pick"] for i in get("/props/feed?min_edge=-0.5", flat).json() for b in i["bets"]}
    assert "Erling Haaland to score" in picks and not any("Saka" in p for p in picks)

    broken = Source(client=Client(players=RuntimeError("OddsPapi /players: HTTP 500")))
    assert get("/props/feed?min_edge=-0.5", broken).status_code == 200
    diag = get("/props/diagnostics", broken).json()
    assert diag["totals"]["unnamed"] == 6 and diag["totals"]["matched"] == 0
    assert "HTTP 500" in diag["names"]["error"]
    # The verdict blames the names request, not Pinnacle's lines.
    assert diag["verdict"].startswith("The player names request failed")
    assert "HTTP 500" in diag["verdict"] and "Pinnacle" not in diag["verdict"]
    # The failure is held: not re-sent on the next read (each attempt is billed).
    assert broken._client.player_calls == 1


def test_an_empty_pinnacle_side_says_so_in_one_request_and_asks_no_names():
    src = Source(pinnacle=None)
    assert get("/props/feed", src).json() == []
    diag = get("/props/diagnostics", src).json()
    assert diag["verdict"].startswith("Pinnacle is posting no player markets")
    assert diag["totals"]["paddy"] == 6 and diag["totals"]["no_fair_player"] == 6
    assert diag["stats"]["goals"]["pinnacle_two_sided"] == 0
    assert diag["player_markets_posted"] == {"paddy": 4, "pinnacle": 0}
    assert src._client.player_calls == 0 and diag["names"]["fetched"] is False


def test_the_diagnostics_count_per_stat_and_give_a_verdict():
    diag = get("/props/diagnostics", Source()).json()
    assert diag["books"] == {"paddy": "paddypower", "fair": "pinnacle+30"}
    assert diag["fixtures_to_kick_off"] == 2
    assert diag["stats"]["shotsongoal"]["matched"] == 2
    assert diag["stats"]["goals"] == {
        "paddy": 4, "pinnacle_two_sided": 4, "matched": 4, "no_fair_player": 0,
        "no_fair_line": 0, "fair_one_sided": 0, "unnamed": 0, "fair_crossed": 0,
        "duplicates_paddy": 0, "duplicates_pinnacle": 0}
    assert diag["verdict"].startswith("6 of 6 Paddy selection(s) priced against Pinnacle")


@pytest.mark.parametrize("granted,key", [("pinnacle+30", "pinnacle"), ("pinnacle", "pinnacle+30"),
                                         ("pinnacle+30", "pinnacle+30")])
def test_the_fair_book_is_read_under_its_variant_slug_either_way(granted, key):
    src = Source(pinnacle_key=key, client=Client(fair_slug=granted))
    assert sum(len(i["bets"]) for i in get("/props/feed?min_edge=-0.5", src).json()) == 6
    # The slug is the one the sweep resolved: a read sends no /account.
    diag = get("/props/diagnostics", src).json()
    assert diag["books"]["fair"] == granted and src._client.resolves == 0


def test_no_fair_book_on_the_sweep_means_no_fair_side():
    src = Source(client=Client(fair_slug=None))
    assert get("/props/feed", src).json() == []
    diag = get("/props/diagnostics", src).json()
    assert diag["books"]["fair"] is None and diag["verdict"].startswith("No fair book")
    assert src._client.resolves == 0


def test_the_grant_guards_both_routes():
    src = Source()
    app.dependency_overrides[user_permissions] = lambda: "cards+hills"
    try:
        assert get("/props/feed", src).status_code == 403
        assert get("/props/diagnostics", src).status_code == 403
        app.dependency_overrides[user_permissions] = lambda: "props"
        assert get("/props/feed", src).status_code == 200
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


def test_no_oddspapi_configured_answers_cleanly():
    r = get("/props/feed", None)
    assert r.status_code == 200 and r.json() == []
    diag = get("/props/diagnostics", None)
    assert diag.status_code == 200 and "ODDSPAPI_API_KEY" in diag.json()["verdict"]


def test_props_is_a_grant_a_tool_on_me_and_off_with_its_flag(monkeypatch):
    from app import site
    from app.config import settings
    from app.security import ALL_TOOLS, enabled_tools, parse_permissions
    assert "props" in ALL_TOOLS and "props" in enabled_tools() and "props" in site.ME_TOOLS
    assert "props" in parse_permissions("*")
    monkeypatch.setattr(settings, "enable_props", False)
    assert "props" not in enabled_tools()


def test_player_names_are_read_tolerantly():
    assert service.player_names(PLAYERS) == {"7": "Erling Haaland", "8": "Bukayo Saka"}
    assert service.player_names({"data": [{"id": 9, "name": "Rice, Declan"}]}) == {
        "9": "Declan Rice"}
    assert service.player_names({"players": {"5": {"playerName": "Rodri"}}}) == {"5": "Rodri"}
    assert service.player_names(None) == {}


def test_a_failing_catalogue_is_never_re_sent_by_props(monkeypatch):
    """Props reads only the catalogue a sweep already fetched: three polls
    after the sweep's /markets failed send nothing more."""
    from app.cards.oddspapi import OddsPapiClient, OddsPapiPaddySource
    client = OddsPapiClient("x")
    sent = []

    def send(path, **params):
        sent.append(path)
        raise RuntimeError(f"OddsPapi {path}: HTTP 500")

    monkeypatch.setattr(client, "get", send)
    monkeypatch.setattr(client, "resolve_book", lambda wanted: wanted)
    src = OddsPapiPaddySource(client)
    src.get_events()                          # the scheduler's sweep: fails on /markets
    before = list(sent)
    assert "/markets" in before and src.last_sweep.get("error")
    for _ in range(3):
        assert get("/props/feed", src).json() == []
    diag = get("/props/diagnostics", src).json()
    assert sent == before                     # props sent nothing at all
    assert "catalogue" in diag["verdict"] or "sweep failed" in diag["verdict"]
    assert diag["catalogue"]["read"] is False


def test_with_no_catalogue_read_yet_the_feed_is_empty_and_says_so():
    class NoCatalogue(Client):
        def catalogue(self):
            raise AssertionError("props must not fetch the catalogue")

        def cached_catalogue(self):
            return None

    src = Source(client=NoCatalogue())
    assert get("/props/feed", src).json() == []
    diag = get("/props/diagnostics", src).json()
    assert diag["verdict"].startswith("The market catalogue has not been read yet")
    assert diag["catalogue"] == {"read": False, "markets_read": 0}


def test_names_on_the_prices_send_no_players_request():
    named = {"markets": {
        "130": {"outcomes": {"1300": {"players": {
            "7": {"price": 2.2, "active": True, "playerName": "Haaland, Erling"}}}}},
    }}

    class NamedSource(Source):
        def get_events(self):
            super().get_events()
            for fx in self.boards.values():
                fx["bookmakerOdds"] = {**fx["bookmakerOdds"], "paddypower": named}

    src = NamedSource()
    picks = [b["pick"] for i in get("/props/feed", src).json() for b in i["bets"]]
    assert picks == ["Erling Haaland to score", "Erling Haaland to score"]
    assert src._client.player_calls == 0


def test_an_unauthenticated_request_is_401():
    from app.security import require_user
    app.dependency_overrides.pop(user_permissions, None)
    app.dependency_overrides.pop(require_user, None)
    try:
        assert get("/props/feed", Source()).status_code == 401
        assert get("/props/diagnostics", Source()).status_code == 401
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
        app.dependency_overrides[require_user] = lambda: "testuser"


def test_a_failed_sweep_lists_nothing_until_a_good_one(monkeypatch):
    """The source keeps the last good boards after a failed sweep (Hills' bet
    log reads them); props must not list them as current."""
    from app.cards.oddspapi import OddsPapiClient, OddsPapiPaddySource
    from app.config import settings
    monkeypatch.setattr(settings, "oddspapi_tournament_ids", "17")
    monkeypatch.setattr(settings, "oddspapi_fair_book", "pinnacle")
    client = OddsPapiClient("x", ttl=0)
    start = (NOW + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    books = {"paddypower": PADDY, "pinnacle+30": PINNACLE}
    state = {"names_fail": False}
    resolves = []

    def send(path, **params):
        if path == "/markets":
            return CATALOGUE
        if path == "/participants":
            if state["names_fail"]:
                raise RuntimeError("OddsPapi /participants: HTTP 503")
            return []
        if path == "/odds-by-tournaments":
            slug = params["bookmakers"]
            return [{"fixtureId": "f1", "participant1Name": "Chelsea", "participant2Name": "Spurs",
                     "startTime": start, "tournamentId": 17,
                     "bookmakerOdds": {slug: books[slug]}}]
        raise AssertionError(path)

    def resolve(wanted):
        resolves.append(wanted)
        return "pinnacle+30" if wanted == "pinnacle" else wanted

    monkeypatch.setattr(client, "get", send)
    monkeypatch.setattr(client, "players", lambda: PLAYERS)
    monkeypatch.setattr(client, "resolve_book", resolve)
    src = OddsPapiPaddySource(client)

    src.get_events()                                  # a good sweep
    assert len(get("/props/feed", src).json()) == 1

    client._ref.clear()                               # the day's reference data lapses ...
    state["names_fail"] = True
    src.get_events()                                  # ... the catalogue comes back, the
                                                      # team names fail, the sweep fails
    assert client.cached_catalogue() is not None
    assert src.boards and src.last_sweep.get("error")  # the old board is kept for Hills
    assert get("/props/feed", src).json() == []
    diag = get("/props/diagnostics", src).json()
    assert diag["verdict"].startswith("The last Paddy sweep failed")
    assert "HTTP 503" in diag["verdict"]

    state["names_fail"] = False
    src.get_events()                                  # a good sweep again
    assert len(get("/props/feed", src).json()) == 1
    # Only the sweeps resolved the fair book; props reads asked nothing.
    assert len(resolves) == 2
