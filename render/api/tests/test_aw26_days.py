"""AW 26: the start date, London days, a slip dated by the game that decided
it whenever its results are typed, and the summary and the slips for one day."""
from datetime import date, datetime

from fastapi.testclient import TestClient

from app.aw26.service import build_slip, day_bounds, settle_slip
from app.main import app
from app.schemas import AwSlipRecordIn

from tests.aw26_kit import LEGS, clock, leg, setup, slip_body  # noqa: F401  (setup is autouse here)

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


# --- one day at a time -------------------------------------------------------


def _single(client, receipt, placed, kickoff, odds=2.0, fair=1.9, event="Arsenal v Chelsea"):
    return client.post("/aw26/slips/record", json=slip_body(
        receipt, [leg(event, None, "Over 10.5 corners", odds, fair, kickoff=kickoff)], placed_at=placed)).json()


def test_each_figure_counts_on_its_own_day(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 11, 22, 0))
    client = TestClient(app)
    a = _single(client, "D1", "09/11/2026 12:00", "2026-11-11T19:45:00Z")
    client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "won"})
    mon = client.get("/aw26/summary?day=2026-11-09").json()
    assert mon["chosen"] == {"ev": a["ev"], "ev_target": 1000.0, "profit": 0.0, "staked": 10.0, "slips": 1, "open": 0}
    assert mon["selections"] == []                               # the game is on Wednesday
    wed = client.get("/aw26/summary").json()
    assert wed["day"] == wed["today"] == "2026-11-11"
    assert wed["chosen"] == {"ev": 0.0, "ev_target": 1000.0, "profit": 10.0, "staked": 0.0, "slips": 0, "open": 0}
    assert wed["to_date"] == {"ev": a["ev"], "ev_target": 1000.0, "profit": 10.0, "staked": 10.0, "slips": 1, "open": 0}
    assert [s["result"] for s in wed["selections"]] == ["won"]


def test_a_day_is_a_day_on_which_a_slip_was_placed(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 14, 12, 0))
    client = TestClient(app)
    _single(client, "D1", "09/11/2026 12:00", None)
    _single(client, "D2", "09/11/2026 18:00", None)
    _single(client, "D3", "12/11/2026 12:00", None)
    s = client.get("/aw26/summary").json()
    assert s["days"] == 2 and s["to_date"]["ev_target"] == 2000.0 and s["to_date"]["slips"] == 3


def test_slips_from_before_the_start_count_nowhere(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 9, 12, 0))
    client = TestClient(app)
    _single(client, "OLD", "08/11/2026 12:00", "2026-11-09T15:00:00Z")
    s = client.get("/aw26/summary").json()
    assert s["days"] == 0 and s["to_date"]["slips"] == 0 and s["selections"] == []
    assert client.get("/aw26/slips?day=2026-11-09").json() == {"slips": []}
    assert client.get("/aw26/slips?day=2026-11-08").json() == {"slips": []}  # its own day lists nothing either
    assert client.get("/aw26/summary?day=2026-11-08").status_code == 400
    assert client.get("/aw26/summary?day=2026-11-10").status_code == 400     # tomorrow


def test_today_lists_from_today_forward_and_an_earlier_day_lists_itself(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 10, 12, 0))
    client = TestClient(app)
    _single(client, "D1", "09/11/2026 12:00", "2026-11-09T15:00:00Z", event="Mon v Day")
    _single(client, "D2", "09/11/2026 12:00", "2026-11-10T15:00:00Z", event="Tue v Day")
    _single(client, "D3", "09/11/2026 12:00", "2026-11-12T19:45:00Z", event="Thu v Day")
    _single(client, "D4", "10/11/2026 09:00", None, event="No v Kickoff")
    events = lambda day: [s["event"] for s in client.get(f"/aw26/summary{day}").json()["selections"]]
    assert events("") == ["Tue v Day", "Thu v Day", "No v Kickoff"]
    assert events("?day=2026-11-09") == ["Mon v Day"]


def test_a_late_summer_evening_belongs_to_the_london_day(monkeypatch):
    clock(monkeypatch, datetime(2027, 6, 2, 9, 0))
    client = TestClient(app)
    _single(client, "BST", "2027-06-01T23:30:00Z", None)          # 00:30 on 2 June in London
    assert len(client.get("/aw26/slips?day=2027-06-02").json()["slips"]) == 1
    assert client.get("/aw26/slips?day=2027-06-01").json() == {"slips": []}
    assert client.get("/aw26/summary").json()["chosen"]["slips"] == 1


def test_a_losing_run_and_an_empty_ledger_are_plain_numbers(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 9, 22, 0))
    client = TestClient(app)
    empty = client.get("/aw26/summary").json()
    assert empty["days"] == 0 and empty["to_date"] == {"ev": 0.0, "ev_target": 0.0, "profit": 0.0, "staked": 0.0, "slips": 0, "open": 0}
    a = _single(client, "L1", "09/11/2026 12:00", "2026-11-09T15:00:00Z")
    client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "lost"})
    assert client.get("/aw26/summary").json()["to_date"]["profit"] == -10.0
