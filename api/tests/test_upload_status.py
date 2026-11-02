"""The upload's live status: every read ends in words, never a silent spin."""
from datetime import date

from app import upload_progress
from app.models import Coupon

from tests.test_grid_ingest import TestSession, setup_db, db  # noqa: F401  (fixtures)
from tests.test_upload_sheets import W2, W3, WTN, _upload, client_for  # noqa: F401


def _status(c, cid):
    r = c.get(f"/upload/{cid}/status")
    assert r.status_code == 200, r.text
    return r.json()


def _states(body):
    return {s["key"]: s["state"] for s in body["steps"]}


def test_a_read_that_works_shows_every_step_done(client_for):
    c = client_for({W2: (3.0, 9.0), W3: (5.0, 15.0), WTN: (4.0, 11.0)})
    body = _status(c, _upload(c, "SHEET_WIN_AND"))
    assert body["status"] == "confirmed" and body["error"] is None and not body["stalled"]
    states = _states(body)
    assert states["verify"] == "skipped"          # this OCR does no strip reads
    assert all(states[k] == "done" for k in ("saved", "read", "check", "build", "price"))
    assert body["legs"] > 0


def test_a_failed_read_says_why_on_the_step_it_died(client_for):
    c = client_for({})

    def boom(*a, **k):
        raise RuntimeError("vision API timed out")
    c.app.state.ocr_provider.extract = boom
    body = _status(c, _upload(c, "SHEET_WIN_AND"))
    assert body["status"] == "failed"
    assert "vision API timed out" in body["error"]
    assert _states(body)["read"] == "failed"
    assert _states(body)["price"] == "todo"


def test_a_coupon_nothing_is_reading_is_reported_stalled(client_for, db):
    c = client_for({})
    coupon = Coupon(type_code="WO15", period="SAT", match_day=date.today(),
                    status="processing", name="x")
    db.add(coupon)
    db.commit()
    body = _status(c, coupon.id)              # no progress entry: a restart
    assert body["stalled"] and "Upload the photo again" in body["error"]


def test_strip_reads_are_counted_on_the_reading_thread():
    upload_progress.start(991)
    with upload_progress.reading(991):
        cid = upload_progress.current()
        upload_progress.step(cid, "verify", "0 of 4 strip reads")
        upload_progress.note(cid, "3 of 4 strip reads")
    snap = upload_progress.snapshot(991)
    verify = next(s for s in snap["steps"] if s["key"] == "verify")
    assert verify["state"] == "running" and verify["note"] == "3 of 4 strip reads"
    assert upload_progress.current() is None
