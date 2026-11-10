"""The 04:00 sweep of the upload folder (app/orphan_files.py): a file no row
points at goes; every file a coupon, an AW 26 slip or a queued photo points at
stays, and so does one written in the last hour (an upload still storing)."""
import os
import time
from datetime import date, datetime

from app.config import settings
from app.models import AwPhoto, AwSlip, Coupon
from app.orphan_files import MIN_AGE_SECONDS, sweep

from tests.aw26_kit import Session, setup  # noqa: F401  (autouse: database and upload folder)

OLD = time.time() - MIN_AGE_SECONDS - 60


def _file(name: str, *, at: float = OLD) -> str:
    path = os.path.join(settings.upload_dir, name)
    with open(path, "wb") as f:
        f.write(b"x")
    os.utime(path, (at, at))
    return path


def test_a_file_no_row_points_at_is_removed():
    stray = _file("stray.jpg")
    db = Session()
    assert sweep(db) == 1
    assert not os.path.exists(stray)


def test_every_file_a_row_points_at_stays():
    coupon, slip, photo = _file("coupon.jpg"), _file("slip.jpg"), _file("photo.jpg")
    db = Session()
    db.add(Coupon(type_code="GGB", match_day=date(2026, 11, 9), status="confirmed",
                  image_path=coupon))
    db.add(AwSlip(placed_at=datetime(2026, 11, 9, 12), user="callum", image_path=slip))
    db.add(AwPhoto(created_at=datetime(2026, 11, 9, 12), user="callum", image_path=photo,
                   source="page", state="waiting", tries=0))
    db.commit()
    assert sweep(db) == 0
    assert all(os.path.exists(p) for p in (coupon, slip, photo))


def test_a_row_written_with_another_form_of_the_path_still_keeps_its_file():
    # Rows store str(Path(upload_dir) / name); a relative and an absolute
    # spelling of the same folder must not cost the file.
    kept = _file("kept.jpg")
    db = Session()
    db.add(AwSlip(placed_at=datetime(2026, 11, 9, 12), user="callum",
                  image_path=os.path.join("somewhere", "else", "kept.jpg")))
    db.commit()
    assert sweep(db) == 0
    assert os.path.exists(kept)


def test_a_file_written_in_the_last_hour_stays():
    fresh = _file("fresh.jpg", at=time.time() - 60)
    assert sweep(Session()) == 0
    assert os.path.exists(fresh)


def test_folders_and_a_missing_upload_folder_are_left_alone(monkeypatch, tmp_path):
    os.mkdir(os.path.join(settings.upload_dir, "sub"))
    assert sweep(Session()) == 0
    assert os.path.isdir(os.path.join(settings.upload_dir, "sub"))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "never-made"))
    assert sweep(Session()) == 0


def test_the_sweep_runs_at_four_in_the_morning_london():
    from app.scheduler import start_scheduler
    s = start_scheduler(odds_provider=None)
    try:
        job = s.get_job("orphan_files")
        assert job is not None
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert (fields["hour"], fields["minute"]) == ("4", "0")
        assert str(job.trigger.timezone) == "Europe/London"
    finally:
        s.shutdown(wait=False)
