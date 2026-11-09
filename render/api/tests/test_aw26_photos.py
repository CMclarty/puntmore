"""AW 26: the photo queue. Render stores a slip photo and tracks its state
(waiting, reading, failed, duplicate, done); Qwen on the Hub takes it, reads
it and records the slip. Render never reads one."""
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.aw26 import photos, record
from app.main import app
from app.models import AwPhoto
from app.schemas import AwSlipRecordIn

from tests.aw26_kit import _db, _png, clock, leg, setup, slip_body  # noqa: F401  (setup is autouse here)


def _upload(client, n=1, **kw):
    files = [("files", (f"s{i}.png", _png(), "image/png")) for i in range(n)]
    return client.post("/aw26/photos", files=files, **kw)


def _legs():
    return [leg("Arsenal v Chelsea", None, "Over 10.5 corners", 1.80, 1.72)]


def test_an_upload_is_stored_waiting_and_nothing_reads_it(tmp_path):
    client = TestClient(app)
    r = _upload(client, 2)
    assert r.status_code == 200 and [p["state"] for p in r.json()] == ["waiting", "waiting"]
    assert len(os.listdir(tmp_path)) == 2
    assert [p["id"] for p in client.get("/aw26/photos").json()] == [p["id"] for p in r.json()]
    assert client.get("/aw26/slips").json() == {"slips": []}
    assert client.get(f"/aw26/photos/{r.json()[0]['id']}/image").status_code == 200


def test_one_bad_file_refuses_the_whole_batch(tmp_path):
    client = TestClient(app)
    good = ("files", ("a.png", _png(), "image/png"))
    for bad in (("files", ("b.txt", b"hello", "text/plain")), ("files", ("c.png", b"", "image/png"))):
        assert client.post("/aw26/photos", files=[good, bad]).status_code == 400
    assert _upload(client, 21).status_code == 400
    assert client.post("/aw26/photos", files=[good], data={"source": "fax"}).status_code == 400
    assert os.listdir(tmp_path) == [] and client.get("/aw26/photos").json() == []


def test_a_photo_is_handed_out_once_and_fails_three_times_before_it_stays_failed():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    assert client.post(f"/aw26/photos/{pid}/take").json()["state"] == "reading"
    assert client.post(f"/aw26/photos/{pid}/take").status_code == 409
    for tries, state in ((1, "waiting"), (2, "waiting"), (3, "failed")):
        if tries > 1:
            client.post(f"/aw26/photos/{pid}/take")
        p = client.post(f"/aw26/photos/{pid}/fail", json={"reason": "not a slip"}).json()
        assert (p["tries"], p["state"], p["reason"]) == (tries, state, "not a slip")
    assert client.post(f"/aw26/photos/{pid}/take").status_code == 409
    p = client.post(f"/aw26/photos/{pid}/resend").json()
    assert (p["state"], p["tries"], p["reason"]) == ("waiting", 0, None)


def test_a_photo_taken_and_never_finished_comes_back_after_15_minutes(monkeypatch):
    client = TestClient(app)
    clock(monkeypatch, datetime(2026, 11, 9, 12, 0))
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    clock(monkeypatch, datetime(2026, 11, 9, 12, 14))
    assert client.get("/aw26/photos").json()[0]["state"] == "reading"
    clock(monkeypatch, datetime(2026, 11, 9, 12, 16))
    p = client.get("/aw26/photos").json()[0]
    assert (p["state"], p["reason"]) == ("waiting", "Qwen did not finish")


def test_a_recorded_slip_takes_the_photo_as_its_picture(tmp_path):
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    s = client.post("/aw26/slips/record", json=slip_body("P1", _legs(), photo_id=pid)).json()
    assert s["has_image"] is True and client.get(f"/aw26/slips/{s['id']}/image").status_code == 200
    assert client.get("/aw26/photos").json() == []               # done: off the queue
    assert client.delete(f"/aw26/photos/{pid}").status_code == 409 and len(os.listdir(tmp_path)) == 1
    client.delete(f"/aw26/slips/{s['id']}")
    assert os.listdir(tmp_path) == []
    assert next(_db()).query(AwPhoto).count() == 0


def test_a_slip_already_in_marks_the_photo_a_duplicate(tmp_path):
    client = TestClient(app)
    client.post("/aw26/slips/record", json=slip_body("P1", _legs()))
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    assert client.post("/aw26/slips/record", json=slip_body("P1", _legs(), photo_id=pid)).status_code == 409
    p = client.get("/aw26/photos").json()[0]
    assert p["state"] == "duplicate" and "P1" in p["reason"]
    assert client.delete(f"/aw26/photos/{pid}").status_code == 200 and os.listdir(tmp_path) == []
    assert len(client.get("/aw26/slips").json()["slips"]) == 1


def test_a_photo_with_no_receipt_is_failed_not_recorded():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    assert client.post("/aw26/slips/record", json=slip_body(None, _legs(), photo_id=pid)).status_code == 400
    p = client.get("/aw26/photos").json()[0]
    assert (p["state"], p["reason"]) == ("waiting", "no receipt number")
    assert client.get("/aw26/slips").json() == {"slips": []}
    # By hand, with no photo, a slip without a receipt is still accepted.
    assert client.post("/aw26/slips/record", json=slip_body(None, _legs())).status_code == 200


def test_a_photo_deleted_while_qwen_reads_it_becomes_no_slip():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    assert client.delete(f"/aw26/photos/{pid}").status_code == 200
    assert client.post("/aw26/slips/record", json=slip_body("P9", _legs(), photo_id=pid)).status_code == 404
    assert client.get("/aw26/slips").json() == {"slips": []}


# --- refused moves, and two requests racing for one photo ---------------------

def test_a_done_photo_refuses_a_second_record_and_a_fail():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    assert client.post("/aw26/slips/record", json=slip_body("P1", _legs(), photo_id=pid)).status_code == 200
    r = client.post("/aw26/slips/record", json=slip_body("P2", _legs(), photo_id=pid))
    assert r.status_code == 409 and r.json()["detail"] == f"Photo {pid} is already a slip"
    r = client.post(f"/aw26/photos/{pid}/fail", json={"reason": "late"})
    assert r.status_code == 409 and r.json()["detail"] == f"Photo {pid} is done"
    assert len(client.get("/aw26/slips").json()["slips"]) == 1


def test_a_waiting_photo_cannot_be_resent():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    r = client.post(f"/aw26/photos/{pid}/resend")
    assert r.status_code == 409 and r.json()["detail"] == f"Photo {pid} is waiting, not failed"


def test_a_second_take_of_a_photo_already_reading_is_refused_and_counts_no_try():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    assert client.post(f"/aw26/photos/{pid}/take").json()["tries"] == 1
    r = client.post(f"/aw26/photos/{pid}/take")
    assert r.status_code == 409 and r.json()["detail"] == f"Photo {pid} is reading, not waiting"
    assert client.get("/aw26/photos").json()[0]["tries"] == 1


def test_two_takes_that_both_saw_the_photo_waiting_give_it_out_once():
    pid = _upload(TestClient(app)).json()[0]["id"]
    first, second = next(_db()), next(_db())
    a, b = first.get(AwPhoto, pid), second.get(AwPhoto, pid)
    assert a.state == b.state == "waiting"                       # both read it before either wrote
    photos.take(first, a)
    first.commit()
    with pytest.raises(record.Refused) as refused:
        photos.take(second, b)
    assert refused.value.status == 409
    second.rollback()
    assert (second.get(AwPhoto, pid).state, second.get(AwPhoto, pid).tries) == ("reading", 1)


def test_a_record_racing_another_for_the_same_photo_is_refused():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    late = next(_db())
    assert late.get(AwPhoto, pid).state == "reading"            # this request saw it still reading
    assert client.post("/aw26/slips/record", json=slip_body("P1", _legs(), photo_id=pid)).status_code == 200
    with pytest.raises(record.Refused) as refused:
        record.record_slip(late, AwSlipRecordIn(**slip_body("P2", _legs(), photo_id=pid)), "api:hub")
    assert (refused.value.status, str(refused.value)) == (409, f"Photo {pid} is already a slip")
    assert [s["receipt"] for s in client.get("/aw26/slips").json()["slips"]] == ["P1"]


def test_a_store_that_fails_partway_keeps_no_row_and_no_file(tmp_path, monkeypatch):
    real = photos.prepare_for_ocr
    calls = []

    def second_write_fails(data, media_type):
        calls.append(1)
        data, media_type = real(data, media_type)
        return (data if len(calls) == 1 else "not bytes"), media_type   # the write raises

    monkeypatch.setattr(photos, "prepare_for_ocr", second_write_fails)
    client = TestClient(app, raise_server_exceptions=False)
    assert _upload(client, 3).status_code == 500
    assert len(calls) == 2                                       # stopped at the second file
    assert os.listdir(tmp_path) == []
    assert next(_db()).query(AwPhoto).count() == 0


def test_a_file_too_large_partway_keeps_no_row_and_no_file(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "max_upload_mb", 0.001)       # about 1 KB
    client = TestClient(app)
    small = ("files", ("a.png", _png(), "image/png"))
    large = ("files", ("b.png", b"\x89PNG" + b"0" * 4096, "image/png"))
    assert client.post("/aw26/photos", files=[small, large]).status_code == 413
    assert os.listdir(tmp_path) == [] and next(_db()).query(AwPhoto).count() == 0
