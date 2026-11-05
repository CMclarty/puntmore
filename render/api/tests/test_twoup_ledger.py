"""2-Up ledger: client scoping, idempotency on bet_id, reports, exposure, and
the dry-run / kill-switch gates on what the Hub endpoint may send."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.database import Base
from app.models import TwoUpAccount, TwoUpBalance, TwoUpBet, TwoUpClient
from app.twoup import ledger

from tests.twoup_helpers import Session, engine

SCHEMA = Path(__file__).resolve().parents[3] / "hub" / "schemas" / "protocol.schema.json"


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    s = Session()
    ledger.ensure_clients(s, "owner:owner=acc-1+acc-2;dave=acc-3")
    yield s
    s.close()
    Base.metadata.drop_all(bind=engine)


def _bet(bid, account="acc-1", stake=10.0, price=2.1, hours=4, scope="shared", event="ev-1",
         selection="Arsenal"):
    now = datetime.now(timezone.utc)
    return {"bet_id": bid, "account_id": account,
            "legs": [{"event_id": event, "market": "Match Result", "selection": selection,
                      "price": price}],
            "stake": stake, "expected_price": price, "bet_type": "single",
            "not_before": now.isoformat(), "not_after": (now + timedelta(hours=hours)).isoformat(),
            "source_scope": scope}


def _live(db, cid="owner"):
    db.get(TwoUpClient, cid).dry_run = False
    db.commit()


def _ts(minutes=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


# --- clients ------------------------------------------------------------------

def test_seeded_clients_start_dry_run_and_are_never_modified(db):
    c = db.get(TwoUpClient, "owner")
    assert c.dry_run is True and c.kill_switch is False and c.is_owner is True
    assert db.get(TwoUpClient, "dave").is_owner is False
    assert [a.account_id for a in ledger.accounts(db, "owner")] == ["acc-1", "acc-2"]
    c.dry_run, c.bankroll = False, 500
    db.commit()
    assert ledger.ensure_clients(db, "owner:owner=acc-1+acc-2+acc-9") == 1   # only the new account
    db.refresh(c)
    assert c.dry_run is False and c.bankroll == 500


def test_parse_clients_spec():
    assert ledger.parse_clients(" a:owner = x+y ; b=z;;") == [("a", True, ["x", "y"]),
                                                              ("b", False, ["z"])]


# --- record / pending / mark_sent --------------------------------------------

def test_record_is_idempotent_on_bet_id(db):
    ledger.record_bet_list(db, "owner", [_bet("b1")])
    ledger.record_bet_list(db, "owner", [_bet("b1", stake=99)])
    rows = db.query(TwoUpBet).all()
    assert len(rows) == 1 and rows[0].stake == 10.0 and rows[0].status == "queued"


def test_record_refuses_foreign_accounts_and_owner_bets_for_others(db):
    ledger.record_bet_list(db, "dave", [_bet("x1", account="acc-1"),
                                        _bet("x2", account="acc-3", scope="owner"),
                                        _bet("x3", account="acc-3")])
    assert [b.bet_id for b in db.query(TwoUpBet).all()] == ["x3"]
    with pytest.raises(ValueError):
        ledger.record_bet_list(db, "nobody", [_bet("y")])


def test_dry_run_records_but_sends_nothing(db):
    ledger.record_bet_list(db, "owner", [_bet("b1")])
    assert db.get(TwoUpBet, "b1").dry_run is True
    assert ledger.pending_bet_lists(db, "owner") == []


def test_pending_lists_are_protocol_bets_grouped_by_list(db):
    _live(db)
    ledger.record_bet_list(db, "owner", [_bet("b1"), _bet("b2", account="acc-2")])
    ledger.record_bet_list(db, "owner", [_bet("b3", event="ev-2")])
    lists = ledger.pending_bet_lists(db, "owner")
    assert [[b["bet_id"] for b in lst["bets"]] for lst in lists] == [["b1", "b2"], ["b3"]]
    bet = lists[0]["bets"][0]
    assert set(bet) == {"bet_id", "account_id", "legs", "stake", "expected_price", "bet_type",
                        "not_before", "not_after", "source_scope"}
    assert bet["not_before"].endswith("+00:00")
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    for lst in lists:
        jsonschema.validate({"type": "bet_list", "client_id": "owner", "msg_id": "m" * 10,
                             "ts": _ts(), "body": {"bets": lst["bets"]}}, schema)


def test_kill_switches_stop_pending(db, monkeypatch):
    _live(db)
    ledger.record_bet_list(db, "owner", [_bet("b1")])
    ledger.set_kill(db, "owner", True)
    assert ledger.pending_bet_lists(db, "owner") == []
    ledger.set_kill(db, "owner", False)
    assert len(ledger.pending_bet_lists(db, "owner")) == 1
    monkeypatch.setattr(settings, "twoup_kill_switch", True)
    assert ledger.pending_bet_lists(db, "owner") == []


def test_expired_queued_bets_are_not_pending_nor_exposure(db):
    _live(db)
    ledger.record_bet_list(db, "owner", [_bet("old", hours=-1), _bet("new", event="ev-2")])
    assert [b["bet_id"] for lst in ledger.pending_bet_lists(db, "owner") for b in lst["bets"]] == ["new"]
    assert ledger.open_exposure(db, "owner", "acc-1") == 10.0


def test_mark_sent_is_scoped_and_only_moves_queued(db):
    _live(db)
    ledger.record_bet_list(db, "owner", [_bet("b1"), _bet("b2", event="ev-2")])
    ledger.record_bet_list(db, "dave", [_bet("d1", account="acc-3")])
    ledger.mark_sent(db, "dave", ["b1"])                      # another client's id: no-op
    assert db.get(TwoUpBet, "b1").status == "queued"
    ledger.mark_sent(db, "owner", ["b1", "d1"])
    assert db.get(TwoUpBet, "b1").status == "sent" and db.get(TwoUpBet, "d1").status == "queued"
    assert [b["bet_id"] for lst in ledger.pending_bet_lists(db, "owner") for b in lst["bets"]] == ["b2"]


# --- reports ------------------------------------------------------------------

def test_placed_then_settled_and_replays_are_no_ops(db):
    ledger.record_bet_list(db, "owner", [_bet("b1")])
    ledger.mark_sent(db, "owner", ["b1"])
    placed = {"kind": "placed", "ts": _ts(), "bet_id": "b1", "actual_price": 2.05, "dry_run": True}
    ledger.apply_report(db, "owner", placed)
    ledger.apply_report(db, "owner", {**placed, "actual_price": 9.9})       # replay
    b = db.get(TwoUpBet, "b1")
    assert (b.status, b.actual_price, b.hub_dry_run) == ("placed", 2.05, True)
    settled = {"kind": "settled", "ts": _ts(), "bet_id": "b1", "outcome": "won", "return": 20.5}
    ledger.apply_report(db, "owner", settled)
    ledger.apply_report(db, "owner", {**settled, "outcome": "lost", "return": 0})
    db.refresh(b)
    assert (b.status, b.outcome, b.return_amount) == ("settled", "won", 20.5)
    assert ledger.open_exposure(db, "owner", "acc-1") == 0.0


def test_failed_is_terminal_and_never_resent(db):
    _live(db)
    ledger.record_bet_list(db, "owner", [_bet("b1")])
    ledger.mark_sent(db, "owner", ["b1"])
    ledger.apply_report(db, "owner", {"kind": "failed", "ts": _ts(), "bet_id": "b1",
                                      "reason": "outside window"})
    ledger.apply_report(db, "owner", {"kind": "placed", "ts": _ts(), "bet_id": "b1",
                                      "actual_price": 2.0, "dry_run": False})
    ledger.record_bet_list(db, "owner", [_bet("b1")])          # re-recording cannot revive it
    b = db.get(TwoUpBet, "b1")
    assert (b.status, b.fail_reason) == ("failed", "outside window")
    assert ledger.pending_bet_lists(db, "owner") == []
    assert ledger.open_exposure(db, "owner", "acc-1") == 0.0


def test_price_changed_records_the_new_price(db):
    ledger.record_bet_list(db, "owner", [_bet("b1")])
    ledger.apply_report(db, "owner", {"kind": "price_changed", "ts": _ts(), "bet_id": "b1",
                                      "new_price": 1.95})
    b = db.get(TwoUpBet, "b1")
    assert (b.status, b.new_price) == ("price_changed", 1.95)


def test_a_report_for_another_clients_bet_is_ignored(db):
    ledger.record_bet_list(db, "owner", [_bet("b1")])
    ledger.apply_report(db, "dave", {"kind": "failed", "ts": _ts(), "bet_id": "b1", "reason": "x"})
    ledger.apply_report(db, "dave", {"kind": "placed", "ts": _ts(), "bet_id": "nope",
                                     "actual_price": 2.0, "dry_run": True})
    assert db.get(TwoUpBet, "b1").status == "queued"


def test_balances_keep_history_once_and_the_newest_wins(db):
    r = {"kind": "balance", "account_id": "acc-1", "balance": 250.0, "ts": _ts()}
    ledger.apply_report(db, "owner", r)
    ledger.apply_report(db, "owner", r)                                    # replay
    ledger.apply_report(db, "owner", {**r, "balance": 100.0, "ts": _ts(-30)})  # older, late
    acc = db.query(TwoUpAccount).filter_by(client_id="owner", account_id="acc-1").one()
    assert acc.balance == 250.0
    assert db.query(TwoUpBalance).count() == 2


def test_balance_for_an_unregistered_account_creates_it_disabled(db):
    ledger.apply_report(db, "owner", {"kind": "balance", "account_id": "acc-new",
                                      "balance": 40.0, "ts": _ts()})
    acc = db.query(TwoUpAccount).filter_by(client_id="owner", account_id="acc-new").one()
    assert acc.enabled is False and acc.balance == 40.0


def test_open_exposure_counts_live_bets_only(db):
    ledger.record_bet_list(db, "owner", [_bet("q", stake=5), _bet("s", stake=10, event="e2"),
                                         _bet("p", stake=20, event="e3"),
                                         _bet("f", stake=50, event="e4"),
                                         _bet("other", stake=7, account="acc-2", event="e5")])
    ledger.mark_sent(db, "owner", ["s", "p", "f"])
    ledger.apply_report(db, "owner", {"kind": "placed", "ts": _ts(), "bet_id": "p",
                                      "actual_price": 2, "dry_run": True})
    ledger.apply_report(db, "owner", {"kind": "failed", "ts": _ts(), "bet_id": "f", "reason": "x"})
    assert ledger.open_exposure(db, "owner", "acc-1") == 35.0
    assert ledger.open_exposure(db, "owner", "acc-2") == 7.0
    assert ledger.open_exposure(db, "dave", "acc-1") == 0.0


def test_pooled_bankroll_is_configured_or_the_sum_of_balances(db):
    for acc, bal in (("acc-1", 300), ("acc-2", 200)):
        ledger.apply_report(db, "owner", {"kind": "balance", "account_id": acc,
                                          "balance": bal, "ts": _ts()})
    c = db.get(TwoUpClient, "owner")
    assert ledger.pooled_bankroll(db, c) == 500
    c.bankroll = 2000
    assert ledger.pooled_bankroll(db, c) == 2000


def test_trailing_stakes_skip_unplaced_real_bets(db):
    _live(db)
    ledger.record_bet_list(db, "owner", [_bet("a", stake=10), _bet("b", stake=20, event="e2"),
                                         _bet("c", stake=50, event="e3")])
    ledger.apply_report(db, "owner", {"kind": "placed", "ts": _ts(), "bet_id": "a",
                                      "actual_price": 2, "dry_run": False})
    ledger.apply_report(db, "owner", {"kind": "failed", "ts": _ts(), "bet_id": "b", "reason": "x"})
    assert ledger.trailing_stakes(db, "owner", "acc-1", 10) == [10]
