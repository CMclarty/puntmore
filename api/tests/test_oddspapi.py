"""Paddy Power corners/cards from OddsPapi — see cards/oddspapi.py.

Shapes here are the ones the trial responses showed (market id = one market at
one line; odds under bookmakerOdds.<book>.markets.<id>.outcomes.<id>.players."0";
fixtures carry participant ids). The catalogue field names are unverified, so
these tests pin the TOLERANCE as much as any one spelling.
"""
from datetime import datetime, timedelta, timezone

import pytest

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
    monkeypatch.setattr(settings, "oddspapi_fair_book", "pinnacle")
    odds_calls = [c for c in fake.calls if c[0] == "/odds-by-tournaments"]
    # 11 tournaments, 5 a request, once per book
    assert [c[1]["bookmaker"] for c in odds_calls] == ["paddypower"] * 3 + ["pinnacle"] * 3
    first = len(fake.calls)
    src.get_events()                                  # within the TTL: all cached
    assert len(fake.calls) == first
    assert src.last_sweep["source"] == "oddspapi"
    assert src.last_sweep["catalogue_total_markets"] == 4   # corners x2, cards, goals


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
    assert asked == ["17,555", "17,555"]              # Paddy, then Pinnacle
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


# --- Pinnacle's fair side from the same vendor --------------------------------------

PIN_MARKETS = {
    "10803": {"outcomes": {"10803": _p(2.30), "10804": _p(1.62)}},
    "10807": {"outcomes": {"10807": _p(3.30), "10808": _p(1.33)}},
}


def _pin_fixture(fid="id1"):
    fx = _fixture(fid=fid)
    fx["bookmakerOdds"] = {"pinnacle": {"markets": PIN_MARKETS}}
    return fx


def test_pinnacle_joins_paddy_on_the_fixture_id_not_the_team_names():
    paddy = [_fixture(fid="id1"), _fixture(fid="id2")]
    pin = [_pin_fixture("id1")]                        # Pinnacle has only one of them
    fixtures = op.merge_books(paddy, pin)
    events, counts = op.to_events(fixtures, op.corner_card_markets(CATALOGUE), NAMES,
                                  now=NOW, window_hours=48, fair_book="pinnacle")
    by_id = {e["id"]: e for e in events}
    assert [b["key"] for b in by_id["id1"]["bookmakers"]] == ["paddypower", "pinnacle"]
    assert [b["key"] for b in by_id["id2"]["bookmakers"]] == ["paddypower"]
    assert counts["carried_paddy_prices"] == 2 and counts["carried_pinnacle_prices"] == 1
    pin_book = by_id["id1"]["bookmakers"][1]
    corners = next(m for m in pin_book["markets"] if m["key"] == op.CORNERS)
    # RAW prices, both sides of every line — value.py de-vigs
    assert {(o["name"], o["point"], o["price"]) for o in corners["outcomes"]} == {
        ("Over", 9.5, 2.30), ("Under", 9.5, 1.62),
        ("Over", 10.5, 3.30), ("Under", 10.5, 1.33),
    }


def test_the_pricing_pipeline_values_paddy_against_oddspapi_pinnacle():
    from app.routers.cards import price_events
    fixtures = op.merge_books([_fixture()], [_pin_fixture()])
    events, _ = op.to_events(fixtures, op.corner_card_markets(CATALOGUE), NAMES,
                             now=NOW, window_hours=48, fair_book="pinnacle")
    rows = price_events(events, -1.0)
    assert rows, "a fixture with both books must produce rows"


def test_merge_pinnacle_leaves_an_oddspapi_fair_side_alone():
    from app.cards.client import merge_pinnacle

    class Boom:
        def board(self):
            raise AssertionError("bettingiscool must not be asked")

        def odds_for(self, _):
            raise AssertionError("bettingiscool must not be asked")

    fixtures = op.merge_books([_fixture()], [_pin_fixture()])
    events, _ = op.to_events(fixtures, op.corner_card_markets(CATALOGUE), NAMES,
                             now=NOW, window_hours=48, fair_book="pinnacle")
    assert merge_pinnacle(events, Boom()) is events


def test_a_rate_limited_request_is_retried_once_after_the_wait_it_names(monkeypatch):
    sleeps, sent = [], []

    class R:
        def __init__(self, code, text="", body=None):
            self.status_code, self.text, self._body = code, text, body

        def json(self):
            return self._body

    replies = [R(429, '{"error":{"details":"Please wait 0.80 seconds before making another request"}}'),
               R(200, body=[{"ok": 1}])]
    monkeypatch.setattr(op.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(op.httpx, "get", lambda *a, **k: sent.append(1) or replies.pop(0))
    c = op.OddsPapiClient("k")
    assert c.get("/odds-by-tournaments", tournamentIds="7") == [{"ok": 1}]
    assert len(sent) == 2
    assert any(abs(s - 1.0) < 1e-9 for s in sleeps)    # 0.80 + margin


def test_back_to_back_requests_are_spaced(monkeypatch):
    sleeps = []

    class R:
        status_code, text = 200, ""

        def json(self):
            return []

    monkeypatch.setattr(op.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(op.httpx, "get", lambda *a, **k: R())
    c = op.OddsPapiClient("k")
    c.get("/a")
    c.get("/b")
    assert sleeps and sleeps[-1] > 0.5


def test_the_default_sweep_takes_the_uefa_qualifiers_and_pinnacle():
    from app.config import settings
    entries = settings.oddspapi_tournament_ids.split(",")
    assert "11" in entries and "27" in entries
    assert settings.oddspapi_fair_book == "pinnacle"
    assert op.TOURNAMENT_SPORT_KEYS[23755] == "soccer_uefa_nations_league"


def _two_way(mid, name, line):
    return {"marketId": mid, "marketName": name, "handicap": line, "sportId": 10,
            "outcomes": [{"outcomeId": mid * 10, "outcomeName": "Over"},
                         {"outcomeId": mid * 10 + 1, "outcomeName": "Under"}]}


def test_the_check_classifies_saves_and_match_goals_totals():
    cat = CATALOGUE + [
        _two_way(40001, "Total Goalkeeper Saves", 6.5),
        _two_way(40002, "Total Goals", 3.5),
        # Look-alikes that are NOT match goals or saves.
        _two_way(40003, "Total Goal Kicks", 15.5),
        _two_way(40004, "Total Shots on Target", 8.5),
        _two_way(40005, "Home Team Saves", 3.5),
        _two_way(40006, "1st Half Goals", 1.5),
    ]
    found = op.match_total_markets(cat, kinds=(op.SAVES, op.GOALS))
    assert {mid: (k, line) for mid, (k, line, _) in found.items()} == {
        "40001": (op.SAVES, 6.5),
        "40002": (op.GOALS, 3.5),
        "1010": (op.GOALS, 2.5),     # the bare "Over Under" is the goals line
    }
    # The pricing path is unchanged: corners and cards only.
    assert {k for k, _, _ in op.corner_card_markets(cat).values()} == {op.CORNERS, op.CARDS}


def test_booking_points_are_not_a_card_count():
    # 10 a yellow, 25 a red: a different scale from Pinnacle's card-count
    # ladder, so pricing one off the other would be wildly wrong.
    found = op.corner_card_markets([_two_way(50001, "Total Booking Points", 40.5)])
    assert found == {}


class _R:
    def __init__(self, code, text="", body=None):
        self.status_code, self.text, self._body = code, text, body

    def json(self):
        return self._body


_SPENT = _R(429, '{"message":"Request limit exceeded","details":"You have exceeded your '
                 'request limit of 200000 requests.","code":"REQUEST_LIMIT_EXCEEDED"}')


def test_an_exhausted_quota_is_not_retried_and_stops_billable_calls(monkeypatch):
    # OddsPapi docs: a spent quota answers 429 REQUEST_LIMIT_EXCEEDED. Unlike
    # the rate limit, waiting a second cannot fix it.
    sent = []
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    monkeypatch.setattr(op.httpx, "get", lambda url, **k: sent.append(url) or _SPENT)
    c = op.OddsPapiClient("k")
    with pytest.raises(RuntimeError, match="quota exhausted"):
        c.get("/odds-by-tournaments", tournamentIds="7")
    assert len(sent) == 1                       # no retry
    assert c.requests_today == 0                # rejected calls are not billed
    with pytest.raises(RuntimeError, match="quota exhausted"):
        c.get("/odds-by-tournaments", tournamentIds="8")
    assert len(sent) == 1                       # nothing more sent


def test_the_free_account_endpoint_says_when_the_quota_has_reset(monkeypatch):
    replies = [_SPENT,
               _R(200, body={"request_limit": 200000, "request_count": 12}),   # /account
               _R(200, body=[{"ok": 1}])]
    sent = []
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    monkeypatch.setattr(op.httpx, "get", lambda url, **k: sent.append(url) or replies.pop(0))
    c = op.OddsPapiClient("k")
    with pytest.raises(RuntimeError):
        c.get("/odds-by-tournaments", tournamentIds="7")
    c._exhausted_at -= op.QUOTA_RECHECK_SECONDS         # half an hour later
    assert c.get("/odds-by-tournaments", tournamentIds="7") == [{"ok": 1}]
    assert [u.rsplit("/", 1)[-1] for u in sent] == ["odds-by-tournaments", "account", "odds-by-tournaments"]


def test_the_odds_refresh_tightens_as_the_next_kickoff_nears():
    tiers = op.parse_ttl_tiers("2700:120,7200:300,21600:900,inf:3600")
    at = lambda hours: [{"startTime": _iso(hours)}, {"startTime": _iso(30)}]
    assert op.ttl_for(at(0.5), tiers, now=NOW) == 120        # 30 min out
    assert op.ttl_for(at(1.5), tiers, now=NOW) == 300
    assert op.ttl_for(at(5), tiers, now=NOW) == 900
    assert op.ttl_for(at(10), tiers, now=NOW) == 3600
    # Kicked-off fixtures do not count; nothing to come is the loosest gear.
    assert op.ttl_for([{"startTime": _iso(-1)}], tiers, now=NOW) == 3600


def test_a_chunk_near_kickoff_is_refetched_sooner_than_a_quiet_one(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "oddspapi_ttl_tiers", "2700:120,inf:3600")
    soon = [{"startTime": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()}]
    late = [{"startTime": (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()}]
    sent = []
    replies = {"7": soon, "8": late}
    monkeypatch.setattr(op.time, "sleep", lambda s: None)

    class R:
        status_code, text = 200, ""
        def __init__(self, body): self.body = body
        def json(self): return self.body

    monkeypatch.setattr(op.httpx, "get",
                        lambda url, params, **k: sent.append(params["tournamentIds"]) or R(replies[params["tournamentIds"]]))
    c = op.OddsPapiClient("k")
    c.odds_by_tournaments([7]); c.odds_by_tournaments([8])
    # Age both entries by 5 minutes: past the 2-minute gear, inside the hourly.
    for k, (at, v) in list(c._odds._data.items()):
        c._odds._data[k] = (at - 300, v)
    c.odds_by_tournaments([7]); c.odds_by_tournaments([8])
    assert sent == ["7", "8", "7"]


_ACCOUNT = {"subscriptions": [
    {"is_active": True, "plan": "dev", "bookmakers": {"pinnacle+30": {}, "paddypower": {}}},
    {"is_active": False, "plan": "free", "bookmakers": {"pinnacle": {}, "bet365": {}}},
]}


def test_the_fair_book_resolves_to_the_slug_the_plan_grants(monkeypatch):
    # Live, Oct 2026: the dev plan grants "pinnacle+30"; asking for plain
    # "pinnacle" came back 403 RESTRICTED_ACCESS on every chunk — billed.
    c = op.OddsPapiClient("k")
    monkeypatch.setattr(c, "account", lambda: _ACCOUNT)
    assert c.resolve_book("pinnacle") == "pinnacle+30"
    assert c.resolve_book("paddypower") == "paddypower"
    assert c.resolve_book("bet365") == "bet365"          # not granted: unchanged


def test_a_book_keyed_by_its_base_slug_is_still_read():
    fx = {"bookmakerOdds": {"pinnacle": {"markets": {"10803": {"outcomes": {
        "10803": _p(1.9), "10804": _p(1.95)}}}}}}
    got = op._book_markets(fx, "pinnacle+30", op.corner_card_markets(CATALOGUE))
    assert {o["name"] for o in got["alternate_totals_corners"]} == {"Over", "Under"}


def test_a_failed_odds_request_is_held_not_resent_every_sweep(monkeypatch):
    sent = []
    restricted = _R(403, '{"error":{"code":"RESTRICTED_ACCESS","details":"Restricted bookmakers: pinnacle."}}')
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    monkeypatch.setattr(op.httpx, "get", lambda url, **k: sent.append(url) or restricted)
    c = op.OddsPapiClient("k")
    for _ in range(5):
        with pytest.raises(RuntimeError):
            c.odds_by_tournaments([17], bookmaker="pinnacle")
    assert len(sent) == 1                                   # billed once, not five times
    held = c._failed.peek(("odds", (17,), "pinnacle"))
    assert held[1] == op.FAILED_HOLD_MAX                    # a plan refusal is held for hours


def test_the_empty_screen_names_a_pinnacle_failure_on_the_oddspapi_path():
    from types import SimpleNamespace
    from app.routers.cards import diagnose
    client = SimpleNamespace(last_sweep={
        "source": "oddspapi", "fair_source": "oddspapi:pinnacle+30",
        "fixtures_in_window": 55, "carried_paddy_prices": 55, "carried_pinnacle_prices": 0,
        "errors": ["pinnacle+30 [17]: OddsPapi /odds-by-tournaments: HTTP 403: RESTRICTED_ACCESS"],
    })
    v = diagnose(client, None)["verdict"]
    assert "Pinnacle (pinnacle+30)" in v and "403" in v
    client.last_sweep.update(carried_pinnacle_prices=40, errors=[])
    # Fair side fine: it must NOT fall back to the old bettingiscool verdicts.
    assert "bettingiscool" not in diagnose(client, None)["verdict"]


def test_requests_from_two_threads_never_overlap(monkeypatch):
    # Live, 15 Oct: the sweep and a page load fetched together and OddsPapi
    # answered 429 RATE_LIMITED, costing a chunk.
    import threading
    active, overlap = [0], []

    def fake_get(url, **k):
        active[0] += 1
        if active[0] > 1:
            overlap.append(url)
        real_sleep(0.01)
        active[0] -= 1
        return _R(200, body=[])

    real_sleep = op.time.sleep
    monkeypatch.setattr(op, "MIN_REQUEST_GAP", 0)
    monkeypatch.setattr(op.httpx, "get", fake_get)
    c = op.OddsPapiClient("k", ttl=0)
    threads = [threading.Thread(target=c.get, args=("/odds-by-tournaments",), kwargs={"tournamentIds": str(i)})
               for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert overlap == []


def test_diagnostics_names_a_sweep_that_failed_before_pricing():
    from types import SimpleNamespace
    from app import scheduler
    from app.routers.cards import diagnose
    scheduler.LAST_CARDS_ERROR.update(at="now", error="KeyError('point')")
    try:
        d = diagnose(SimpleNamespace(last_sweep={"fixtures_in_window": 3, "carried_paddy_prices": 3}), None)
        assert "FAILED before pricing" in d["verdict"] and "KeyError" in d["verdict"]
    finally:
        scheduler.LAST_CARDS_ERROR.clear()


def test_no_fixtures_in_a_competition_is_an_empty_answer_cached_hourly(monkeypatch):
    # Live, 15 Oct: the internationals chunk outside a window answers 404
    # FIXTURE_NOT_FOUND. Treated as an error it was re-sent every 10 min.
    sent = []
    nf = _R(404, '{"error":{"message":"No fixtures found for the specified tournament IDs and '
                 'bookmaker.","code":"FIXTURE_NOT_FOUND"}}')
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    monkeypatch.setattr(op.httpx, "get", lambda url, **k: sent.append(url) or nf)
    c = op.OddsPapiClient("k")
    assert c.odds_by_tournaments([23755]) == []
    for k, (at, v) in list(c._odds._data.items()):
        c._odds._data[k] = (at - 1800, v)          # half an hour later
    assert c.odds_by_tournaments([23755]) == []
    assert len(sent) == 1                          # still cached: hourly gear
