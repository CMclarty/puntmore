def test_tip_dedupe(store):
    tip = {"source_id": "s", "event": "A v B", "market": "Match Result", "selection": "A", "tip_time": "2026-11-05T10:00:00+00:00"}
    assert store.add_tip(tip) is not None
    assert store.add_tip({**tip, "advised_price": 2.0}) is None
    assert store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 1


def test_source_record(store):
    for i, (outcome, ret) in enumerate([("won", 2.0), ("lost", 0.0)]):
        tid = store.add_tip({"source_id": "t", "event": f"E{i}", "market": "M", "selection": "S", "tip_time": "2026-11-05", "advised_stake_units": 1.0})
        store.settle_tip(tid, outcome, ret)
    rec = store.source_record("t")
    assert rec["settled"] == 2 and rec["strike_rate"] == 0.5 and rec["roi"] == 0.0


def test_open_exposure(store):
    store.upsert_bet("b1", "acc", {"stake": 10.0}, "placed")
    store.upsert_bet("b2", "acc", {"stake": 5.0}, "settled")
    assert store.open_exposure("acc") == 10.0
