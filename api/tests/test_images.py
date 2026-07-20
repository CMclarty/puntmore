"""EXIF orientation normalisation for phone-camera uploads."""
import io

from PIL import Image

from app.images import normalise_orientation


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


def test_upright_image_is_returned_untouched():
    # Must not re-encode: a needless JPEG round-trip loses detail that small
    # print on a coupon depends on.
    plain = _jpeg()
    assert normalise_orientation(plain, "image/jpeg") is plain
    upright = _jpeg(orientation=1)
    assert normalise_orientation(upright, "image/jpeg") is upright


def test_rotated_image_is_actually_rotated():
    rotated = _jpeg(orientation=6, size=(400, 200))  # 6 = rotate 90° CW
    out = normalise_orientation(rotated, "image/jpeg")
    assert Image.open(io.BytesIO(out)).size == (200, 400)


def test_unreadable_input_does_not_raise():
    # A mis-rotated image still OCRs imperfectly; a raised exception loses the
    # upload entirely. Always fall back to the original bytes.
    junk = b"not an image"
    assert normalise_orientation(junk, "image/jpeg") == junk
