"""Upload normalisation: EXIF orientation and the send-size cap."""
import io

from PIL import Image

from app.images import MAX_LONG_EDGE, MAX_PAYLOAD_BYTES, prepare_for_ocr


def _jpeg(orientation=None, size=(400, 200)):
    img = Image.new("RGB", size, "white")
    buf = io.BytesIO()
    if orientation is None:
        img.save(buf, format="JPEG")
    else:
        exif = img.getexif()
        exif[0x0112] = orientation
        img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_upright_in_range_image_is_returned_untouched():
    # Must not re-encode: a needless JPEG round-trip loses detail that small
    # print on a coupon depends on.
    plain = _jpeg()
    assert prepare_for_ocr(plain, "image/jpeg") == (plain, "image/jpeg")
    upright = _jpeg(orientation=1)
    assert prepare_for_ocr(upright, "image/jpeg") == (upright, "image/jpeg")


def test_rotated_image_is_actually_rotated():
    rotated = _jpeg(orientation=6, size=(400, 200))  # 6 = rotate 90° CW
    out, _ = prepare_for_ocr(rotated, "image/jpeg")
    assert Image.open(io.BytesIO(out)).size == (200, 400)


def test_unreadable_input_does_not_raise():
    # A mis-rotated image still OCRs imperfectly; a raised exception loses the
    # upload entirely. Always fall back to the original bytes.
    junk = b"not an image"
    assert prepare_for_ocr(junk, "image/jpeg") == (junk, "image/jpeg")


def test_oversized_image_is_capped_at_the_apis_own_resolution():
    """Above the cap the vision API downscales anyway, so keeping the extra
    pixels costs memory and buys nothing."""
    big = _jpeg(size=(MAX_LONG_EDGE * 2, MAX_LONG_EDGE))
    out, media_type = prepare_for_ocr(big, "image/jpeg")
    assert media_type == "image/jpeg"
    w, h = Image.open(io.BytesIO(out)).size
    assert max(w, h) == MAX_LONG_EDGE
    assert (w, h) == (MAX_LONG_EDGE, MAX_LONG_EDGE // 2)   # aspect preserved


def test_a_rotated_oversized_image_is_both_capped_and_rotated():
    big = _jpeg(orientation=6, size=(MAX_LONG_EDGE * 2, MAX_LONG_EDGE))
    out, _ = prepare_for_ocr(big, "image/jpeg")
    # Rotated 90°, so the long edge is now the height — and still capped.
    assert Image.open(io.BytesIO(out)).size == (MAX_LONG_EDGE // 2, MAX_LONG_EDGE)


def test_the_prepared_image_fits_the_providers_send_limit():
    """The Claude API refuses any image over 10 MB base64-encoded. A capped
    photo lands far below that; this is the regression guard."""
    # Noise, so JPEG cannot compress it away — a worst case for size.
    import os
    noisy = Image.frombytes(
        "RGB", (4000, 3000), os.urandom(4000 * 3000 * 3)
    )
    buf = io.BytesIO()
    noisy.save(buf, format="JPEG", quality=95)
    out, _ = prepare_for_ocr(buf.getvalue(), "image/jpeg")
    assert len(out) <= MAX_PAYLOAD_BYTES


def test_a_png_too_large_to_send_falls_back_to_jpeg():
    """Better a JPEG the model can read than a PNG it never receives."""
    import os
    from app import images

    noisy = Image.frombytes("RGB", (2000, 1500), os.urandom(2000 * 1500 * 3))
    buf = io.BytesIO()
    noisy.save(buf, format="PNG")

    # A PNG of noise is genuinely enormous; make the threshold bite regardless.
    original = images.MAX_PAYLOAD_BYTES
    images.MAX_PAYLOAD_BYTES = 1_000
    try:
        # Force the resize path so the encoder actually runs.
        big = io.BytesIO()
        noisy.resize((MAX_LONG_EDGE + 100, 1500)).save(big, format="PNG")
        out, media_type = images.prepare_for_ocr(big.getvalue(), "image/png")
    finally:
        images.MAX_PAYLOAD_BYTES = original

    assert media_type == "image/jpeg"
    assert Image.open(io.BytesIO(out)).format == "JPEG"
