"""odds-api.net -> the counts pipeline (cards/oddsapinet.py): which games Hills
lists, how a snapshot's William Hill and Pinnacle items become the events
`price_events` already reads, and how old and how long-lived a board is.
Hermetic: recorded responses in tests/fixtures/odds_api_net/."""
from datetime import datetime, timezone

import pytest

from app.cards import feed, laddered
from app.cards.oddsapinet import (
    Listing, SnapshotLines, fixture_of, listed_events, parse_snapshot, priced_at,
    snapshot_ttl, to_event,
)
from app.cards.oddspapi import (
    CARDS, CARDS_HANDICAP, CORNERS, CORNERS_HANDICAP, GOALS, TEAM_CARDS_AWAY,
    TEAM_CARDS_HOME, TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME,
)
from tests.oan_fixtures import ARMENIA_EMPTY, NOW, load


def _item(bookmaker="williamhill", bet_type="total", metric="corners", line="over 9.5",
          odds=1.8, period="full time", is_available=True, side=None):
    item = {"bookmaker": bookmaker, "bet_type": bet_type, "metric": metric, "line": line,
            "odds": odds, "period": period, "is_available": is_available}
    if side:
        item["side"] = side
    return item


def _snapshot(items):
    return {"event_id": "1", "bookmaker_as_of_ts_ms": {}, "stale_bookmakers": [],
            "target_refresh_interval_seconds": 3599, "complete": True, "items": items}


def test_listing_keeps_hills_competitions_in_the_window():
    got = listed_events(load("events.json"), now=NOW, window_hours=168)
    assert [l.event_id for l in got] == ["4229698994", "1417142635"]
    eng = got[1]
    assert (eng.home, eng.away, eng.league) == ("England", "Croatia", "UEFA Nations League")
    assert eng.start == datetime(2026, 11, 12, 19, 45, tzinfo=timezone.utc)
    assert "williamhill" in eng.books and "pinnacle" not in got[0].books


def test_listing_drops_started_games_and_games_beyond_the_window():
    assert [l.event_id for l in listed_events(load("events.json"),
            now=datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc), window_hours=168)] == ["1417142635"]
    assert [l.event_id for l in listed_events(load("events.json"), now=NOW, window_hours=30)] == ["4229698994"]


def test_a_game_listed_under_both_championship_names_is_kept_once():
    game = {"home_team": "Leeds", "away_team": "Hull", "start_time": 1794512700,
            "bookmakers": ["williamhill"]}
    body = {"items": [{**game, "event_id": "1", "league": "English Championship"},
                      {**game, "event_id": "2", "league": "EFL Championship"}]}
    assert [l.event_id for l in listed_events(body, now=NOW, window_hours=168)] == ["1"]


def test_fixture_of_maps_the_league_to_the_sofascore_id():
    eng = listed_events(load("events.json"), now=NOW, window_hours=168)[1]
    assert fixture_of(eng) == {"id": "oan:1417142635", "home": "England", "away": "Croatia",
                               "start": datetime(2026, 11, 12, 19, 45, tzinfo=timezone.utc),
                               "tournament_id": 23755, "round": None}
    start = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
    for league, tid in (("EPL", 17), ("English FA Cup", 19), ("EFL Cup", 21)):
        listing = Listing("9", "A", "B", start, league, frozenset({"williamhill"}))
        assert fixture_of(listing)["tournament_id"] == tid


def test_the_snapshot_maps_william_hill_and_pinnacle():
    lines = parse_snapshot(load("snapshot_1417142635.json"))
    wh, pin = lines.markets["williamhill"], lines.markets["pinnacle"]
    assert {k: len(v) for k, v in wh.items()} == {CORNERS: 20, CARDS: 12, GOALS: 12,
        TEAM_CORNERS_HOME: 20, TEAM_CORNERS_AWAY: 14, TEAM_CARDS_HOME: 6, TEAM_CARDS_AWAY: 10}
    assert {k: len(v) for k, v in pin.items()} == {CORNERS: 10, CARDS: 10, GOALS: 8,
        TEAM_CORNERS_HOME: 2, TEAM_CORNERS_AWAY: 2, TEAM_CARDS_HOME: 2, TEAM_CARDS_AWAY: 2,
        CORNERS_HANDICAP: 10, CARDS_HANDICAP: 6}
    assert {"name": "Over", "point": 9.5, "price": 1.8} in wh[CORNERS]
    assert {"name": "Under", "point": 10.0, "price": 1.64} in pin[CORNERS]
    assert {"name": "Home", "point": -2.5, "price": 1.51} in pin[CORNERS_HANDICAP]
    assert {"name": "Away", "point": -2.5, "price": 2.4} in pin[CORNERS_HANDICAP]
    assert {"name": "Away", "point": 0.5, "price": 1.53} in pin[CARDS_HANDICAP]
    assert not [o for o in pin[GOALS] if o["point"] in (2.25, 2.75, 3.25, 3.75, 4.25)]
    assert lines.as_of["williamhill"] == datetime(2026, 11, 11, 11, 26, 38, tzinfo=timezone.utc)
    assert lines.as_of["pinnacle"] == datetime(2026, 11, 11, 12, 36, 44, 578000, tzinfo=timezone.utc)
    assert (lines.refresh_seconds, lines.stale_books, lines.complete) == (3599, frozenset(), True)


def test_odd_even_halves_suspended_other_stats_and_other_books_are_dropped():
    body = _snapshot([
        _item(line="odd", odds=1.85),
        _item(line="over 9.5", period="1st half", odds=2.1),
        _item(line="over 8.5", is_available=False, odds=1.4),
        _item(metric="shots", line="over 20.5", odds=1.9),
        _item(bet_type="player prop", metric="player saves", line="over 2.5", odds=1.7),
        _item(bookmaker="betfair", metric="STANDARD", line="over 2.5", odds=1.5),
        _item(line="over 9.5", odds=1.8),
    ])
    assert parse_snapshot(body).markets == {
        "williamhill": {CORNERS: [{"name": "Over", "point": 9.5, "price": 1.8}]}}


def test_metric_case_is_ignored():
    body = _snapshot([_item(bookmaker="pinnacle", metric="STANDARD", line="over 2.5", odds=1.47)])
    assert parse_snapshot(body).markets["pinnacle"][GOALS] == [
        {"name": "Over", "point": 2.5, "price": 1.47}]


def test_to_event_is_the_counts_model_shape():
    eng = listed_events(load("events.json"), now=NOW, window_hours=168)[1]
    ev = to_event(eng, parse_snapshot(load("snapshot_1417142635.json")))
    assert (ev["id"], ev["home_team"], ev["away_team"], ev["commence_time"], ev["sport_key"],
            ev["fair_source"]) == ("oan:1417142635", "England", "Croatia", "2026-11-12T19:45:00Z",
                                   "soccer_uefa_nations_league", "odds-api.net")
    assert [b["key"] for b in ev["bookmakers"]] == ["williamhill", "pinnacle"]
    wh = ev["bookmakers"][0]["markets"]
    assert {"name": "Over", "point": 9.5, "price": 1.8} in next(
        m["outcomes"] for m in wh if m["key"] == CORNERS)


def test_without_william_hill_there_is_no_event_and_without_pinnacle_wh_rides_alone():
    events = listed_events(load("events.json"), now=NOW, window_hours=168)
    arm, eng = events[0], events[1]
    assert to_event(arm, parse_snapshot(ARMENIA_EMPTY)) is None
    body = load("snapshot_1417142635.json")
    body["items"] = [i for i in body["items"] if i["bookmaker"] != "pinnacle"]
    ev = to_event(eng, parse_snapshot(body))
    assert [b["key"] for b in ev["bookmakers"]] == ["williamhill"]


def test_priced_at_is_the_older_book_and_ttl_follows_the_vendor():
    lines = parse_snapshot(load("snapshot_1417142635.json"))
    assert priced_at(lines) == datetime(2026, 11, 11, 11, 26, 38, tzinfo=timezone.utc)
    assert snapshot_ttl(lines) == 3599
    for refresh, ttl in ((60, 120), (7200, 3600), (None, 3600)):
        held = SnapshotLines(markets={}, as_of={}, stale_books=frozenset(),
                             refresh_seconds=refresh, complete=True)
        assert snapshot_ttl(held) == ttl
    assert priced_at(SnapshotLines(markets={}, as_of={}, stale_books=frozenset(),
                                   refresh_seconds=None, complete=True)) is None


def test_the_recorded_game_prices_through_the_unchanged_pipeline():
    eng = listed_events(load("events.json"), now=NOW, window_hours=168)[1]
    rows = feed.price_events([to_event(eng, parse_snapshot(load("snapshot_1417142635.json")))],
                             -0.5, "williamhill")
    run = laddered.LAST_RUNS["williamhill"]
    # 82: William Hill's 11.5 corners Over and Under are priced (Paddy's
    # excluded line is Paddy's only, Task 2b).
    assert len(rows) == 82 and run["ladders_fitted"] == 3
    assert {r.side for r in rows if r.market_key == CORNERS and r.line == 11.5} == {"Over", "Under"}
    assert run["by_market"]["team_corners"]["split_disagreement"] == 0
    assert run["by_market"]["team_corners"]["split_sources"]["handicap"] == 2
    over95 = next(r for r in rows if r.market_key == CORNERS and r.line == 9.5 and r.side == "Over")
    assert over95.paddy_odds == 1.8 and over95.fair_odds == pytest.approx(2.05, abs=0.01)
    assert {r.event_id for r in rows} == {"oan:1417142635"}
    assert not [r for r in rows if "saves" in r.market_key]
