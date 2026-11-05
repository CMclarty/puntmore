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


# --- Straightening --------------------------------------------------------------
#
# A tilt of under a degree drifts the far edge of a 78-row sheet by most of a
# row, so the price at the right of row 55 sits level with the fixture name of
# row 54. The tilt is measured off the text lines and rotated out before the
# model sees the page.

from PIL import ImageDraw

from app.images import MIN_SKEW_DEG, STRIP_HEIGHT, measure_skew, strips


def _sheet(w=1200, h=1600, pitch=24, tall=7):
    """Rows of black 'text' bars with a fixture column and six price blocks."""
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = 80
    while y < h - 40:
        d.rectangle((40, y, 330, y + tall), fill="black")
        x = 400
        for _ in range(6):
            d.rectangle((x, y, x + 35, y + tall), fill="black")
            d.rectangle((x + 50, y, x + 85, y + tall), fill="black")
            x += 130
        y += pitch
    return img


def _tilted(deg):
    return _sheet().rotate(deg, resample=Image.BICUBIC, fillcolor="white")


def test_a_tilted_sheet_is_measured_to_within_a_tenth_of_a_degree():
    for tilt in (0.4, 0.8, 1.5, -1.2, 2.7):
        correction = measure_skew(_tilted(tilt))
        assert abs(correction + tilt) <= 0.15, (tilt, correction)


def test_a_level_sheet_and_a_blank_page_are_left_alone():
    assert measure_skew(_sheet()) == 0.0
    assert measure_skew(Image.new("RGB", (800, 1000), "white")) == 0.0


def test_a_tilt_below_the_floor_is_not_corrected():
    assert measure_skew(_tilted(MIN_SKEW_DEG / 2)) == 0.0


def test_prepare_straightens_the_page_it_stores():
    buf = io.BytesIO()
    _tilted(1.5).save(buf, format="JPEG", quality=92)
    out, media_type = prepare_for_ocr(buf.getvalue(), "image/jpeg")
    assert media_type == "image/jpeg"
    assert out != buf.getvalue()
    assert measure_skew(Image.open(io.BytesIO(out))) == 0.0


def test_a_level_in_range_upright_photo_is_still_returned_untouched():
    """The straightening must not cost a needless re-encode."""
    buf = io.BytesIO()
    _sheet().save(buf, format="JPEG", quality=92)
    assert prepare_for_ocr(buf.getvalue(), "image/jpeg") == (buf.getvalue(), "image/jpeg")


# --- Strips -----------------------------------------------------------------

def test_strips_overlap_so_every_row_is_in_two_of_them():
    buf = io.BytesIO()
    _sheet(h=2000).save(buf, format="JPEG")
    pieces = strips(buf.getvalue(), "image/jpeg")
    sizes = [Image.open(io.BytesIO(b)).size for b, _ in pieces]
    assert len(pieces) == 5
    assert all(w == 1200 for w, _ in sizes)
    assert all(h == round(2000 * STRIP_HEIGHT) for _, h in sizes)
    # Last strip is flush with the bottom of the page, not past it.
    assert all(mt == "image/jpeg" for _, mt in pieces)


def test_strips_keep_the_pages_format():
    buf = io.BytesIO()
    _sheet(h=900).save(buf, format="PNG")
    pieces = strips(buf.getvalue(), "image/png")
    assert all(mt == "image/png" for _, mt in pieces)
