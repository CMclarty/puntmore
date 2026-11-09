"""AW 26: the start date, London days, and a slip dated by the game that
decided it, whenever its results are typed."""
from datetime import date, datetime

from app.aw26.service import build_slip, day_bounds, settle_slip
from app.schemas import AwSlipRecordIn

from tests.aw26_kit import LEGS, leg, setup, slip_body  # noqa: F401  (setup is autouse here)

TUE1, TUE2, THU = "2026-11-10T15:00:00Z", "2026-11-10T19:45:00Z", "2026-11-12T19:45:00Z"
SAT = datetime(2026, 11, 14, 9, 0)


def _refund(kickoffs):
    body = slip_body("R1", [leg(e, m, sel, o, f, kickoff=k)            # the kit's qualifying 5-fold
                            for (e, m, sel, o, f), k in zip(LEGS, kickoffs)], offer="refund", stake=50)
    return build_slip(AwSlipRecordIn(**body), user="t", now=datetime(2026, 11, 9, 12, 0))


def test_day_bounds_are_london_days():
    assert day_bounds(date(2026, 11, 9)) == (datetime(2026, 11, 9, 0, 0), datetime(2026, 11, 10, 0, 0))
    assert day_bounds(date(2027, 6, 2)) == (datetime(2027, 6, 1, 23, 0), datetime(2027, 6, 2, 23, 0))


def test_profit_lands_on_the_deciding_games_day_whenever_the_result_is_typed():
    slip = _refund([TUE1, TUE2, THU, THU, THU])
    slip.legs[0].result = slip.legs[1].result = "lost"
    settle_slip(slip, now=SAT)                                   # typed four days late
    assert slip.status == "lost" and slip.settled_at == datetime(2026, 11, 10, 19, 45)
    for lg in slip.legs[2:]:
        lg.result = "won"
    settle_slip(slip, now=SAT)
    assert slip.settled_at == datetime(2026, 11, 10, 19, 45)     # later results move nothing


def test_the_order_results_are_typed_in_does_not_matter():
    slip = _refund([TUE1, TUE2, THU, THU, THU])
    for lg in slip.legs[2:]:
        lg.result = "won"
    settle_slip(slip, now=SAT)
    assert slip.status == "open" and slip.settled_at is None
    slip.legs[0].result = slip.legs[1].result = "lost"
    settle_slip(slip, now=SAT)
    assert slip.settled_at == datetime(2026, 11, 10, 19, 45)     # two losers on Tuesday decided it


def test_a_refunded_slip_is_decided_by_its_last_game():
    slip = _refund([TUE1, TUE2, THU, THU, THU])
    slip.legs[0].result = "lost"
    for lg in slip.legs[1:]:
        lg.result = "won"
    settle_slip(slip, now=SAT)
    assert slip.status == "refunded" and slip.settled_at == datetime(2026, 11, 12, 19, 45)


def test_a_result_on_a_leg_with_no_kickoff_dates_the_slip_by_when_it_was_recorded():
    slip = _refund([None, TUE2, THU, THU, THU])
    slip.legs[0].result = slip.legs[1].result = "lost"
    settle_slip(slip, now=SAT)
    assert slip.settled_at == SAT
    settle_slip(slip, now=datetime(2026, 11, 20, 9, 0))
    assert slip.settled_at == SAT                                # and it stays there
    slip.legs[0].result = "pending"
    settle_slip(slip, now=SAT)
    assert slip.status == "open" and slip.settled_at is None
