"""Paddy Power corners/cards from OddsPapi — see cards/oddspapi.py.

Shapes here are the ones the trial responses showed (market id = one market at
one line; odds under bookmakerOdds.<book>.markets.<id>.outcomes.<id>.players."0";
fixtures carry participant ids). The catalogue field names are unverified, so
these tests pin the TOLERANCE as much as any one spelling.
"""
from datetime import datetime, timedelta, timezone

from app.cards import oddspapi as op

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _iso(hours):
    return (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


CATALOGUE = [
    {"marketId": 10803, "marketName": "Total Corners", "handicap": 9.5, "sportId": 10,
     "period": "fulltime", "outcomes": [{"outcomeId": 10803, "outcomeName": "Over"},
                                        {"outcomeId": 10804, "outcomeName": "Under"}]},
    {"marketId": 10807, "marketName": "Total Corners", "handicap": 10.5, "sportId": 10,
     "outcomes": [{"outcomeId": 10807, "outcomeName": "Over"},
                  {"outcomeId": 10808, "outcomeName": "Under"}]},
    {"marketId": 20001, "marketName": "Total Bookings Over/Under 4.5", "sportId": 10,
     "outcomes": [{"outcomeId": 1, "outcomeName": "Over 4.5"},
                  {"outcomeId": 2, "outcomeName": "Under 4.5"}]},
    # Not match totals: must NOT be taken.
    {"marketId": 30001, "marketName": "1st Half Corners", "handicap": 4.5,
     "outcomes": [{"outcomeId": 3, "outcomeName": "Over"}, {"outcomeId": 4, "outcomeName": "Under"}]},
    {"marketId": 30002, "marketName": "Home Team Corners", "handicap": 5.5,
     "outcomes": [{"outcomeId": 5, "outcomeName": "Over"}, {"outcomeId": 6, "outcomeName": "Under"}]},
    {"marketId": 30003, "marketName": "Corners Handicap", "handicap": -1.5,
     "outcomes": [{"outcomeId": 7, "outcomeName": "Over"}, {"outcomeId": 8, "outcomeName": "Under"}]},
    {"marketId": 1010, "marketName": "Over Under", "handicap": 2.5,
     "outcomes": [{"outcomeId": 1010, "outcomeName": "Over"}, {"outcomeId": 1011, "outcomeName": "Under"}]},
]


def _p(price, active=True):
    return {"players": {"0": {"price": price, "active": active}}}


def _fixture(hours=20, markets=None, fid="id1", t=17):
    return {
        "fixtureId": fid, "participant1Id": 17, "participant2Id": 38, "tournamentId": t,
        "startTime": _iso(hours),
        "bookmakerOdds": {"paddypower": {"markets": markets if markets is not None else {
            "10803": {"outcomes": {"10803": _p(2.55), "10804": _p(1.45)}},
            "10807": {"outcomes": {"10807": _p(3.60), "10808": _p(1.25)}},
            "20001": {"outcomes": {"1": _p(1.90), "2": _p(1.85)}},
            "1010": {"outcomes": {"1010": _p(1.9), "1011": _p(1.9)}},
        }}},
    }


NAMES = {"17": "Manchester City", "38": "Chelsea"}


def test_the_catalogue_yields_only_full_match_corner_and_card_totals():
    m = op.corner_card_markets(CATALOGUE)
    assert set(m) == {"10803", "10807", "20001"}
    assert m["10803"] == (op.CORNERS, 9.5, {"10803": "Over", "10804": "Under"})
    assert m["20001"][0] == op.CARDS and m["20001"][1] == 4.5   # line read from the name


def test_the_catalogue_is_read_whatever_it_is_wrapped_in():
    wrapped = {"data": CATALOGUE}
    assert set(op.corner_card_markets(wrapped)) == {"10803", "10807", "20001"}
    by_id = {str(m["marketId"]): {k: v for k, v in m.items() if k != "marketId"} for m in CATALOGUE}
    assert set(op.corner_card_markets(by_id)) == {"10803", "10807", "20001"}


def test_fixtures_become_theoddsapi_shaped_events_with_paddys_lines():
    events, counts = op.to_events([_fixture()], op.corner_card_markets(CATALOGUE), NAMES,
                                  now=NOW, window_hours=48)
    assert counts["carried_paddy_prices"] == 1
    e = events[0]
    assert (e["home_team"], e["away_team"], e["sport_key"]) == ("Manchester City", "Chelsea", "soccer_epl")
    book = e["bookmakers"][0]
    assert book["key"] == "paddypower"
    corners = next(m for m in book["markets"] if m["key"] == op.CORNERS)["outcomes"]
    assert {"name": "Over", "point": 9.5, "price": 2.55} in corners
    assert {"name": "Under", "point": 10.5, "price": 1.25} in corners
    assert all(m["key"] != "1010" for m in book["markets"])     # goals ignored


def test_the_pricing_pipeline_reads_those_events_unchanged():
    from app.cards.laddered import _lines
    events, _ = op.to_events([_fixture()], op.corner_card_markets(CATALOGUE), NAMES,
                             now=NOW, window_hours=48)
    corners = next(m for m in events[0]["bookmakers"][0]["markets"] if m["key"] == op.CORNERS)
    assert _lines(corners["outcomes"]) == {9.5: {"Over": 2.55, "Under": 1.45},
                                           10.5: {"Over": 3.60, "Under": 1.25}}


def test_suspended_prices_and_out_of_window_fixtures_are_dropped():
    m = op.corner_card_markets(CATALOGUE)
    susp = _fixture(markets={"10803": {"outcomes": {"10803": _p(2.55, active=False),
                                                     "10804": _p(1.45)}}})
    events, _ = op.to_events([susp], m, NAMES, now=NOW, window_hours=48)
    assert events[0]["bookmakers"][0]["markets"][0]["outcomes"] == [
        {"name": "Under", "point": 9.5, "price": 1.45}]
    _, counts = op.to_events([_fixture(hours=-1), _fixture(hours=100)], m, NAMES,
                             now=NOW, window_hours=48)
    assert counts["fixtures_in_window"] == 0 and counts["fixtures_listed"] == 2


def test_a_fixture_whose_teams_cannot_be_named_is_counted_not_guessed():
    events, counts = op.to_events([_fixture()], op.corner_card_markets(CATALOGUE), {},
                                  now=NOW, window_hours=48)
    assert events == [] and counts["unnamed"] == 1


def test_participant_names_accept_list_or_mapping():
    assert op.participant_names([{"participantId": 17, "participantName": "Man City"}]) == {"17": "Man City"}
    assert op.participant_names({"17": "Man City"}) == {"17": "Man City"}


class _Fake(op.OddsPapiClient):
    def __init__(self):
        super().__init__("k", ttl=60)
        self.calls = []

    def get(self, path, **params):
        self._roll_day()
        self.requests_today += 1
        self.calls.append((path, params))
        if path == "/markets":
            return CATALOGUE
        if path == "/participants":
            return {"17": "Manchester City", "38": "Chelsea"}
        return [_fixture(hours=5)]


def test_a_sweep_costs_a_few_requests_however_many_games_and_caches(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "oddspapi_tournament_ids", "17,18,24,8,23,35,34,238,7,679,10783")
    monkeypatch.setattr(settings, "oddspapi_tournaments_per_request", 5)
    fake = _Fake()
    src = op.OddsPapiPaddySource(fake)
    src.get_events()
    odds_calls = [c for c in fake.calls if c[0] == "/odds-by-tournaments"]
    assert len(odds_calls) == 3                       # 11 tournaments, 5 a request
    assert all(c[1]["bookmaker"] == "paddypower" for c in odds_calls)
    first = len(fake.calls)
    src.get_events()                                  # within the TTL: all cached
    assert len(fake.calls) == first
    assert src.last_sweep["source"] == "oddspapi"
    assert src.last_sweep["catalogue_corner_card_markets"] == 3


def test_the_daily_request_budget_stops_the_client(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "oddspapi_daily_request_budget", 0)
    c = op.OddsPapiClient("k")
    try:
        c.get("/markets")
        raised = False
    except RuntimeError as exc:
        raised = "budget" in str(exc)
    assert raised


def test_the_diagnostics_verdict_still_reads_an_oddspapi_sweep():
    from app.routers.cards import diagnose
    src = op.OddsPapiPaddySource(_Fake())
    src.get_events()
    verdict = diagnose(src, None)["verdict"]
    assert "No cards client" not in verdict and "No sweep" not in verdict


# --- competitions by name, and a book's coverage -----------------------------------

def test_a_competition_can_be_named_and_an_unknown_one_is_reported():
    known = {17: "Premier League", 555: "UEFA Nations League", 556: "UEFA Women's Nations League"}
    ids, missing = op.resolve_tournaments(["17", "UEFA Nations League", "Narnia Cup"], known)
    assert ids == [17, 555] and missing == ["Narnia Cup"]
    # exact match only: the women's competition is never taken for the men's
    ids, _ = op.resolve_tournaments(["uefa nations league"], known)
    assert ids == [555]


def test_the_default_sweep_names_the_nations_league():
    from app.config import settings
    assert "UEFA Nations League" in settings.oddspapi_tournament_ids
    assert "10783" not in settings.oddspapi_tournament_ids


def test_a_sweep_resolves_named_competitions_and_reports_the_rest(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "oddspapi_tournament_ids", "17,UEFA Nations League,Narnia Cup")

    class F(_Fake):
        def get(self, path, **params):
            if path == "/tournaments":
                self.calls.append((path, params))
                return [{"tournamentId": 17, "tournamentName": "Premier League"},
                        {"tournamentId": 555, "tournamentName": "UEFA Nations League"}]
            return super().get(path, **params)

    f = F()
    src = op.OddsPapiPaddySource(f)
    src.get_events()
    asked = [c[1]["tournamentIds"] for c in f.calls if c[0] == "/odds-by-tournaments"]
    assert asked == ["17,555"]
    assert src.last_sweep["unresolved_tournaments"] == ["Narnia Cup"]


def test_coverage_counts_two_sided_lines_per_fixture_by_market_type():
    index = op.catalogue_index([
        {"marketId": 1, "marketType": "totals-corners", "marketName": "Corners OU",
         "handicap": 9.5, "period": "fulltime"},
        {"marketId": 2, "marketType": "totals-corners", "marketName": "Corners OU",
         "handicap": 10.5, "period": "fulltime"},
        {"marketId": 3, "marketType": "team-totals", "marketName": "Home Team Total",
         "handicap": 1.5, "period": "fulltime"},
    ])
    fx = {"bookmakerOdds": {"pinnacle": {"markets": {
        "1": {"outcomes": {"a": _p(1.9), "b": _p(1.9)}},
        "2": {"outcomes": {"a": _p(2.3), "b": _p(1.6)}},
        "3": {"outcomes": {"a": _p(2.0), "b": _p(None)}},     # one side only: not counted
    }}}}
    rep = op.coverage([fx, fx], index, "pinnacle")
    assert rep["fixtures_with_prices"] == 2
    corners = rep["market_types"]["totals-corners [fulltime]"]
    assert corners["two_sided_lines_per_fixture"] == {"min": 2, "median": 2, "max": 2}
    assert corners["lines_seen"] == [9.5, 10.5]
    assert "team-totals [fulltime]" not in rep["market_types"]
