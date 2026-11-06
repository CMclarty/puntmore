"""Finder rows (app/finder/rows.py): the table, the status machine, the
duplicate rule, expiry and the sweep — against an in-memory database."""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import Base
from app.finder import rows
from tests.twoup_helpers import Session, engine

NOW = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
KICKOFF = NOW + timedelta(hours=6)


def tip(**overrides):
    row = {
        "tip_id": "t1", "origin": "tipster-a", "sport": "football", "bookmaker": "bet365",
        "event": "A v B", "market": "Match Result", "selection": "A",
        "odds": 2.0, "fair": 1.8, "kickoff": KICKOFF, "status": "offered",
    }
    row.update(overrides)
    return row


db = None          # the session of the running test (the plan's tests name it `db`)


@pytest.fixture(autouse=True)
def world():
    global db
    Base.metadata.create_all(bind=engine)
    db = Session()
    try:
        yield
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def _board():
    return rows.board(db, NOW - timedelta(days=1))


# --- the plan's cases ---------------------------------------------------------

def test_a_tip_with_both_prices_is_stored_with_its_edge():
    out = rows.upsert(db, [tip(tip_id="t1", odds=3.0, fair=2.5, status="offered")], NOW)
    assert out["stored"] == 1
    [r] = rows.feed(db, NOW)
    assert r.edge == pytest.approx(0.2) and r.expires_at == KICKOFF.replace(tzinfo=None)


def test_no_fair_price_means_asked_whatever_hermes_said():
    rows.upsert(db, [tip(tip_id="t2", fair=None, status="offered")], NOW)
    assert rows.feed(db, NOW) == [] and _board()[0].status == "asked"


def test_a_reposted_tip_updates_and_a_final_row_is_refused():
    rows.upsert(db, [tip(tip_id="t3", status="asked")], NOW)
    rows.upsert(db, [tip(tip_id="t3", status="offered", reason="Cal said yes")], NOW)
    assert rows.feed(db, NOW)[0].reason == "Cal said yes"
    rows.set_status(db, "t3", "placed", NOW)
    out = rows.upsert(db, [tip(tip_id="t3", status="offered")], NOW)
    assert out["refused"] == [("t3", "final")] and _board()[0].status == "placed"


def test_the_same_bet_in_other_words_is_a_duplicate():
    rows.upsert(db, [tip(tip_id="a", origin="tipster-a", event="Man Utd v Spurs", status="offered")], NOW)
    out = rows.upsert(db, [tip(tip_id="b", origin="tipster-b", event="Man Utd v Spurs", status="offered")], NOW)
    assert out["duplicates"] == ["b"]
    b = next(r for r in _board() if r.tip_id == "b")
    assert b.status == "rejected" and b.reason == "duplicate of a"
    assert [r.tip_id for r in rows.feed(db, NOW)] == ["a"]


def test_expiry_is_the_earlier_of_kickoff_and_the_tips_own_and_hits_asked_rows_too():
    rows.upsert(db, [tip(tip_id="k", status="asked", expiry=KICKOFF - timedelta(hours=2))], NOW)
    assert rows.expire(db, KICKOFF - timedelta(hours=3)) == 0
    assert rows.expire(db, KICKOFF - timedelta(hours=1)) == 1
    with pytest.raises(ValueError):
        rows.set_status(db, "k", "placed", NOW)


def test_set_status_accepts_only_placed_and_gone_and_sweep_keeps_thirty_days():
    rows.upsert(db, [tip(tip_id="s", status="offered")], NOW - timedelta(days=31))
    with pytest.raises(ValueError):
        rows.set_status(db, "s", "offered", NOW)
    assert rows.sweep(db, NOW) == 1


# --- the edges the plan's interfaces name -------------------------------------

def test_the_counts_and_the_times_hermes_left():
    out = rows.upsert(db, [tip(tip_id="x", status="asked"), tip(tip_id="y", selection="B")], NOW)
    assert (out["stored"], out["updated"], out["refused"], out["duplicates"]) == (2, 0, [], [])
    later = NOW + timedelta(minutes=10)
    out = rows.upsert(db, [tip(tip_id="x", status="offered")], later)
    assert (out["stored"], out["updated"]) == (0, 1)
    x = next(r for r in _board() if r.tip_id == "x")
    assert x.asked_at == NOW.replace(tzinfo=None) and x.answered_at == later.replace(tzinfo=None)
    assert x.created_at == NOW.replace(tzinfo=None) and x.seen_at == later.replace(tzinfo=None)


def test_an_unknown_status_and_an_unknown_tip():
    out = rows.upsert(db, [tip(tip_id="p", status="placed")], NOW)
    assert out["refused"] == [("p", "status")] and _board() == []
    with pytest.raises(KeyError):
        rows.set_status(db, "nope", "gone", NOW)


def test_an_offered_row_does_not_go_back_to_asked():
    rows.upsert(db, [tip(tip_id="o", status="offered")], NOW)
    out = rows.upsert(db, [tip(tip_id="o", fair=None)], NOW)
    assert out["refused"] == [("o", "offered → asked")]
    assert rows.feed(db, NOW)[0].tip_id == "o"


def test_an_asked_row_cannot_be_placed():
    rows.upsert(db, [tip(tip_id="q", status="asked")], NOW)
    with pytest.raises(ValueError):
        rows.set_status(db, "q", "placed", NOW)


def test_a_repost_after_kickoff_changes_nothing():
    rows.upsert(db, [tip(tip_id="late", status="asked")], NOW)
    out = rows.upsert(db, [tip(tip_id="late", status="offered")], KICKOFF + timedelta(seconds=1))
    assert out["refused"] == [("late", "final")] and _board()[0].status == "expired"


def test_a_duplicate_is_matched_on_event_id_when_there_is_one_and_ignores_case():
    rows.upsert(db, [tip(tip_id="a", event_id="f1", event="Man Utd v Spurs")], NOW)
    out = rows.upsert(db, [tip(tip_id="b", event_id="f1", event="Manchester United v Tottenham",
                               selection=" a ")], NOW)
    assert out["duplicates"] == ["b"]
    # A different line of the same market is a different bet.
    out = rows.upsert(db, [tip(tip_id="c", event_id="f1", market="Goals", selection="Over", line=2.5),
                           tip(tip_id="d", event_id="f1", market="Goals", selection="Over", line=3.5)], NOW)
    assert out["duplicates"] == []


def test_a_settled_bet_frees_its_key_and_rejected_rows_never_hold_it():
    rows.upsert(db, [tip(tip_id="a", status="rejected", reason="price below fair")], NOW)
    out = rows.upsert(db, [tip(tip_id="b")], NOW)
    assert out["duplicates"] == []
    rows.set_status(db, "b", "gone", NOW)
    assert rows.upsert(db, [tip(tip_id="c")], NOW)["duplicates"] == []


def test_an_asked_row_answered_offered_is_checked_for_a_duplicate_too():
    rows.upsert(db, [tip(tip_id="a", status="asked", fair=None)], NOW)
    rows.upsert(db, [tip(tip_id="b", status="offered", selection="B")], NOW)
    # b took the key for B first; a, answered as B, is a duplicate of it.
    out = rows.upsert(db, [tip(tip_id="a", status="offered", selection="B")], NOW)
    assert out["duplicates"] == ["a"]
    a = next(r for r in _board() if r.tip_id == "a")
    assert a.status == "rejected" and a.reason == "duplicate of b"


def test_the_feed_is_soonest_kickoff_first_and_the_board_newest_first():
    rows.upsert(db, [tip(tip_id="late", selection="X", kickoff=KICKOFF + timedelta(hours=1))], NOW)
    rows.upsert(db, [tip(tip_id="soon", selection="Y")], NOW + timedelta(minutes=1))
    assert [r.tip_id for r in rows.feed(db, NOW + timedelta(minutes=2))] == ["soon", "late"]
    assert [r.tip_id for r in _board()] == ["soon", "late"]


def test_callum_saying_no_to_an_asked_row_with_no_fair_rejects_it():
    rows.upsert(db, [tip(tip_id="n", fair=None, status="asked")], NOW)
    rows.upsert(db, [tip(tip_id="n", fair=None, status="rejected", reason="Cal: no")], NOW)
    n = _board()[0]
    assert (n.status, n.reason, n.edge) == ("rejected", "Cal: no", None)


def test_an_offered_row_reposted_onto_another_live_bet_is_refused_and_unchanged():
    rows.upsert(db, [tip(tip_id="a", selection="A"), tip(tip_id="b", selection="B")], NOW)
    out = rows.upsert(db, [tip(tip_id="b", selection="A", reason="moved")], NOW)
    assert out["refused"] == [("b", "duplicate of a")] and out["duplicates"] == []
    b = next(r for r in _board() if r.tip_id == "b")
    assert (b.status, b.selection, b.reason) == ("offered", "B", None)


# --- the final review's fixes (FR1–FR5, finder-ledger.md) ----------------------

def test_an_asked_row_may_come_with_no_kickoff_odds_or_fair_and_lives_48_hours():
    # FR1: Hermes asks when it cannot place a tip on a day and time.
    out = rows.upsert(db, [tip(tip_id="q", status="asked", kickoff=None, odds=None, fair=None,
                               reason="which day is Arsenal?")], NOW)
    assert out["stored"] == 1 and out["rows"] == [{"tip_id": "q", "status": "asked"}]
    q = _board()[0]
    assert (q.kickoff, q.odds, q.fair, q.edge) == (None, None, None, None)
    assert q.expires_at == (NOW + timedelta(hours=48)).replace(tzinfo=None)
    assert rows.expire(db, NOW + timedelta(hours=47)) == 0
    assert rows.expire(db, NOW + timedelta(hours=48)) == 1


def test_an_asked_row_with_no_kickoff_expires_at_the_tips_own_expiry():
    rows.upsert(db, [tip(tip_id="q", status="asked", kickoff=None, odds=None, fair=None,
                         expiry=NOW + timedelta(hours=3))], NOW)
    assert _board()[0].expires_at == (NOW + timedelta(hours=3)).replace(tzinfo=None)


def test_an_offered_post_missing_kickoff_or_odds_is_stored_asked():
    # The route refuses it (422); rows.upsert, called directly, never offers it.
    rows.upsert(db, [tip(tip_id="k", kickoff=None), tip(tip_id="o", selection="B", odds=None)], NOW)
    assert rows.feed(db, NOW) == [] and {r.status for r in _board()} == {"asked"}


def test_a_placed_twin_blocks_the_same_bet_from_another_tipster():
    # FR2 + FR3: A is placed; B, from another tipster in other words, carries the same
    # canonical event_id, so it is the same bet and must not be placed twice.
    eid = "football:2026-11-07:manchester-united-v-tottenham-hotspur"
    rows.upsert(db, [tip(tip_id="a", origin="tipster-a", event_id=eid, event="Man Utd v Spurs")], NOW)
    rows.set_status(db, "a", "placed", NOW)
    out = rows.upsert(db, [tip(tip_id="b", origin="tipster-b", event_id=eid,
                               event="Manchester United vs Tottenham")], NOW)
    assert out["duplicates"] == ["b"] and out["rows"] == [{"tip_id": "b", "status": "rejected"}]
    b = next(r for r in _board() if r.tip_id == "b")
    assert (b.status, b.reason) == ("rejected", "duplicate of a")
    assert rows.feed(db, NOW) == []


def test_gone_rejected_and_expired_twins_do_not_block():
    rows.upsert(db, [tip(tip_id="g")], NOW)
    rows.set_status(db, "g", "gone", NOW)
    rows.upsert(db, [tip(tip_id="r", status="rejected")], NOW)
    rows.upsert(db, [tip(tip_id="x", status="asked", fair=None, expiry=NOW + timedelta(minutes=1))], NOW)
    rows.expire(db, NOW + timedelta(minutes=2))
    out = rows.upsert(db, [tip(tip_id="n")], NOW + timedelta(minutes=3))
    assert out["duplicates"] == [] and rows.feed(db, NOW + timedelta(minutes=3))[0].tip_id == "n"


def test_hermes_withdraws_an_offered_row_by_reposting_it_rejected():
    # FR4: offered → rejected; the row leaves the feed with Hermes's reason.
    rows.upsert(db, [tip(tip_id="w")], NOW)
    later = NOW + timedelta(minutes=5)
    out = rows.upsert(db, [tip(tip_id="w", status="rejected", reason="tipster withdrew it")], later)
    assert out["refused"] == [] and out["rows"] == [{"tip_id": "w", "status": "rejected"}]
    w = _board()[0]
    assert (w.status, w.reason, w.answered_at) == ("rejected", "tipster withdrew it", later.replace(tzinfo=None))
    assert rows.feed(db, later) == []


def test_a_placed_row_cannot_be_withdrawn():
    rows.upsert(db, [tip(tip_id="p")], NOW)
    rows.set_status(db, "p", "placed", NOW)
    out = rows.upsert(db, [tip(tip_id="p", status="rejected", reason="tipster withdrew it")], NOW)
    assert out["refused"] == [("p", "final")] and out["rows"] == [{"tip_id": "p", "status": "placed"}]
    assert (_board()[0].status, _board()[0].reason) == ("placed", None)


def test_the_answer_carries_each_rows_stored_status():
    # FR5: the Hub marks what Render stored, not what Hermes sent.
    rows.upsert(db, [tip(tip_id="a")], NOW)
    out = rows.upsert(db, [tip(tip_id="nofair", selection="X", fair=None),       # offered → stored asked
                           tip(tip_id="dup"),                                       # twin of a → rejected
                           tip(tip_id="bad", status="placed"),                      # not Hermes's word, no row
                           tip(tip_id="a", reason="again")], NOW)                   # an update
    assert out["rows"] == [{"tip_id": "nofair", "status": "asked"}, {"tip_id": "dup", "status": "rejected"},
                           {"tip_id": "bad", "status": None}, {"tip_id": "a", "status": "offered"}]
