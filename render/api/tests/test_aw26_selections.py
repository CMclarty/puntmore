"""AW 26: which legs are the same selection, grouping them, and one mark that
settles a selection on every slip that carries it."""
from datetime import datetime

from fastapi.testclient import TestClient

from app.aw26 import selections
from app.main import app
from app.models import AwLeg

from tests.aw26_kit import _db, leg, setup, slip_body  # noqa: F401  (setup is autouse here)

CORNERS = dict(market=None, selection="Over 10.5 corners", odds=1.80, fair=1.72)


def _one(client, receipt, event, odds=1.80, selection="Over 10.5 corners", **extra):
    return client.post("/aw26/slips/record", json=slip_body(
        receipt, [leg(event, None, selection, odds, 1.72, **extra)])).json()


def test_one_mark_settles_the_selection_on_every_slip_that_carries_it():
    client = TestClient(app)
    a = _one(client, "S1", "Arsenal v Chelsea")
    b = _one(client, "S2", "arsenal  vs  chelsea")               # the same game, typed differently
    c = _one(client, "S3", "Arsenal v Chelsea", selection="Over 9.5 corners")
    r = client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "won"})
    assert r.status_code == 200 and r.json() == {"legs": 2, "slips": 2}
    day = client.get("/aw26/slips?day=2026-11-09").json()["slips"]
    by = {s["receipt"]: s for s in day}
    assert (by["S1"]["status"], by["S1"]["returns"]) == ("won", 18.0)
    assert by["S2"]["status"] == "won" and by["S3"]["status"] == "open"
    # A mark is not a correction: the fair and the EV stand.
    assert by["S1"]["legs"][0]["fair_odds"] == 1.72 and by["S1"]["ev"] == a["ev"] and by["S1"]["edited"] is False


def test_an_event_id_groups_legs_whatever_their_words_for_the_game():
    client = TestClient(app)
    a = _one(client, "S1", "Arsenal v Chelsea", fixture_id="E1")
    _one(client, "S2", "Arsenal vs Chelsea FC", fixture_id="E1")
    _one(client, "S3", "Arsenal v Chelsea", fixture_id="E2")
    r = client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "lost"})
    assert r.json() == {"legs": 2, "slips": 2}


def test_a_slip_from_before_the_start_is_never_marked():
    client = TestClient(app)
    old = client.post("/aw26/slips/record", json=slip_body(
        "OLD", [leg("Arsenal v Chelsea", None, "Over 10.5 corners", 1.80, 1.72)], placed_at="08/11/2026 12:00")).json()
    new = _one(client, "S1", "Arsenal v Chelsea")
    assert client.post(f"/aw26/selections/{new['legs'][0]['id']}/result", json={"result": "won"}).json() == {"legs": 1, "slips": 1}
    assert client.post(f"/aw26/selections/{old['legs'][0]['id']}/result", json={"result": "won"}).status_code == 404


def test_a_bad_result_or_a_missing_leg_is_refused():
    client = TestClient(app)
    a = _one(client, "S1", "Arsenal v Chelsea")
    assert client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "maybe"}).status_code == 400
    assert client.post("/aw26/selections/999/result", json={"result": "won"}).status_code == 404


def test_group_averages_the_price_and_counts_the_slips():
    client = TestClient(app)
    _one(client, "S1", "Arsenal v Chelsea", odds=1.80, kickoff="2026-11-09T15:00:00Z")
    _one(client, "S2", "Arsenal v Chelsea", odds=1.90, kickoff="2026-11-09T15:00:00Z", fair_assumed=True)
    db = next(_db())
    (g,) = selections.group(db.query(AwLeg).all())
    assert (g["slips"], g["odds"], g["result"], g["fair_assumed"], g["day"]) == (2, 1.85, "pending", True, "2026-11-09")
    assert g["game"] == "w:arsenal v chelsea" and g["kickoff"] == datetime(2026, 11, 9, 15, 0)
