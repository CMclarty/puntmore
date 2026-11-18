"""2-Up with bet365's price from Bookie Bashing's Early Payout Tracker (the
`bb` source, the register 18 Nov 2026): the ingest route, the two modes over
the tracker's rows, the join to OddsPapi's fixtures, freshness and status.
OddsPapi is a stub (never live); the tracker is posted as the Hub would."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.security import user_permissions
from app.twoup import model
from app.twoup.feed import TwoUpFeed, value_rows
from app.twoup.model import Params, RescueTable
from app.twoup.tracker import TrackerRow, event_id_for

from tests.twoup_helpers import NOW, FakeOddsPapi, Session, engine, fixture

KEY = "ingest-key"
KO = NOW + timedelta(hours=5)
DAY = KO.date().isoformat()
DELAY = timedelta(minutes=30)        # the fake plan's `pinnacle+30`


@pytest.fixture(autouse=True)
def world(monkeypatch):
    Base.metadata.create_all(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(settings, "twoup_bet365_source", "bb")
    monkeypatch.setattr(settings, "twoup_tournament_ids", "17,18")
    monkeypatch.setattr(settings, "oddspapi_fair_book", "pinnacle")
    monkeypatch.setattr(settings, "saves_ingest_key", KEY)
    yield
    app.dependency_overrides.pop(get_db, None)
    app.state.twoup_feed = None
    app.state.twoup_tracker = None
    Base.metadata.drop_all(bind=engine)


def pinnacle_board(*extra):
    """Pinnacle's 1X2 only: OddsPapi carries no bet365 in this source."""
    return {"pinnacle+30": [
        fixture("f1", "Arsenal", "Chelsea", KO, (1.90, 3.70, 4.40), "pinnacle+30"),
        fixture("f2", "Leeds", "Hull", KO, (2.40, 3.30, 3.10), "pinnacle+30"),
        *extra,
    ]}


def row(home="Arsenal", away="Chelsea", side="home", book_odds=2.05, rating=104.0,
        n_odds=None, updated_s=60, seen_at=None, bookmaker="bet365", kickoff=None, **kw):
    seen = seen_at or NOW
    out = {"bookmaker": bookmaker, "event": f"{home} v {away}", "home": home, "away": away,
           "selection": {"home": home, "away": away}.get(side, "Draw"), "side": side,
           "book_odds": book_odds, "lay": round(book_odds * 1.05, 2), "rating": rating,
           "n_odds": n_odds or round(book_odds / (rating / 100), 3),
           "kickoff": (kickoff or KO).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "updated_s": updated_s, "seen_at": seen.isoformat()}
    out.update(kw)
    return out


def _feed(boards=None):
    client = FakeOddsPapi(boards if boards is not None else pinnacle_board())
    return TwoUpFeed(client, params=Params()), client


def _rows(feed, rows, now=NOW):
    """The tracker post as the route takes it: bet365's home and away rows."""
    kept = [TrackerRow(
        bookmaker="bet365", event=r["event"], home=r["home"], away=r["away"],
        selection=r["selection"], side=r["side"], book_odds=r["book_odds"], lay=r["lay"],
        rating=r["rating"], n_odds=r["n_odds"],
        kickoff=model_dt(r["kickoff"]), updated_s=r["updated_s"], seen_at=model_dt(r["seen_at"]),
    ) for r in rows if r["bookmaker"] == "bet365" and r["side"] != "draw"]
    return feed.take_tracker(kept, len(rows), {}, now)


def model_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def live_row(*args, **kw):
    """A row as the route sees one now: the route reads Render's real clock."""
    now = datetime.now(timezone.utc)
    kw.setdefault("seen_at", now)
    kw.setdefault("kickoff", now + timedelta(hours=5))
    return row(*args, **kw)


def by_key(rows):
    return {(c.event_id, c.side): c for c in rows}


def post(api, rows, key=KEY):
    return api.post("/2up/bb", json={"prices": rows}, headers={"X-API-Key": key} if key else {})


# --- no OddsPapi request for bet365 ------------------------------------------------

def test_bb_source_asks_oddspapi_for_pinnacle_only():
    feed, client = _feed()
    _rows(feed, [row()])
    feed.refresh(NOW)
    assert client.calls and {b for _, b in client.calls} == {"pinnacle+30"}
    assert len(client.calls) == 1               # one chunk (17,18), Pinnacle alone
    assert feed.last_pass["pinnacle"]["book"] == "pinnacle+30"


def test_oddspapi_source_still_asks_for_both_books_and_ignores_the_tracker(monkeypatch):
    monkeypatch.setattr(settings, "twoup_bet365_source", "oddspapi")
    boards = pinnacle_board()
    boards["bet365"] = [fixture("f1", "Arsenal", "Chelsea", KO, (2.05, 3.50, 4.00), "bet365")]
    feed, client = _feed(boards)
    _rows(feed, [row(rating=150.0)])            # held, but not this source's
    rows = feed.refresh(NOW)
    assert {b for _, b in client.calls} == {"pinnacle+30", "bet365"}
    assert {c.event_id for c in rows} == {"f1"}
    home = by_key(rows)[("f1", "home")]
    assert home.bb_rating is None and model.flagged_by(home, feed.params) == ["pinnacle"]
    assert feed.status(NOW)["provider"] == "oddspapi"


# --- the two modes ----------------------------------------------------------------------

def test_a_row_both_modes_flag_carries_the_model_fields_and_bbs_beside_them():
    feed, _ = _feed()
    _rows(feed, [row(rating=104.0, n_odds=1.971)])
    rows = feed.refresh(NOW)
    c = by_key(rows)[(f"bb:arsenal|chelsea|{DAY}", "home")]
    assert model.flagged_by(c, feed.params) == ["bb", "pinnacle"]
    assert c.p_win == pytest.approx(model.fair_1x2(1.90, 3.70, 4.40)[0])
    assert c.rescue == feed.table.rate(c.p_win)
    assert c.fair_price == pytest.approx(1 / c.p_win)        # the model's, not BB's
    assert c.edge == pytest.approx(model.edge(c.p_win, c.rescue, 2.05))
    assert (c.bb_rating, c.bb_fair, c.competition) == (104.0, 1.971, "Premier League")


def test_a_row_only_bb_flags_has_no_model_fields():
    feed, _ = _feed()
    _rows(feed, [row("Real Betis", "Sevilla", side="away", book_odds=3.0, rating=105.0,
                     n_odds=2.857)])
    rows = feed.refresh(NOW)
    c = by_key(rows)[(event_id_for("Real Betis", "Sevilla", KO), "away")]
    assert (c.p_win, c.rescue) == (None, None)
    assert c.fair_price == 2.857 and c.bb_fair == 2.857 and c.bb_rating == 105.0
    assert c.ev == pytest.approx(0.05)                       # rating/100 - 1
    assert c.edge == pytest.approx(0.05 / 3.0)               # ev / price, probability units
    assert model.flagged_by(c, feed.params) == ["bb"]
    assert model.passes_filters(c, feed.params) is None
    assert feed.last_pass["unmatched"] == 1


def test_a_row_only_the_pinnacle_model_flags():
    feed, _ = _feed()
    _rows(feed, [row(rating=100.5)])                         # below 101: BB says no
    c = by_key(feed.refresh(NOW))[(f"bb:arsenal|chelsea|{DAY}", "home")]
    assert c.edge >= feed.params.min_edge
    assert model.flagged_by(c, feed.params) == ["pinnacle"]
    assert model.passes_filters(c, feed.params) is None


def test_a_row_neither_mode_flags_is_not_value():
    feed, _ = _feed()
    _rows(feed, [row(book_odds=1.80, rating=96.0)])
    c = feed.refresh(NOW)[0]
    assert model.flagged_by(c, feed.params) == []
    assert model.passes_filters(c, feed.params) == "no mode flags it"


def test_the_price_band_applies_to_both_modes():
    feed, _ = _feed()
    _rows(feed, [row("Real Madrid", "PSV", side="away", book_odds=13.0, rating=108.86,
                     n_odds=11.95)])
    c = feed.refresh(NOW)[0]
    assert model.flagged_by(c, feed.params) == ["bb"]
    assert model.passes_filters(c, feed.params) == "price outside band"


def test_draws_and_other_bookmakers_are_not_kept():
    api = TestClient(app)
    feed, _ = _feed()
    app.state.twoup_feed = feed
    r = post(api, [live_row(), live_row(side="draw", book_odds=3.5),
                   live_row(bookmaker="Paddy Power")])
    assert r.status_code == 200
    assert r.json()["kept"] == 1
    assert r.json()["not_kept"] == {"not bet365": 1, "draw": 1, "seen_at in the future": 0,
                                    "selection does not match side": 0}


# --- the join ---------------------------------------------------------------------------

def test_an_ambiguous_row_has_no_pinnacle_verdict_and_is_counted():
    boards = pinnacle_board(
        fixture("f8", "Real Madrid", "PSV", KO, (1.30, 5.5, 9.0), "pinnacle+30"),
        fixture("f9", "Atletico Madrid", "PSV Eindhoven", KO, (1.60, 4.0, 5.5), "pinnacle+30"))
    feed, _ = _feed(boards)
    _rows(feed, [row("Madrid", "PSV", book_odds=1.5, rating=102.0)])
    [c] = feed.refresh(NOW)
    assert c.p_win is None and model.flagged_by(c, feed.params) == ["bb"]
    assert (feed.last_pass["ambiguous"], feed.last_pass["matched"]) == (1, 0)


def test_an_exact_name_beats_a_looser_one():
    boards = pinnacle_board(
        fixture("f8", "Real Madrid", "PSV", KO, (1.30, 5.5, 9.0), "pinnacle+30"),
        fixture("f9", "Real Madrid Castilla", "PSV", KO, (1.60, 4.0, 5.5), "pinnacle+30"))
    feed, _ = _feed(boards)
    _rows(feed, [row("Real Madrid", "PSV", book_odds=1.5, rating=102.0)])
    [c] = feed.refresh(NOW)
    assert c.p_win == pytest.approx(model.fair_1x2(1.30, 5.5, 9.0)[0])
    assert feed.last_pass["matched"] == 1


def test_a_fixture_on_another_day_does_not_join():
    feed, _ = _feed()
    _rows(feed, [row(kickoff=KO + timedelta(hours=13))])
    # 13 h past Pinnacle's kick-off, outside the 12 h window; inside the 24 h horizon.
    [c] = feed.refresh(NOW)
    assert c.p_win is None and feed.last_pass["unmatched"] == 1


def test_no_row_joining_a_healthy_pinnacle_board_is_named_not_all():
    feed, _ = _feed()
    _rows(feed, [row("Real Betis", "Sevilla", book_odds=3.0, rating=105.0)])
    feed.refresh(NOW)
    st = feed.status(NOW)
    assert st["cannot_price"] == ["pinnacle mode: no tracker row priced against Pinnacle "
                                  "(1 unmatched, 0 ambiguous, 0 without Pinnacle's 1X2, "
                                  "0 too old)"]
    assert feed.served(NOW)[1]


def test_event_id_is_the_tracker_rows_whatever_the_join_does():
    feed, client = _feed()
    _rows(feed, [row()])
    first = feed.refresh(NOW)[0]
    client.boards = {"pinnacle+30": []}                 # Pinnacle drops the fixture
    second = feed.refresh(NOW + timedelta(minutes=5))[0]
    assert first.p_win is not None and second.p_win is None
    assert first.event_id == second.event_id == f"bb:arsenal|chelsea|{DAY}"
    assert event_id_for("Arsenal FC", "Chelsea", KO) == first.event_id   # the name folds


# --- freshness --------------------------------------------------------------------------

def test_a_row_is_dropped_by_its_prices_own_age_and_an_unknown_age_is_dropped():
    feed, _ = _feed()
    _rows(feed, [row(updated_s=601),                                         # 601 s old when read
                 row("Real Betis", "Sevilla", updated_s=200,
                     seen_at=NOW - timedelta(seconds=401)),                    # 601 s old now
                 row("Wigan", "Bolton", updated_s=None),                       # age unknown
                 row("Leeds", "Hull", updated_s=599)])
    rows = feed.refresh(NOW)
    assert [c.home for c in rows] == ["Leeds"]
    assert feed.last_pass["dropped"]["row_too_old"] == 2
    assert feed.last_pass["dropped"]["unknown_age"] == 1


def test_priced_at_is_the_staler_of_when_each_side_was_true():
    feed, client = _feed()
    _rows(feed, [row("Real Betis", "Sevilla", updated_s=90, seen_at=NOW)])
    [c] = feed.refresh(NOW)
    assert c.bet365_priced_at == c.priced_at == NOW - timedelta(seconds=90)
    assert c.fair_priced_at is None                          # no Pinnacle side
    # A row Pinnacle priced as well: its price was true 30 minutes before it was
    # fetched (`pinnacle+30`), and the row is as old as its staler side.
    old = NOW - timedelta(minutes=40)
    client.fetched_at = lambda ids, book: old
    later = NOW + timedelta(seconds=1)
    _rows(feed, [row(updated_s=30, seen_at=later)], later)
    [c] = feed.refresh(later)
    assert c.p_win is not None and c.bet365_priced_at == later - timedelta(seconds=30)
    assert c.fair_priced_at == old - DELAY == c.priced_at
    assert feed.status(later)["pinnacle"]["delay_s"] == 1800


def test_a_snapshot_too_old_serves_nothing_from_either_mode():
    feed, _ = _feed()
    _rows(feed, [row()])
    feed.refresh(NOW)
    later = NOW + timedelta(seconds=settings.twoup_bb_max_age_s + 1)
    assert feed.served(NOW)[1] and feed.served(later)[1] == []
    st = feed.status(later)
    assert st["cannot_price"][0].startswith("ALL — the Bookie Bashing tracker snapshot is")
    assert st["degraded"] is True and st["stale"] is False


def test_no_snapshot_since_boot_is_not_yet_refreshed():
    feed, _ = _feed()
    feed.refresh(NOW)
    st = feed.status(NOW)
    assert st["not_yet_refreshed"] is True
    assert st["cannot_price"][0].startswith("ALL — no Bookie Bashing tracker snapshot")
    assert feed.served(NOW)[1] == []


def test_pinnacle_failing_leaves_the_bb_mode_serving_partly_priced():
    feed, client = _feed()
    client.odds_by_tournaments = lambda ids, bookmaker="x": (_ for _ in ()).throw(RuntimeError("HTTP 500"))
    _rows(feed, [row(rating=104.0), row("Real Betis", "Sevilla", book_odds=3.0, rating=105.0)])
    rows = feed.refresh(NOW)
    assert len(rows) == 2 and all(c.p_win is None for c in rows)
    st = feed.status(NOW)
    assert st["stale"] is False and st["not_yet_refreshed"] is False
    assert st["cannot_price"] == ["pinnacle mode: every OddsPapi odds request for Pinnacle failed"]
    assert st["last_pass"]["priced_fixtures"] == 2
    assert len(feed.served(NOW)[1]) == 2


def test_pinnacle_reference_data_failing_is_not_all(monkeypatch):
    feed, client = _feed()
    client.catalogue = lambda: (_ for _ in ()).throw(RuntimeError("HTTP 503"))
    _rows(feed, [row()])
    feed.refresh(NOW)
    st = feed.status(NOW)
    assert st["cannot_price"] == ["pinnacle mode: OddsPapi reference data failed"]
    assert not any(c.startswith("ALL") for c in st["cannot_price"])


def test_a_tracker_post_composes_the_board_without_fetching():
    feed, client = _feed()
    feed.refresh(NOW)
    n = len(client.calls)
    _rows(feed, [row()], NOW + timedelta(seconds=30))
    at, served = feed.served(NOW + timedelta(seconds=30))
    assert len(client.calls) == n and len(served) == 1 and served[0].p_win is not None
    assert at == NOW + timedelta(seconds=30)


def test_an_older_post_is_refused_and_the_held_snapshot_stands():
    feed, _ = _feed()
    snap, why = _rows(feed, [row(seen_at=NOW)])
    assert snap is not None and why is None
    snap, why = _rows(feed, [row(rating=150.0, seen_at=NOW - timedelta(minutes=2))])
    assert snap is None and "older than the snapshot held" in why
    assert feed.tracker.snapshot().rows[0].rating == 104.0


def test_rescue_table_unreadable_is_named_beside_the_bb_rows():
    feed, _ = _feed()
    feed.table = RescueTable(buckets=(), path="x (missing)", loaded=False)
    _rows(feed, [row("Real Betis", "Sevilla", book_odds=3.0, rating=105.0)])
    feed.refresh(NOW)
    st = feed.status(NOW)
    assert st["cannot_price"][-1].startswith("rescue table unreadable")
    assert st["degraded"] is True and feed.served(NOW)[1]


# --- the routes -------------------------------------------------------------------------

def test_the_post_needs_the_ingest_key(monkeypatch):
    api = TestClient(app)
    app.state.twoup_feed, _ = _feed()
    assert post(api, [live_row()], key=None).status_code == 401
    assert post(api, [live_row()], key="wrong").status_code == 401
    assert post(api, [live_row()]).status_code == 200
    monkeypatch.setattr(settings, "saves_ingest_key", "")
    assert post(api, [live_row()], key="anything").status_code == 503


@pytest.mark.parametrize("bad", [
    {"side": "both"},
    {"book_odds": 1.0},
    {"rating": -1},
    {"kickoff": "Saturday"},
    {"seen_at": None},
    {"surprise": 1},
])
def test_a_malformed_row_422s_the_whole_post(bad):
    api = TestClient(app)
    feed, _ = _feed()
    app.state.twoup_feed = feed
    r = post(api, [row("Leeds", "Hull"), {**row(), **bad}])
    assert r.status_code == 422
    assert feed.tracker.snapshot() is None


def test_a_missing_key_422s():
    api = TestClient(app)
    app.state.twoup_feed, _ = _feed()
    r = row()
    del r["updated_s"]
    assert post(api, [r]).status_code == 422
    assert api.post("/2up/bb", json={"rows": []}, headers={"X-API-Key": KEY}).status_code == 422


def test_a_post_older_than_the_held_one_is_409():
    api = TestClient(app)
    app.state.twoup_feed, _ = _feed()
    now = datetime.now(timezone.utc)
    assert post(api, [live_row(seen_at=now)]).status_code == 200
    assert post(api, [live_row(seen_at=now - timedelta(minutes=1))]).status_code == 409


def test_the_post_is_taken_with_no_feed_on_the_deploy():
    api = TestClient(app)
    app.state.twoup_feed = None
    app.state.twoup_tracker = None
    assert post(api, [live_row()]).status_code == 200
    diag = api.get("/2up/diagnostics").json()
    assert diag["tracker"]["bet365_rows"] == 1


def test_feed_route_serves_bb_rows_with_null_model_fields(monkeypatch):
    # The real clock: the route reads now, so the tracker is posted now.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ko = now + timedelta(hours=5)
    feed, _ = _feed({"pinnacle+30": [
        fixture("f1", "Arsenal", "Chelsea", ko, (1.90, 3.70, 4.40), "pinnacle+30")]})
    app.state.twoup_feed = feed
    api = TestClient(app)
    assert post(api, [row(kickoff=ko, seen_at=now, rating=104.0),
                      row("Real Betis", "Sevilla", side="away", book_odds=3.0, rating=105.0,
                          n_odds=2.857, kickoff=ko, seen_at=now)]).status_code == 200
    feed.refresh(now)
    body = api.get("/2up/feed?value_only=true").json()
    rows = {c["home"]: c for c in body["candidates"]}
    both, bb = rows["Arsenal"], rows["Real Betis"]
    assert both["sources"] == ["bb", "pinnacle"] and both["p_win"] is not None
    assert both["bb_rating"] == 104.0 and both["value"] is True
    assert bb["sources"] == ["bb"] and (bb["p_win"], bb["rescue"]) == (None, None)
    assert bb["fair_price"] == 2.857 and bb["ev"] == pytest.approx(0.05)
    assert bb["event_id"] == event_id_for("Real Betis", "Sevilla", ko)
    assert bb["event_id"].startswith("bb:real betis|") and bb["event_id"].endswith(ko.date().isoformat())
    assert bb["bet365_priced_at"] is not None and bb["fair_priced_at"] is None
    assert both["fair_priced_at"] is not None
    assert both["priced_at"] == min(both["bet365_priced_at"], both["fair_priced_at"])
    assert body["status"]["bet365_source"] == "bb" and body["status"]["degraded"] is False


def test_kill_switch_empties_the_bb_feed(monkeypatch):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    feed, _ = _feed()
    app.state.twoup_feed = feed
    api = TestClient(app)
    post(api, [row("Real Betis", "Sevilla", kickoff=now + timedelta(hours=5), seen_at=now)])
    monkeypatch.setattr(settings, "twoup_kill_switch", True)
    body = api.get("/2up/feed").json()
    assert body["candidates"] == [] and body["status"]["killed"] is True


def test_diagnostics_report_the_tracker_and_the_mode_settings():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    boards = pinnacle_board(
        fixture("f8", "Real Madrid", "PSV", now + timedelta(hours=5), (1.30, 5.5, 9.0), "pinnacle+30"),
        fixture("f9", "Atletico Madrid", "PSV Eindhoven", now + timedelta(hours=5),
                (1.60, 4.0, 5.5), "pinnacle+30"))
    feed, _ = _feed(boards)
    app.state.twoup_feed = feed
    api = TestClient(app)
    ko = now + timedelta(hours=5)
    post(api, [row("Madrid", "PSV", kickoff=ko, seen_at=now),
               row("Real Betis", "Sevilla", kickoff=ko, seen_at=now),
               row("Leeds", "Hull", kickoff=ko, seen_at=now, updated_s=9999),
               row(side="draw", kickoff=ko, seen_at=now)])
    feed.refresh(now)
    diag = api.get("/2up/diagnostics").json()
    t = diag["tracker"]
    assert (t["rows_received"], t["bet365_rows"], t["not_kept"]["draw"]) == (4, 3, 1)
    assert (t["ambiguous"], t["unmatched"], t["dropped"]["row_too_old"]) == (1, 1, 1)
    assert t["fresh"] is True and t["age_s"] is not None
    s = diag["settings"]
    assert (s["twoup_bet365_source"], s["twoup_bb_min_rating"], s["twoup_bb_max_age_s"],
            s["twoup_bb_row_max_age_s"], s["twoup_pinnacle_max_age_s"]) == ("bb", 101.0, 300, 600, 5700)
    assert diag["feed"]["pinnacle"]["delay_s"] == 1800 and "modes_disagree" in t


def test_the_value_source_carries_a_bb_row():
    feed, _ = _feed()
    _rows(feed, [row("Real Betis", "Sevilla", book_odds=3.0, rating=105.0),
                 row(book_odds=1.80, rating=96.0)])
    feed.refresh(NOW)
    out, note = value_rows(feed, 0.0, NOW)
    [r] = out
    assert note is None and r.home == "Real Betis" and r.edge == pytest.approx(0.05)
    assert r.edge == pytest.approx(r.odds / r.fair - 1)


def test_the_value_source_says_when_the_tracker_is_too_old():
    feed, _ = _feed()
    _rows(feed, [row("Real Betis", "Sevilla", book_odds=3.0, rating=105.0)])
    feed.refresh(NOW)
    out, note = value_rows(feed, 0.0, NOW + timedelta(hours=1))
    assert out == [] and note.startswith("cannot price: ALL — the Bookie Bashing tracker")


def test_routes_the_365_grant_cannot_post():
    app.dependency_overrides[user_permissions] = lambda: "accabonus"
    try:
        # A session is never the ingest grant: the post is key-only.
        assert TestClient(app).post("/2up/bb", json={"prices": []}).status_code == 401
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


# --- review fixes, 18 Nov 2026 ------------------------------------------------------------

def test_a_row_whose_selection_is_not_its_side_is_dropped_and_counted():
    api = TestClient(app)
    feed, _ = _feed()
    app.state.twoup_feed = feed
    r = post(api, [live_row(selection="Chelsea"),                       # side home, Chelsea away
                   live_row(side="away", selection="Arsenal"),
                   live_row(selection="Madrid", home="Real Madrid", away="Atletico Madrid"),
                   live_row("Leeds", "Hull")])
    assert r.status_code == 200
    assert r.json()["kept"] == 1 and r.json()["not_kept"]["selection does not match side"] == 3
    assert [x.home for x in feed.tracker.snapshot().rows] == ["Leeds"]


def test_a_row_read_ahead_of_renders_clock_is_dropped_and_a_post_of_only_those_is_422():
    api = TestClient(app)
    feed, _ = _feed()
    app.state.twoup_feed = feed
    ahead = datetime.now(timezone.utc) + timedelta(seconds=180)
    r = post(api, [live_row(seen_at=ahead), live_row("Leeds", "Hull")])
    assert r.status_code == 200 and r.json()["not_kept"]["seen_at in the future"] == 1
    assert r.json()["kept"] == 1
    bad = post(api, [live_row(seen_at=ahead), live_row("Leeds", "Hull", seen_at=ahead)])
    assert bad.status_code == 422 and "ahead of Render's clock" in bad.json()["detail"]
    # The good post stands, and the next honest post is not wedged behind the bad one.
    assert post(api, [live_row("Wigan", "Bolton")]).status_code == 200


def test_a_reversed_fixture_is_never_joined():
    feed, _ = _feed({"pinnacle+30": [
        fixture("f1", "Chelsea", "Arsenal", KO, (1.90, 3.70, 4.40), "pinnacle+30")]})
    _rows(feed, [row()])
    [c] = feed.refresh(NOW)
    assert c.p_win is None and feed.last_pass["unmatched"] == 1


def test_a_fixture_matching_both_ways_round_is_ambiguous():
    feed, _ = _feed({"pinnacle+30": [
        fixture("f1", "Manchester United", "Manchester City", KO, (2.4, 3.5, 2.9), "pinnacle+30")]})
    _rows(feed, [row("Man Utd", "Man City", book_odds=2.6, rating=102.0)])
    [c] = feed.refresh(NOW)
    assert c.p_win is None and feed.last_pass["ambiguous"] == 1


def test_youth_and_womens_sides_never_join_the_senior_fixture():
    feed, _ = _feed(pinnacle_board(
        fixture("f7", "Arsenal U21", "Chelsea U21", KO, (2.2, 3.6, 3.0), "pinnacle+30")))
    _rows(feed, [row("Arsenal Women", "Chelsea Women", rating=102.0),
                 row("Arsenal", "Chelsea", rating=102.0)])
    rows = {c.home: c for c in feed.refresh(NOW)}
    assert rows["Arsenal Women"].p_win is None
    assert rows["Arsenal"].p_win == pytest.approx(model.fair_1x2(1.90, 3.70, 4.40)[0])
    assert (feed.last_pass["matched"], feed.last_pass["unmatched"]) == (1, 1)


def test_a_snapshot_delivered_late_is_as_old_as_what_it_carries():
    feed, _ = _feed()
    _rows(feed, [row(seen_at=NOW - timedelta(seconds=400), updated_s=0)], NOW)   # received now
    feed.refresh(NOW)
    st = feed.status(NOW)
    assert st["cannot_price"][0].startswith("ALL — the Bookie Bashing tracker snapshot is 400s old")
    assert feed.served(NOW)[1] == []


def test_a_pinnacle_side_too_old_gives_no_pinnacle_verdict_and_is_counted():
    feed, client = _feed()
    client.fetched_at = lambda ids, book: NOW - timedelta(minutes=66)   # true 96 minutes ago
    _rows(feed, [row(rating=104.0)])
    [c] = feed.refresh(NOW)
    assert c.p_win is None and c.fair_priced_at is None
    assert model.flagged_by(c, feed.params) == ["bb"]
    assert feed.last_pass["pinnacle_too_old"] == 1


def test_a_stalled_pinnacle_refresh_is_said_even_after_a_post_composes_the_board():
    feed, _ = _feed()
    _rows(feed, [row()])
    feed.refresh(NOW)                                   # Pinnacle true at NOW - 30 min
    later = NOW + timedelta(minutes=70)                 # no refresh since: 100 minutes
    _rows(feed, [row(seen_at=later)], later)            # a post composes a fresh board
    st = feed.status(later)
    assert st["served_at"] == later.isoformat()
    stalled = [c for c in st["cannot_price"] if "refresh has stalled" in c]
    assert stalled and stalled[0].startswith("pinnacle mode: ")
    assert not any(c.startswith("ALL") for c in st["cannot_price"])
    assert st["pinnacle"]["newest_age_s"] == 6000
    [c] = feed.served(later)[1]
    assert c.p_win is None                              # no verdict off the stale line


def test_rows_the_modes_disagree_on_are_counted_and_left_as_they_are():
    feed, _ = _feed()
    _rows(feed, [row(rating=98.0)])                     # Pinnacle flags it, BB rates it below 100
    [c] = feed.refresh(NOW)
    assert model.flagged_by(c, feed.params) == ["pinnacle"]
    assert model.passes_filters(c, feed.params) is None  # still value: Callum's call
    assert feed.last_pass["modes_disagree"] == 1
    assert feed.tracker_status(NOW)["modes_disagree"] == 1


@pytest.mark.parametrize("rows, says", [
    ([], "0 received"),
    ([row(updated_s=None), row("Leeds", "Hull", updated_s=None)], "unknown_age 2"),
    ([row(updated_s=900)], "row_too_old 1"),
])
def test_a_snapshot_with_no_usable_bet365_row_is_an_all_gap(rows, says):
    feed, _ = _feed()
    _rows(feed, rows)
    feed.refresh(NOW)
    st = feed.status(NOW)
    assert st["cannot_price"][0].startswith("ALL — the Bookie Bashing tracker snapshot has no "
                                            "usable bet365 row")
    assert says in st["cannot_price"][0] and st["degraded"] is True


def test_usable_rows_all_outside_the_horizon_are_an_honest_quiet_board():
    feed, _ = _feed()
    _rows(feed, [row(kickoff=NOW + timedelta(hours=30))])
    assert feed.refresh(NOW) == []
    st = feed.status(NOW)
    assert not any(c.startswith("ALL") for c in st["cannot_price"])
    assert feed.last_pass["dropped"]["out_of_horizon"] == 1


def test_a_post_racing_a_refresh_lands_in_the_board_and_time_never_goes_back():
    """A post arrives while the job is fetching Pinnacle. Both compose under one
    lock, each reading the snapshot and the clock inside it, so the board left
    holds the post's row and its time is the later one."""
    now = datetime.now(timezone.utc)
    ko = now + timedelta(hours=5)
    feed, client = _feed({"pinnacle+30": [
        fixture("f1", "Arsenal", "Chelsea", ko, (1.90, 3.70, 4.40), "pinnacle+30")]})
    client.fetched_at = lambda ids, book: now
    real = client.odds_by_tournaments
    posted = []

    def fetch_with_a_post_arriving(ids, bookmaker="x"):
        t = threading.Thread(target=lambda: posted.append(
            _rows(feed, [row(kickoff=ko, seen_at=datetime.now(timezone.utc))], None)))
        t.start()
        t.join()
        return real(ids, bookmaker=bookmaker)
    client.odds_by_tournaments = fetch_with_a_post_arriving
    feed.refresh()                                      # the job's call: no explicit time
    first_at = feed.served()[0]
    [c] = feed.served()[1]
    assert posted and posted[0][0] is not None
    assert c.p_win is not None                          # the post's row, priced by the refresh
    feed.recompose()
    assert feed.served()[0] >= first_at
