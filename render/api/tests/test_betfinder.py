"""Bet Finder orchestrator: the source registry, the pipeline (normalise,
dedupe, match, price, quarantine), extra_candidates (owner-only, stake
modes), source tracking off the ledger, the routes and the scheduled pass.
Same stubs as the 2-Up tests: an in-memory DB and a fake OddsPapi client."""
import json
from datetime import timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.betfinder import candidates as bf_candidates
from app.betfinder import pipeline, sources, stats
from app.config import settings
from app.database import Base, get_db
from app.hub import service
from app.main import app
from app.models import BetFinderSource, Tip, TwoUpBet
from app.security import user_permissions
from app.twoup import decision, ledger
from app.twoup.feed import TwoUpFeed
from app.twoup.model import Params

from tests.twoup_helpers import NOW, FakeOddsPapi, Session, engine, fixture, standard_boards

P = Params(kelly_fraction=0.25, max_selection_fraction=0.02, min_edge=0.005,
           stake_hard_cap=200, stake_balance_fraction=0.10, initial_stake_cap=10,
           growth_min_history=5, stake_growth_cap=0.25, max_bets_per_account_day=20)


@pytest.fixture
def db(monkeypatch):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(settings, "twoup_tournament_ids", "17")
    monkeypatch.setattr(settings, "oddspapi_fair_book", "pinnacle")
    monkeypatch.setattr(settings, "hub_owner_client_id", "owner")
    monkeypatch.setattr(settings, "betfinder_unit_stake", 5.0)
    monkeypatch.setattr(settings, "betfinder_min_edge", 0.0)
    s = Session()
    ledger.ensure_clients(s, "owner:owner=acc-1+acc-2;dave=acc-3")
    for cid, acc in (("owner", "acc-1"), ("owner", "acc-2"), ("dave", "acc-3")):
        ledger.apply_report(s, cid, {"kind": "balance", "account_id": acc, "balance": 500.0,
                                     "ts": (NOW - timedelta(hours=1)).isoformat()})
    yield s
    s.close()
    Base.metadata.drop_all(bind=engine)


def _feed(boards=None):
    client = FakeOddsPapi(boards if boards is not None else standard_boards())
    feed = TwoUpFeed(client, params=Params())
    return feed, client


_n = [0]


def hub_tip(db, *, source="sharp", event="Arsenal v Chelsea", market="Match Result",
            selection="Arsenal", tip_time=None, units=None, client="owner", expiry=None,
            tip_id=None, advised_price=2.1):
    _n[0] += 1
    tid = tip_id or f"tip-{_n[0]:08d}"
    tt = (tip_time or NOW - timedelta(hours=1)).isoformat()
    body = {"tip_id": tid, "source_id": source, "event": event, "market": market,
            "selection": selection, "advised_price": advised_price,
            "advised_stake_units": units, "tip_time": tt, "expiry": expiry,
            "origin": "tipster", "source_scope": "owner", "extra": {"trust_weight": 0.99}}
    service._on_tip(db, client, body)      # the endpoint's own write: the one `tips` row
    db.commit()
    return tid


def activate(db, name="sharp", trust=1.0, mode="auto"):
    sources.get_or_create(db, name)
    db.commit()
    sources.update(db, name, active=True, trust_weight=trust, stake_mode=mode)


def priced(db, feed=None, now=NOW):
    feed = feed or _feed()[0]
    pipeline.ingest(db, now)
    return pipeline.price_open(db, feed.candidates(now), now)


def tip_row(db, tid) -> Tip:
    return db.query(Tip).filter(Tip.tip_id == tid).one()


# --- source registry ----------------------------------------------------------

def test_sources_seed_from_the_setting_and_never_modify_a_row(db):
    spec = "Sharp:trust=0.6,active=1,stake=advised;Other;bad:trust=7,stake=yolo"
    assert sources.ensure_sources(db, spec) == 3
    rows = {s.name: s for s in sources.all_sources(db)}
    assert (rows["sharp"].trust_weight, rows["sharp"].active, rows["sharp"].stake_mode) == \
        (0.6, True, "advised")
    assert (rows["other"].trust_weight, rows["other"].active, rows["other"].stake_mode,
            rows["other"].kind) == (0.5, False, "auto", "hub")
    # Unreadable values fall back to the defaults, never to the bad value.
    assert (rows["bad"].trust_weight, rows["bad"].stake_mode) == (0.5, "auto")
    sources.update(db, "sharp", trust_weight=0.9)
    assert sources.ensure_sources(db, "sharp:trust=0.1,active=0") == 0
    assert db.get(BetFinderSource, "sharp").trust_weight == 0.9


def test_a_tip_from_an_unknown_source_creates_it_inactive_at_half_trust(db):
    hub_tip(db, source="New Tipster")
    assert pipeline.ingest(db, NOW)["sources_created"] == 1
    src = db.get(BetFinderSource, "new tipster")
    assert (src.active, src.trust_weight, src.kind) == (False, 0.5, "hub")
    # The tip's own extra.trust_weight (0.99) is untrusted and ignored.


def test_source_names_are_stripped_of_control_characters():
    assert sources.source_name("  Evil\nName\x1b[31m ") == "evilname31m"
    assert len(sources.source_name("x" * 500)) == sources.NAME_MAX


# --- matching and pricing -----------------------------------------------------

def test_a_match_result_tip_is_priced_off_the_2up_feed(db):
    feed, _ = _feed()
    tid = hub_tip(db, event="Arsenal FC vs Chelsea", selection="Arsenal to win")
    priced(db, feed)
    t = tip_row(db, tid)
    [c] = [c for c in feed.candidates(NOW) if c.event_id == "f1" and c.side == "home"]
    assert (t.status, t.event_id, t.side, t.selection, t.price) == \
        ("priced", "f1", "home", "Arsenal", 2.05)
    assert t.p_win == pytest.approx(c.p_win) and t.fair_price == pytest.approx(1 / c.p_win)
    assert t.edge == pytest.approx(c.p_win - 1 / 2.05)     # rescue 0
    assert t.match_score >= 0.72


def test_the_away_side_resolves_through_the_shared_side_reader(db):
    tid = hub_tip(db, event="Arsenal v Chelsea FC", selection="Chelsea")
    priced(db)
    t = tip_row(db, tid)
    assert (t.side, t.selection, t.price) == ("away", "Chelsea", 4.00)


@pytest.mark.parametrize("kw,reason", [
    ({"market": "Both Teams To Score", "selection": "Yes"}, "market_not_priced"),
    ({"market": "Over/Under 2.5"}, "market_not_priced"),
    ({"selection": "Draw"}, "draw_not_priced"),
    ({"selection": "Tottenham"}, "selection_unresolved"),
    ({"event": "Arsenal to beat everyone"}, "event_unreadable"),
    ({"event": "Wigan v Bolton", "selection": "Wigan"}, "no_fixture"),   # no bet365 side
    ({"event": "Barnsley v Rotherham", "selection": "Barnsley"}, "no_fixture"),
    ({"client": "dave"}, "not_owner_hub"),
    ({"units": -1}, "bad_stake_units"),
    ({"units": 500}, "bad_stake_units"),
])
def test_anything_not_priced_exactly_is_quarantined_never_guessed(db, kw, reason):
    tid = hub_tip(db, **kw)
    priced(db)
    t = tip_row(db, tid)
    assert (t.status, t.quarantine) == ("quarantined", reason)
    assert t.price is None and t.event_id is None
    activate(db, t.source)
    assert bf_candidates.extra_candidates(db, "owner", now=NOW) == []


def test_a_fixture_must_kick_off_after_the_tip_and_inside_the_match_window(db):
    late = hub_tip(db, tip_time=NOW + timedelta(hours=6))         # after kickoff
    early = hub_tip(db, source="b", tip_time=NOW - timedelta(days=5))
    priced(db)
    assert tip_row(db, late).quarantine == "no_fixture"
    assert tip_row(db, early).quarantine == "no_fixture"


def test_an_unmatched_tip_is_retried_until_its_window_ends(db):
    tid = hub_tip(db, event="Wigan v Bolton", selection="Bolton")
    priced(db)
    assert tip_row(db, tid).quarantine == "no_fixture"
    boards = standard_boards()
    ko = NOW + timedelta(hours=5)
    boards["bet365"].append(fixture("f3", "Wigan", "Bolton", ko, (2.6, 3.2, 3.1), "bet365"))
    pipeline.price_open(db, _feed(boards)[0].candidates(NOW), NOW)
    t = tip_row(db, tid)
    assert (t.status, t.event_id, t.side) == ("priced", "f3", "away")
    # A final reason is never retried.
    bad = hub_tip(db, selection="Draw")
    priced(db)
    pipeline.price_open(db, _feed(boards)[0].candidates(NOW), NOW)
    assert tip_row(db, bad).quarantine == "draw_not_priced"


def test_a_degraded_empty_feed_quarantines_nothing(db):
    tid = hub_tip(db)
    pipeline.ingest(db, NOW)
    pipeline.price_open(db, [], NOW, degraded=True)
    pipeline.price_open(db, None, NOW)
    assert tip_row(db, tid).status == "new"


def test_expiry_and_kickoff_expire_an_open_tip(db):
    gone = hub_tip(db, expiry=(NOW - timedelta(minutes=1)).isoformat())
    live = hub_tip(db, source="b")
    priced(db)
    assert tip_row(db, gone).status == "expired"
    assert tip_row(db, live).status == "priced"
    after = NOW + timedelta(hours=6)
    pipeline.price_open(db, [], after)
    assert tip_row(db, live).status == "expired"


# --- dedupe -------------------------------------------------------------------

def test_same_source_event_market_selection_and_day_is_one_tip(db):
    first = hub_tip(db, event="Arsenal v Chelsea")
    again = hub_tip(db, event="  ARSENAL  vs  Chelsea ", market="1X2",
                    tip_time=NOW - timedelta(minutes=30))
    other_day = hub_tip(db, tip_time=NOW - timedelta(days=1, hours=1))
    other_source = hub_tip(db, source="rival")
    counts = pipeline.ingest(db, NOW)
    assert counts["duplicate"] == 1
    assert (tip_row(db, again).status, tip_row(db, again).duplicate_of) == ("duplicate", first)
    assert tip_row(db, other_day).status == "new"
    assert tip_row(db, other_source).status == "new"
    # Ingest is once per hub tip: a second pass adds nothing.
    assert pipeline.ingest(db, NOW)["new"] == 0
    assert db.query(Tip).count() == 4


def test_two_sources_on_one_selection_make_one_candidate_the_more_trusted(db):
    a = hub_tip(db, source="a")
    b = hub_tip(db, source="b")
    priced(db)
    activate(db, "a", trust=0.4)
    activate(db, "b", trust=0.8)
    [c] = bf_candidates.extra_candidates(db, "owner", now=NOW)
    assert c.source == "b" and c.extra["tip_id"] == b
    assert set(c.extra["tip_ids"]) == {a, b} and c.extra["sources"] == ["a", "b"]


# --- extra_candidates: owner only, active, fresh ------------------------------

def test_candidates_are_owner_only_and_need_an_active_source(db):
    hub_tip(db)
    priced(db)
    assert bf_candidates.extra_candidates(db, "owner", now=NOW) == []   # inactive
    activate(db)
    [c] = bf_candidates.extra_candidates(db, "owner", now=NOW)
    assert (c.source_scope, c.source, c.rescue, c.market) == ("owner", "sharp", 0.0,
                                                                "Match Result")
    assert bf_candidates.extra_candidates(db, "dave", now=NOW) == []
    # The decision seam forwards it, and never to a non-owner.
    assert decision.extra_candidates(db, "owner", now=NOW) == [c]
    assert decision.extra_candidates(db, "dave", now=NOW) == []


def test_a_stale_price_is_not_offered(db):
    hub_tip(db)
    priced(db)
    activate(db)
    later = NOW + timedelta(minutes=settings.betfinder_max_price_age_minutes + 1)
    assert bf_candidates.extra_candidates(db, "owner", now=later) == []


def test_a_non_owner_ledger_client_named_owner_gets_nothing(db, monkeypatch):
    hub_tip(db, client="dave")
    monkeypatch.setattr(settings, "hub_owner_client_id", "dave")
    priced(db)
    activate(db)
    # dave sent it and is the configured owner id, but the ledger says dave is
    # not an owner client: nothing.
    assert bf_candidates.extra_candidates(db, "dave", now=NOW) == []


# --- stake modes --------------------------------------------------------------

@pytest.mark.parametrize("mode,units,applied", [
    ("auto", 2.0, "advised"), ("auto", None, "kelly"),
    ("advised", 2.0, "advised"), ("advised", None, "kelly"),
    ("kelly", 2.0, "kelly"),
])
def test_stake_mode_per_source_is_recorded_in_extra(db, mode, units, applied):
    hub_tip(db, units=units)
    priced(db)
    activate(db, mode=mode)
    [c] = bf_candidates.extra_candidates(db, "owner", now=NOW)
    assert c.extra["stake_mode"] == applied and c.extra["stake_mode_setting"] == mode
    if applied == "advised":
        assert c.extra["fixed_stake"] == 10.0                  # 2 units x £5
    else:
        assert "fixed_stake" not in c.extra
    if mode == "advised" and units is None:
        assert "no units" in c.extra["stake_mode_note"]


def test_advised_stake_is_units_times_unit_scaled_by_trust_and_capped(db):
    hub_tip(db, units=2.0)
    priced(db)
    activate(db, trust=0.5)
    [c] = bf_candidates.extra_candidates(db, "owner", now=NOW)
    assert decision.target_stake(c, 1000.0, P) == pytest.approx(5.0)   # 10 x 0.5
    # Never above the per-selection share of the pool.
    assert decision.target_stake(c, 100.0, P) == pytest.approx(1.0)    # 2% of 100 x 0.5


def test_kelly_mode_is_the_engines_kelly_scaled_by_trust(db):
    from app.twoup import model
    hub_tip(db)
    priced(db)
    activate(db, trust=0.5, mode="kelly")
    [c] = bf_candidates.extra_candidates(db, "owner", now=NOW)
    assert decision.target_stake(c, 1000.0, P) == pytest.approx(
        model.kelly_stake(c, 1000.0, P) * 0.5)


def test_the_decision_stakes_an_owner_tip_with_its_advised_units(db):
    hub_tip(db, units=1.0)
    priced(db)
    activate(db)
    d = decision.run_for_client(db, "owner", [], now=NOW, params=P)
    assert [(b["legs"][0]["selection"], b["stake"], b["source_scope"]) for b in d.bets] == \
        [("Arsenal", 5.0, "owner")]
    assert decision.run_for_client(db, "dave", [], now=NOW, params=P).bets == []


def test_a_tip_below_its_edge_floor_is_dropped(db):
    hub_tip(db, selection="Chelsea")            # bet365 4.00 under Pinnacle's fair
    priced(db)
    activate(db)
    d = decision.decide(db, "owner", [], now=NOW, params=P)
    assert d.bets == [] and d.dropped == {"edge below floor": 1}


def test_a_2up_row_and_a_tip_on_one_selection_are_bet_once(db):
    feed, _ = _feed()
    hub_tip(db, units=4.0)
    priced(db, feed)
    activate(db)
    d = decision.decide(db, "owner", feed.value(NOW), now=NOW, params=P)
    assert d.dropped.get("same selection twice") == 1
    assert {b["source_scope"] for b in d.bets} == {"shared"}     # the higher edge
    assert len({(b["legs"][0]["event_id"], b["legs"][0]["selection"]) for b in d.bets}) == 1


# --- source tracking ----------------------------------------------------------

def _settle(db, bet_ids, outcome, ret_per_pound):
    for bid in bet_ids:
        b = db.get(TwoUpBet, bid)
        ledger.apply_report(db, "owner", {"kind": "placed", "bet_id": bid,
                                          "actual_price": b.expected_price, "dry_run": True,
                                          "ts": NOW.isoformat()})
        ledger.apply_report(db, "owner", {"kind": "settled", "bet_id": bid, "outcome": outcome,
                                          "return": b.stake * ret_per_pound,
                                          "ts": (NOW + timedelta(hours=8)).isoformat()})


def test_a_settled_bet_is_recorded_against_its_source(db):
    tid = hub_tip(db, units=1.0)
    priced(db)
    activate(db)
    d = decision.run_for_client(db, "owner", [], now=NOW, params=P)
    assert pipeline.link_and_settle(db)["decided"] == 1
    t = tip_row(db, tid)
    assert t.status == "decided" and t.bet_id_list == [b["bet_id"] for b in d.bets]
    # Decided: not offered again.
    assert bf_candidates.extra_candidates(db, "owner", now=NOW) == []
    _settle(db, t.bet_id_list, "won", 2.05)
    assert pipeline.link_and_settle(db)["settled"] == 1
    t = tip_row(db, tid)
    assert (t.status, t.outcome, t.staked, t.returned) == ("settled", "won", 5.0, 10.25)

    # A second, losing tip from the same source (bet straight into the ledger).
    lose = hub_tip(db, event="Leeds v Hull", selection="Leeds", tip_id="tip-lose-0001")
    pipeline.ingest(db, NOW)
    pipeline.price_open(db, _feed()[0].candidates(NOW), NOW)
    ledger.record_bet_list(db, "owner", [{
        "bet_id": "bf-lose", "account_id": "acc-1", "stake": 5.0, "expected_price": 2.3,
        "legs": [{"event_id": "f2", "market": "Match Result", "selection": "Leeds",
                  "price": 2.3}], "bet_type": "single", "source_scope": "owner",
        "not_before": NOW.isoformat(), "not_after": (NOW + timedelta(hours=4)).isoformat()}])
    _settle(db, ["bf-lose"], "lost", 0.0)
    pipeline.link_and_settle(db)
    assert tip_row(db, lose).outcome == "lost"

    st = stats.source_stats(db)["sharp"]
    assert st["all"]["settled"] == 2 and st["all"]["strike_rate"] == 0.5
    assert st["all"]["roi"] == pytest.approx(((10.25 / 5 - 1) + (0 - 1)) / 2)
    assert st["rolling"]["settled"] == 2 and st["tips"]["settled"] == 2
    # Trust weight is never touched by the record.
    assert db.get(BetFinderSource, "sharp").trust_weight == 1.0


def test_a_failed_bet_leaves_the_tip_not_placed_and_out_of_the_stats(db):
    tid = hub_tip(db, units=1.0)
    priced(db)
    activate(db)
    d = decision.run_for_client(db, "owner", [], now=NOW, params=P)
    for b in d.bets:
        ledger.apply_report(db, "owner", {"kind": "failed", "bet_id": b["bet_id"],
                                          "reason": "x", "ts": NOW.isoformat()})
    pipeline.link_and_settle(db)
    pipeline.link_and_settle(db)
    assert tip_row(db, tid).status == "not_placed"
    assert stats.source_stats(db)["sharp"]["all"]["settled"] == 0


def test_the_rolling_window_is_the_most_recent_settled_tips(db, monkeypatch):
    monkeypatch.setattr(settings, "betfinder_stats_window", 1)
    for i, (outcome, ret) in enumerate((("lost", 0.0), ("won", 10.0))):
        db.add(Tip(
            tip_id=f"s-{i:06d}", client_id="owner", source_id="sharp", tip_time_raw="-",
            body="{}", source="sharp", event_raw="a v b",
            market_raw="1x2", selection_raw="a", market="match_result", dedupe_key=f"k{i}",
            status="settled", outcome=outcome, staked=5.0, returned=ret, bet_ids="[]",
            settled_at=NOW + timedelta(hours=i)))
    db.commit()
    st = stats.source_stats(db)["sharp"]
    assert st["all"]["strike_rate"] == 0.5 and st["rolling"]["strike_rate"] == 1.0


# --- routes, the pass, the scheduler ------------------------------------------

@pytest.fixture
def api(db):
    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    app.state.twoup_feed = None


def test_routes_list_edit_quarantine_stats_and_candidates(db, api):
    hub_tip(db, selection="Draw")
    good = hub_tip(db, source="sharp", units=1.0)
    feed, _ = _feed()
    feed.candidates(NOW)
    app.state.twoup_feed = feed
    body = api.post("/2up/betfinder/run").json()
    assert body["ingest"]["new"] == 2 and body["pricing"]["priced"] == 1

    [src] = api.get("/2up/betfinder/sources").json()
    assert (src["name"], src["active"], src["trust_weight"]) == ("sharp", False, 0.5)
    r = api.patch("/2up/betfinder/sources/sharp", json={"active": True, "trust_weight": 0.7})
    assert r.status_code == 200 and r.json()["active"] is True
    assert api.patch("/2up/betfinder/sources/sharp", json={"trust_weight": 1.5}).status_code == 422
    assert api.patch("/2up/betfinder/sources/sharp", json={"stake_mode": "yolo"}).status_code == 422
    assert api.patch("/2up/betfinder/sources/ghost", json={"active": True}).status_code == 404

    [q] = api.get("/2up/betfinder/quarantine").json()
    assert q["quarantine"] == "draw_not_priced" and q["quarantine_detail"]
    assert q["selection_raw"] == "Draw"
    tips = api.get("/2up/betfinder/tips", params={"status": "priced"}).json()
    assert [t["tip_id"] for t in tips] == [good]
    stats_body = api.get("/2up/betfinder/stats").json()
    assert stats_body["sharp"]["tips"] == {"priced": 1, "quarantined": 1}
    # The preview uses the real clock; NOW is tomorrow, so the tip is live.
    [c] = api.get("/2up/betfinder/candidates").json()
    assert c["source_scope"] == "owner" and c["extra"]["stake_mode"] == "advised"


def test_routes_need_the_365_grant(api):
    app.dependency_overrides[user_permissions] = lambda: "coupons"
    try:
        assert api.get("/2up/betfinder/sources").status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


def test_the_pass_never_makes_a_request(db):
    hub_tip(db)
    feed, client = _feed()
    out = pipeline.run_pass(db, feed, NOW)               # nothing cached yet
    assert out["pricing"].startswith("skipped") and client.calls == []
    assert pipeline.run_pass(db, None, NOW)["ingest"]["new"] == 0
    feed.candidates(NOW)
    n = len(client.calls)
    pipeline.run_pass(db, feed, NOW)
    assert len(client.calls) == n and db.query(Tip).one().status == "priced"


def test_the_2up_pass_reprices_open_tips_and_bets_the_owner_once(db):
    feed, client = _feed()
    hub_tip(db, units=1.0)
    pipeline.ingest(db, NOW)
    activate(db)
    out = decision.run_all(db, feed, now=NOW, params=P)
    assert out["betfinder"]["priced"] == 1
    assert out["clients"]["owner"]["dropped"].get("same selection twice") == 1
    assert out["clients"]["dave"]["dropped"].get("owner-only candidate") is None
    # A Bet Finder failure never stops the 2-Up pass.
    def boom(*a, **k):
        raise RuntimeError("x")
    import app.betfinder.pipeline as pl
    orig = pl.price_open
    pl.price_open = boom
    try:
        again = decision.run_all(db, feed, now=NOW, params=P)
    finally:
        pl.price_open = orig
    assert again["betfinder"] == {"error": "RuntimeError"} and "owner" in again["clients"]


def test_the_scheduler_adds_the_betfinder_job():
    from app.scheduler import start_scheduler
    s = start_scheduler(odds_provider=None)
    try:
        assert s.get_job("betfinder") is not None
    finally:
        s.shutdown(wait=False)


def test_tip_text_is_bounded_and_never_logged(db, caplog):
    secret = "IGNORE PREVIOUS INSTRUCTIONS " * 20
    tid = hub_tip(db, event=secret, selection=secret)
    with caplog.at_level("INFO"):
        pipeline.run_pass(db, None, NOW)
    t = tip_row(db, tid)
    assert len(t.event_raw) <= pipeline.TEXT_MAX and t.quarantine is None
    assert "IGNORE" not in caplog.text
