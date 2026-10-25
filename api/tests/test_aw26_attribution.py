"""AW 26 EV attribution: each leg's edge, a slip's EV split by the source that
priced each leg (plus the offer's own worth), reconciling to the penny; and
summary() reporting the value on offer beside the EV taken."""
from datetime import date, datetime
from math import prod

import pytest

from app.aw26.attribution import add_up, ev_by_source, leg_edge
from app.aw26.offers import expected_value
from app.aw26.service import price_slip, summary
from app.models import AwLeg, AwLegLine, AwSlip, ValueSnapshot

PLACED = datetime(2026, 10, 25, 12, 0)


def _slip(legs, offer="other", stake=50.0):
    """legs: (odds, fair, source) with source 'paddy' (married) or 'bb'."""
    slip = AwSlip(placed_at=PLACED, user="u", offer=offer, stake=stake, status="open")
    for i, (odds, fair, source) in enumerate(legs):
        leg = AwLeg(position=i, event="A v B", odds=odds, kind="corners", line=9.5,
                    side="over", result="pending")
        if source == "paddy":
            leg.line_link = AwLegLine(source="paddy", snapshot_at=PLACED,
                                      offered_odds_at_snapshot=odds, fair_odds=fair,
                                      edge_at_snapshot=odds / fair - 1, match_score=1.0)
        else:
            leg.fair_odds = fair
        slip.legs.append(leg)
    price_slip(slip, [])
    return slip


def test_leg_edge():
    leg = AwLeg(odds=2.2, fair_odds=2.0)
    assert leg_edge(leg) == pytest.approx(0.10)
    assert leg.edge == pytest.approx(0.10)
    assert leg_edge(AwLeg(odds=2.2)) is None


def test_a_single_married_leg_is_all_paddy():
    slip = _slip([(2.2, 2.0, "paddy")])
    assert slip.legs[0].fair_source == "paddy"
    assert ev_by_source(slip) == {"paddy": slip.ev}


def test_a_refund_acca_splits_into_offer_and_sources_to_the_penny():
    legs = [(1.40, 1.30, "paddy"), (1.36, 1.33, "bb"), (1.33, 1.33, "bb"),
            (1.25, 1.20, "paddy"), (1.30, 1.31, "bb")]
    slip = _slip(legs, offer="refund")
    split = ev_by_source(slip)
    assert set(split) == {"offer", "paddy", "bb"}
    assert round(sum(split.values()), 2) == slip.ev
    plain = 50 * (prod(o for o, _, _ in legs) / prod(f for _, f, _ in legs) - 1)
    assert split["offer"] == pytest.approx(slip.ev - plain, abs=0.02)
    assert split["offer"] > 0
    assert split["paddy"] > split["bb"]          # the paddy legs carry most edge


def test_an_off_terms_slip_has_no_offer_share():
    slip = _slip([(1.5, 1.4, "bb"), (1.5, 1.4, "paddy"), (1.5, 1.5, "bb"), (1.5, 1.45, "bb")],
                 offer="refund")                 # 4 legs: no refund
    assert "offer" not in ev_by_source(slip)


def test_no_net_edge_splits_evenly_by_leg():
    slip = _slip([(2.0, 2.0, "paddy"), (2.0, 2.0, "bb")], offer="boost", stake=10)
    # 2-fold: no boost tier, so EV is 0 and nothing to split but zeros.
    assert sum(ev_by_source(slip).values()) == pytest.approx(slip.ev)


def test_unpriced_slip_has_no_split():
    slip = AwSlip(placed_at=PLACED, user="u", offer="other", stake=10, status="open")
    slip.legs.append(AwLeg(position=0, event="A v B", odds=2.0, result="pending"))
    price_slip(slip, [])
    assert slip.ev is None and ev_by_source(slip) is None
    assert add_up([slip]) == {}


def test_summary_reports_value_on_offer_and_ev_by_source():
    a = _slip([(2.2, 2.0, "paddy")])
    b = _slip([(2.1, 2.0, "bb")])
    snap = ValueSnapshot(at=datetime(2026, 10, 25, 11, 45), source="paddy", n_lines=120,
                         n_value=14, best_edge=0.081, sum_edge_positive=0.42)
    s = summary([a, b], date(2026, 10, 25), snapshot=snap)
    assert s["value_on_offer"] == {"at": "2026-10-25T11:45:00", "source": "paddy",
                                   "lines": 120, "value_lines": 14, "best_edge": 0.081,
                                   "sum_edge_positive": 0.42}
    assert s["today"]["ev_by_source"] == {"paddy": a.ev, "bb": b.ev}
    assert s["total"]["ev_by_source"] == {"paddy": a.ev, "bb": b.ev}
    assert summary([a, b], date(2026, 10, 25))["value_on_offer"] is None


def test_ev_matches_the_offer_maths():
    slip = _slip([(2.2, 2.0, "paddy")])
    assert slip.ev == round(expected_value("other", 50, [2.2], [2.0]), 2)


# --- the daily post ----------------------------------------------------------


def test_daily_message_keeps_its_lines_and_adds_the_new_ones():
    from app.aw26.service import daily_message
    a = _slip([(2.2, 2.0, "paddy")])
    plain = daily_message([a], date(2026, 10, 25))
    snap = ValueSnapshot(at=datetime(2026, 10, 26, 8, 45), source="paddy", n_lines=120,
                         n_value=14, best_edge=0.081, sum_edge_positive=0.42)
    full = daily_message([a], date(2026, 10, 25), snapshot=snap, free_bet_balance=0.0)
    old = plain.split("\n")
    new = full.split("\n")
    assert new[:4] == old[:4]                         # the original four, unchanged
    assert new[4:] == [
        f"EV by source: paddy £{a.ev:,.0f}",
        "On offer now (paddy, 08:45): 14 value lines of 120 · best +8.1% · Σ edge +42.0%",
        "Free-bet balance £0 (not tracked yet)",
    ]


def test_daily_message_without_priced_slips_or_a_snapshot_is_the_old_four_lines():
    from app.aw26.service import daily_message
    s = AwSlip(placed_at=datetime(2026, 10, 20, 12), user="u", offer="refund", stake=50,
               status="open")
    assert len(daily_message([s], date(2026, 10, 20)).split("\n")) == 4


def test_run_daily_posts_the_value_on_offer_and_free_bet_lines():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.aw26 import snapshots
    from app.aw26.service import run_daily
    from app.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    snapshots.record(db, [], "paddy", datetime(2026, 10, 21, 7, 45))
    msg = run_daily(db, None, day=date(2026, 10, 20))
    assert "On offer now (paddy, 08:45): 0 value lines of 0 · Σ edge +0.0%" in msg
    assert msg.endswith("Free-bet balance £0 (not tracked yet)")
    db.close()
    snapshots._last.clear()
