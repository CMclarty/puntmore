"""2-Up decision: Kelly on the pooled bankroll, the split across accounts,
the account-preservation constraints, idempotent bet_ids, the kill switch and
dry-run, and the owner-only scope."""
from datetime import date, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.database import Base
from app.models import TwoUpBet, TwoUpClient
from app.twoup import decision, ledger
from app.twoup.model import Params

from tests.twoup_helpers import NOW, Session, cand, engine

P = Params(kelly_fraction=0.25, max_selection_fraction=0.02, min_edge=0.005,
           stake_hard_cap=200, stake_balance_fraction=0.10, initial_stake_cap=10,
           growth_min_history=5, stake_growth_cap=0.25, max_bets_per_account_day=20)
LONDON = ZoneInfo("Europe/London")


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    s = Session()
    ledger.ensure_clients(s, "owner:owner=acc-1+acc-2;dave=acc-3")
    for cid, acc in (("owner", "acc-1"), ("owner", "acc-2"), ("dave", "acc-3")):
        ledger.apply_report(s, cid, {"kind": "balance", "account_id": acc, "balance": 500.0,
                                     "ts": (NOW - timedelta(hours=1)).isoformat()})
    yield s
    s.close()
    Base.metadata.drop_all(bind=engine)


def _run(db, cands, client="owner", now=NOW, params=P):
    return decision.run_for_client(db, client, cands, now=now, params=params)


def test_kelly_on_the_pool_split_across_accounts_by_the_initial_cap(db):
    # payout 0.52 at 2.2: full Kelly 0.12, quarter 0.03, capped at 2% of £1,000
    # = £20; each new account may stake £10 until it has history.
    d = _run(db, [cand()])
    assert d.skipped is None and d.bankroll == 1000
    assert sorted((b["account_id"], b["stake"]) for b in d.bets) == [("acc-1", 10), ("acc-2", 10)]
    assert len({b["bet_id"] for b in d.bets}) == 2
    rows = db.query(TwoUpBet).all()
    assert len(rows) == 2 and all(r.status == "queued" and r.dry_run for r in rows)


def test_rerunning_the_decision_queues_nothing_new(db):
    first = _run(db, [cand()])
    again = _run(db, [cand()])
    assert again.bets == [] and again.dropped == {"already bet today": 1}
    assert db.query(TwoUpBet).count() == len(first.bets)
    assert decision.bet_id_for("owner", "acc-1", "e1", "Arsenal", NOW.date()) == \
        decision.bet_id_for("owner", "acc-1", "e1", "Arsenal", NOW.date())


def test_highest_edge_first_and_the_pool_bounds_the_total(db):
    c = db.get(TwoUpClient, "owner")
    c.bankroll = 100.0
    db.commit()
    # Strong: quarter Kelly 4.5% -> capped at 2% = £2. Weak: 0.66% = 66p -> £1.
    weak = cand(event_id="w", selection="Weak", p_win=0.47, rescue=0.02, price=2.1)
    strong = cand(event_id="s", selection="Strong", p_win=0.55, rescue=0.02, price=2.1)
    d = _run(db, [weak, strong])
    assert [(b["legs"][0]["selection"], b["stake"]) for b in d.bets] == [("Strong", 2), ("Weak", 1)]
    # Pool spent down to its last pound: nothing more is staked past it.
    c.bankroll = 4.0
    db.commit()
    more = _run(db, [cand(event_id=f"m{i}", selection=f"M{i}", p_win=0.6) for i in range(3)])
    assert sum(b["stake"] for b in more.bets) <= 4.0 - 3.0


def test_each_share_is_bounded_by_balance_less_open_exposure(db):
    ledger.record_bet_list(db, "owner", [{
        "bet_id": "old", "account_id": "acc-1", "stake": 497.0, "expected_price": 2.0,
        "legs": [{"event_id": "x", "market": "Match Result", "selection": "X", "price": 2.0}],
        "bet_type": "single", "not_before": NOW.isoformat(),
        "not_after": (NOW + timedelta(hours=3)).isoformat(), "day": NOW.date().isoformat()}])
    d = _run(db, [cand()])
    stakes = {b["account_id"]: b["stake"] for b in d.bets}
    assert stakes["acc-1"] == 2            # £3 left on the account -> the £2 rung
    assert stakes["acc-2"] == 10


def test_twenty_bets_per_account_per_day_hard_max(db):
    c = db.get(TwoUpClient, "owner")
    c.bankroll = 100_000.0
    db.commit()
    cands = [cand(event_id=f"e{i}", selection=f"T{i}") for i in range(30)]
    d = _run(db, cands, params=P.but(max_bets_per_account_day=3))
    per = {}
    for b in d.bets:
        per[b["account_id"]] = per.get(b["account_id"], 0) + 1
    assert per == {"acc-1": 3, "acc-2": 3}
    assert d.dropped.get("no account can take it") == 30 - 3   # 3 events each fit on both
    again = _run(db, [cand(event_id="late", selection="Late")], params=P.but(max_bets_per_account_day=3))
    assert again.bets == []


def test_growth_cap_follows_trailing_stakes_not_results(db):
    c = db.get(TwoUpClient, "owner")
    c.bankroll = 100_000.0
    db.commit()
    day = NOW.date() - timedelta(days=1)
    hist = [{"bet_id": f"h{i}", "account_id": "acc-1", "stake": 4.0, "expected_price": 2.0,
             "legs": [{"event_id": f"h{i}", "market": "Match Result", "selection": "H",
                       "price": 2.0}], "bet_type": "single", "day": day.isoformat()}
            for i in range(5)]
    ledger.record_bet_list(db, "owner", hist)
    for i in range(5):                     # all won: must not lift the cap
        ledger.apply_report(db, "owner", {"kind": "settled", "ts": NOW.isoformat(),
                                          "bet_id": f"h{i}", "outcome": "won", "return": 8.0})
    d = _run(db, [cand()])
    stakes = {b["account_id"]: b["stake"] for b in d.bets}
    assert stakes["acc-1"] == 5            # mean 4 x 1.25 = 5
    assert stakes["acc-2"] == 10           # no history: the initial cap


def test_dynamic_ceiling_from_the_account_balance(db):
    db.get(TwoUpClient, "owner").bankroll = 1000.0      # keep the Kelly total at £20
    db.commit()
    ledger.apply_report(db, "owner", {"kind": "balance", "account_id": "acc-1",
                                      "balance": 30.0, "ts": NOW.isoformat()})
    d = _run(db, [cand()])
    # acc-2 (more room) takes £10; acc-1 may stake 10% of £30 = £3 -> the £2 rung.
    assert {b["account_id"]: b["stake"] for b in d.bets} == {"acc-2": 10, "acc-1": 2}


def test_outside_the_window_nothing_is_decided(db):
    early = NOW.astimezone(LONDON).replace(hour=7, minute=0).astimezone(timezone.utc)
    late = NOW.astimezone(LONDON).replace(hour=22, minute=5).astimezone(timezone.utc)
    for when in (early, late):
        d = _run(db, [cand(kickoff=when + timedelta(hours=5))], now=when)
        assert d.skipped == "outside betting window" and d.bets == []


def test_placement_window_clips_to_kickoff_and_window_end(db):
    late_ko = NOW.astimezone(LONDON).replace(hour=23, minute=0).astimezone(timezone.utc)
    d = _run(db, [cand(kickoff=late_ko), cand(event_id="soon", selection="Soon",
                                               kickoff=NOW + timedelta(minutes=10))])
    nb = {b["legs"][0]["selection"]: b for b in d.bets}
    assert "Soon" not in nb and d.dropped["no placement time inside the window"] == 1
    b = nb["Arsenal"]
    assert b["not_before"] == NOW.isoformat()
    end = NOW.astimezone(LONDON).replace(hour=22, minute=0).astimezone(timezone.utc)
    assert b["not_after"] == end.isoformat()


def test_kill_switches(db, monkeypatch):
    ledger.set_kill(db, "owner", True)
    assert _run(db, [cand()]).skipped == "client kill switch on"
    ledger.set_kill(db, "owner", False)
    monkeypatch.setattr(settings, "twoup_kill_switch", True)
    assert _run(db, [cand()]).skipped == "global kill switch on"
    assert db.query(TwoUpBet).count() == 0


def test_dry_run_decides_and_records_but_leaves_nothing_to_send(db):
    d = _run(db, [cand()])
    assert d.dry_run and d.bets
    assert ledger.pending_bet_lists(db, "owner") == []
    db.get(TwoUpClient, "owner").dry_run = False
    db.commit()
    d = _run(db, [cand(event_id="e2", selection="Other")])
    sent = [b["bet_id"] for lst in ledger.pending_bet_lists(db, "owner") for b in lst["bets"]]
    assert sorted(sent) == sorted(b["bet_id"] for b in d.bets)


def test_a_failed_selection_is_never_rebet_but_a_repriced_one_is(db):
    first = _run(db, [cand()])
    by_acc = {b["account_id"]: b["bet_id"] for b in first.bets}
    ledger.apply_report(db, "owner", {"kind": "failed", "ts": NOW.isoformat(),
                                      "bet_id": by_acc["acc-1"], "reason": "x"})
    ledger.apply_report(db, "owner", {"kind": "price_changed", "ts": NOW.isoformat(),
                                      "bet_id": by_acc["acc-2"], "new_price": 2.15})
    assert _run(db, [cand()]).bets == []                      # one failed: the selection is done

    db.query(TwoUpBet).filter(TwoUpBet.bet_id == by_acc["acc-1"]).delete()
    db.commit()
    again = _run(db, [cand()])
    assert again.bets and all(b["bet_id"].endswith("-r1") for b in again.bets)
    assert all(b["expected_price"] == 2.15 for b in again.bets)   # the lower, reported price


def test_owner_candidates_only_reach_the_owner(db):
    tip = cand(event_id="t", selection="Tip", scope="owner")
    assert _run(db, [tip], client="dave").dropped == {"owner-only candidate": 1}
    d = _run(db, [tip], client="owner")
    assert d.bets and all(b["source_scope"] == "owner" for b in d.bets)


def test_extra_candidates_is_empty_without_bet_finder_tips(db):
    assert decision.extra_candidates(db, "owner") == []


def test_filters_drop_with_a_reason(db):
    d = _run(db, [cand(price=1.1, p_win=0.95), cand(event_id="n", p_win=0.4)])
    assert d.bets == [] and d.dropped == {"price outside band": 1, "edge below floor": 1}


def test_unknown_client_and_no_accounts(db):
    assert _run(db, [cand()], client="ghost").skipped == "unknown client"
    ledger.ensure_clients(db, "empty=")
    assert _run(db, [cand()], client="empty").skipped == "no enabled accounts"


def test_decided_bets_validate_against_the_hub_protocol(db):
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from pathlib import Path
    schema = json.loads((Path(__file__).resolve().parents[3] / "hub" / "schemas"
                         / "protocol.schema.json").read_text())
    d = _run(db, [cand()])
    jsonschema.validate({"type": "bet_list", "client_id": "owner", "msg_id": "x" * 12,
                         "ts": NOW.isoformat(), "body": {"bets": d.bets}}, schema)
    assert isinstance(d.day, date)
