"""Nothing in this app may grow without a ceiling.

Every cache here was once a plain dict with a timestamp beside each value. The
timestamp decided whether a value could be REUSED; it never removed the entry.
Keyed by fixture id and fed by a loop that runs every ten minutes forever, that
is not a cache — it is a list of every fixture the app has ever seen, and it is
what exhausted the instance's memory until Render restarted it.

These tests fail if any of that comes back.
"""
import time

import pytest

from app.odds.client import CACHE_MAX, EVENT_CACHE_MAX, TheOddsApiClient
from app.providers.grid_coupon import (
    GRID_CACHE_MAX, ODDS_CACHE_MAX, GridCouponProvider,
)
from app.ttl_cache import MISS, TTLCache


# --- the cache itself -------------------------------------------------------

def test_an_expired_entry_is_deleted_not_merely_ignored():
    """The whole bug in one assertion. The old code checked the timestamp on
    read and left the entry in place, so nothing was ever freed."""
    c = TTLCache(ttl=0.05, maxsize=100)
    c.set("k", "v")
    assert c.get("k") == "v"
    time.sleep(0.06)
    assert c.get("k") is MISS
    assert len(c) == 0, "the entry must be GONE, not just unusable"


def test_writing_sweeps_keys_nobody_asks_about_again():
    """The normal case here: a fixture stops being queried once it kicks off,
    so an eviction that only happens on read would never happen at all."""
    c = TTLCache(ttl=0.05, maxsize=100)
    for i in range(10):
        c.set(f"old{i}", i)
    time.sleep(0.06)
    c.set("new", 1)
    assert len(c) == 1


def test_the_ceiling_holds_when_inserts_outrun_the_ttl():
    c = TTLCache(ttl=3600, maxsize=50)
    for i in range(5000):
        c.set(i, "x" * 100)
    assert len(c) == 50


def test_the_oldest_entry_is_the_one_evicted():
    c = TTLCache(ttl=3600, maxsize=3)
    for k in ("a", "b", "c"):
        c.set(k, k)
    c.get("a")          # touching it makes it the newest
    c.set("d", "d")
    assert c.get("b") is MISS
    assert c.get("a") == "a"


def test_a_cached_none_is_not_a_miss():
    """Load-bearing: a fixture whose grid cannot be fitted caches None, and
    without this it would be refetched and refitted on every single pass."""
    c = TTLCache(ttl=3600, maxsize=10)
    c.set("k", None)
    assert c.get("k") is None
    assert c.get("absent") is MISS


# --- the caches that actually leaked ---------------------------------------

def test_the_coupon_odds_caches_are_bounded():
    p = GridCouponProvider()
    for event_id in range(ODDS_CACHE_MAX * 3):
        p._odds_cache.set(event_id, [{"market": "totals"}] * 40)
        p._grid_cache.set(event_id, {(0, 0): 0.1})
    assert len(p._odds_cache) <= ODDS_CACHE_MAX
    assert len(p._grid_cache) <= GRID_CACHE_MAX


def test_the_shared_odds_client_caches_are_bounded():
    c = TheOddsApiClient("test-key")
    for i in range(max(CACHE_MAX, EVENT_CACHE_MAX) * 3):
        c._cache.set(("sport", "h2h", str(i)), [{"id": i}])
        c._event_cache.set(("__event__", str(i), "m", ""), {"id": i})
    assert len(c._cache) <= CACHE_MAX
    assert len(c._event_cache) <= EVENT_CACHE_MAX


def test_the_login_limiter_cannot_be_grown_without_limit():
    """A spray across many IPs is the traffic this defends against, so it must
    not be a way to fill memory."""
    from app.security import LoginThrottle

    rl = LoginThrottle(max_attempts=5, lockout_seconds=900)
    for i in range(rl.MAX_KEYS * 2):
        rl.record_failure("someone", f"10.0.{i // 256}.{i % 256}")
    assert len(rl._failures) <= rl.MAX_KEYS


def test_a_locked_out_user_stays_locked_out():
    """The ceiling must not become an accidental way past the lockout."""
    from app.security import LoginThrottle

    rl = LoginThrottle(max_attempts=3, lockout_seconds=900)
    for _ in range(3):
        rl.record_failure("boss", "1.2.3.4")
    assert rl.is_locked("boss", "1.2.3.4")


# --- the request path -------------------------------------------------------

def test_an_enormous_image_is_stored_rather_than_decoded():
    """A format with no reduced-scale decode has to be materialised whole.
    Past the ceiling that is bigger than the whole instance, and a sideways
    coupon beats a restart."""
    from PIL import Image
    import io

    from app import images

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, format="PNG")
    original = buf.getvalue()

    # Claim a huge picture without allocating one.
    class _Huge:
        format = "PNG"
        width = height = 20_000          # 400 MP
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def getexif(self): return {0x0112: 6}    # rotate 90
        def draft(self, *_a): return None        # PNG has no reduced decode

    monkeypatched = images.Image.open
    images.Image.open = lambda *_a, **_k: _Huge()
    try:
        assert images.prepare_for_ocr(original, "image/png") == (original, "image/png")
    finally:
        images.Image.open = monkeypatched


def test_a_phone_photo_never_decodes_at_full_size():
    """The whole point of the cap: a 48 MP photo used to cost ~500 MB to send.
    Peak allocation here must stay a small fraction of a 512 MB instance."""
    import io
    import tracemalloc
    from PIL import Image

    from app import images

    # 8064x6048 ≈ 48 MP, a 2026 flagship at full resolution.
    src = Image.new("RGB", (8064, 6048), "white")
    buf = io.BytesIO()
    src.save(buf, format="JPEG", quality=90)
    src.close()
    data = buf.getvalue()
    del buf

    tracemalloc.start()
    out, _ = images.prepare_for_ocr(data, "image/jpeg")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert max(Image.open(io.BytesIO(out)).size) == images.MAX_LONG_EDGE
    # Full-size would be ~146 MB per copy, two copies while rotating.
    assert peak < 80 * 1024 * 1024, f"peak was {peak / 1e6:.0f} MB"


def _upload_of(client, size_bytes):
    import io
    return client.post(
        "/upload",
        data={"type_code": "TTS2", "period": "SAT"},
        files={"file": ("c.png", io.BytesIO(b"x" * size_bytes), "image/png")},
    )


def test_an_oversized_upload_is_refused(monkeypatch):
    """`await file.read()` pulls the whole body into memory whatever its size —
    on a 512 MB box that is one large POST away from a restart."""
    from app.config import settings
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "max_upload_mb", 2)
    with TestClient(app) as client:
        over = _upload_of(client, 3 * 1024 * 1024)
        assert over.status_code == 413
        assert "larger than 2 MB" in over.json()["detail"]

        # And the cap does not reject a normal photo: under the limit it gets
        # past the size check and on into the pipeline.
        under = _upload_of(client, 512 * 1024)
        assert under.status_code != 413


def test_an_empty_upload_is_refused(monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        assert _upload_of(client, 0).status_code == 400
