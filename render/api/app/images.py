"""Image normalisation for OCR uploads.

Coupons are photographed on a phone. Three things happen to the photo before it
can be sent to a vision model, all done here, once, at upload:

1. **Orientation.** Phone cameras record rotation as an EXIF tag rather than
   rotating the pixels, so a portrait shot can reach the OCR provider sideways.
2. **Size.** The vision APIs cap the resolution they will look at. Anything
   above the cap is downscaled *by them* before the model sees it, so sending a
   50 MP original buys no fidelity — it only costs us the memory to decode it.
3. **Straightening.** A hand-held photo of a sheet is never quite square, and
   on a 78-row grid that is not cosmetic. At 2576 px the row pitch is ~30 px,
   and a tilt of 0.7° drifts the far edge of the page by 2000 × tan(0.7°) ≈
   24 px — most of a row. So the price at the right-hand end of row 55 sits,
   in the photo, level with the fixture name of row 54. That is the mechanism
   behind "line 55 priced as line 54": the read is faithful to a picture in
   which the columns genuinely no longer line up. The skew is measured off
   the text lines themselves and rotated out before the model sees the page.

Otherwise deliberately minimal — no threshold, denoise, or sharpening. That
pipeline is tuned for classic OCR engines that want clean black-on-white;
vision models read the original photograph better, and binarising throws away
information they use (ink separating from paper texture, faint pen marks,
partially shadowed digits).

This module also cuts a prepared photo into overlapping horizontal STRIPS
(`strips`). A strip is the unit of the second, independent read that every
price has to survive (`providers/verified_ocr.py`): a model transcribing a
quarter of a page cannot drift a whole row between the fixture column and the
price columns the way it can across seventy-eight of them.
"""
import io
import logging
import statistics

from PIL import Image, ImageOps

logger = logging.getLogger("uvicorn.error")

# Quality for the re-encode. 4:4:4 (subsampling=0) keeps the chroma at full
# resolution, which matters for small coloured print on a coupon.
JPEG_QUALITY = 90

# The longest edge we keep. This is the vision API's OWN maximum native
# resolution — Claude's high-resolution tier (Sonnet 5 and every 4.7+ model)
# caps images at 2576 px on the long edge and downscales anything larger before
# the model sees it. So this is not a quality trade-off: past this point the
# extra pixels are discarded upstream either way, and the only question is
# whether we spend ~500 MB of our own RAM to hand them over first.
#
# Do NOT lower this to "save memory". Below the API's cap it becomes a real loss
# of detail, and the small print on a coupon is exactly what is read here.
MAX_LONG_EDGE = 2576

# Ceiling on the pixels we will DECODE, checked AFTER the JPEG draft below.
# JPEGs decode straight to a reduced size, so a phone photo never approaches
# this; it bites only on a format with no such trick (a huge PNG), where the
# decode is ~3 bytes per pixel and would take the instance down. An image that
# arrives sideways and oversized is a far smaller problem than one that arrives
# after a restart. Pillow's own decompression-bomb guard sits much higher and is
# about malice, not about our RAM.
MAX_DECODE_PIXELS = 40_000_000

# Largest payload we will hand to the OCR provider. The Claude API rejects any
# image over 10 MB once base64-encoded, and base64 inflates by 4/3 — so the raw
# ceiling is ~7.5 MB and this sits under it. A 2576 px JPEG lands far below;
# this only catches a pathological re-encode, which is sent as JPEG instead.
MAX_PAYLOAD_BYTES = 7_000_000

_ROTATED_TAGS = (2, 3, 4, 5, 6, 7, 8)

# --- Straightening ----------------------------------------------------------

# How far off square a photo is allowed to be before it is measured as
# something other than a tilted sheet. A hand-held shot is within a degree or
# two; past this the "tilt" is more likely a sheet photographed at an angle in
# a way rotation cannot fix, and a confident 6° correction of a page that
# needed 0° would do exactly the damage this exists to prevent.
MAX_SKEW_DEG = 4.0

# Below this the pixels are left alone. Rotating resamples the whole image, and
# a tenth of a degree over the page is a couple of pixels — not worth a
# re-encode of print this small.
MIN_SKEW_DEG = 0.2

# The measurement is made on a copy this wide. Rows of text are ~30 px tall at
# full size, so at a quarter scale they are still clearly separate bands, and
# the thirty-odd trial rotations cost milliseconds instead of seconds.
_MEASURE_WIDTH = 640

# The best trial angle must beat "no rotation" by this factor before it is
# believed. A page with no line structure (a blank wall, a blurred shot) gives
# a flat score surface where the argmax is noise; this keeps noise from being
# applied as a correction.
_SKEW_CONFIDENCE = 1.05


def measure_skew(img: Image.Image) -> float:
    """The angle, in degrees, to pass to Image.rotate to square the text lines.

    Rows of print are horizontal bands of ink. Summed across the page they make
    a profile with a sharp peak per row — but only when the rows are level;
    tilt smears each row over its neighbours and flattens the profile. So the
    tilt is whatever trial rotation makes the profile sharpest, measured as its
    variance. Coarse pass at half-degree steps over ±MAX_SKEW_DEG, then a fine
    pass at tenths around the winner.

    Pure Pillow: the profile is the image resized to ONE pixel wide with a box
    filter, which is exactly the mean of each row. No numpy, nothing to install.
    Returns 0.0 when the measurement is not confident enough to act on.
    """
    grey = img.convert("L")
    if grey.width > _MEASURE_WIDTH:
        scale = _MEASURE_WIDTH / grey.width
        grey = grey.resize(
            (_MEASURE_WIDTH, max(1, round(grey.height * scale))), Image.BOX
        )
    # Ink bright, paper dark, so the fill that rotation exposes at the corners
    # (0 = black here) contributes nothing to any row.
    ink = ImageOps.invert(grey)

    def sharpness(angle: float) -> float:
        turned = ink.rotate(angle, resample=Image.BILINEAR, expand=False, fillcolor=0)
        profile = list(turned.resize((1, turned.height), Image.BOX).tobytes())
        return statistics.pvariance(profile) if len(profile) > 1 else 0.0

    level = sharpness(0.0)
    if level <= 0:
        return 0.0

    coarse = [i * 0.5 for i in range(-int(MAX_SKEW_DEG * 2), int(MAX_SKEW_DEG * 2) + 1)]
    best = max(coarse, key=sharpness)
    fine = [round(best + i * 0.1, 1) for i in range(-4, 5)]
    best = max(fine, key=sharpness)

    if abs(best) < MIN_SKEW_DEG or sharpness(best) < level * _SKEW_CONFIDENCE:
        return 0.0
    return best


def straighten(img: Image.Image, angle: float) -> Image.Image:
    """Rotate the page level, keeping its size. The sliver of corner this
    exposes is filled paper-white, so it reads as margin rather than as a
    black wedge the model might take for a border."""
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    fill = 255 if img.mode == "L" else (255, 255, 255)
    return img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=fill)


# --- Strips -----------------------------------------------------------------

# Each strip is this share of the page tall, and consecutive strips start this
# far apart — so every row except those at the very top and bottom falls inside
# TWO strips, and the page becomes five overlapping pieces. A row cut in half
# by one strip's edge is whole in the next; and a fixture that turns up in two
# strips has been read twice by two separate calls, which is the point.
STRIP_HEIGHT = 0.34
STRIP_STRIDE = 0.17


def strips(image_bytes: bytes, media_type: str) -> list[tuple[bytes, str]]:
    """The page as overlapping horizontal pieces, top to bottom.

    Cut from the PREPARED image (already upright, capped and straightened), so
    a strip is at the same scale the whole-page read saw and its rows are level.
    Returned in the page's own format, or as JPEG if that comes out too large.
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        img.load()
        fmt = img.format or ("PNG" if media_type == "image/png" else "JPEG")
        width, height = img.size
        tall = max(1, round(height * STRIP_HEIGHT))
        step = max(1, round(height * STRIP_STRIDE))
        tops: list[int] = []
        top = 0
        while True:
            tops.append(min(top, height - tall))
            if top + tall >= height:
                break
            top += step
        out = []
        for top in dict.fromkeys(tops):   # ordered, de-duplicated
            piece = img.crop((0, top, width, top + tall))
            try:
                out.append(_encode(piece, fmt, media_type))
            finally:
                piece.close()
        return out


# --- Upload preparation -----------------------------------------------------

def prepare_for_ocr(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Return (bytes, media_type) ready to store and to send to the OCR provider.

    Applies the EXIF orientation tag to the pixels, caps the long edge at the
    vision API's own maximum resolution, and rotates the page level. Returns
    the ORIGINAL bytes unchanged when none of the three is needed — re-encoding
    a JPEG that was already upright, in range and square would lose detail for
    nothing.

    Any failure also returns the original: a mis-rotated or oversized image
    still OCRs imperfectly, but a raised exception loses the upload entirely.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # 0x0112 = Orientation. 1 means "already upright"; absent means no
            # tag was recorded. Reading the EXIF block never touches the pixels,
            # since Image.open is lazy.
            orientation = img.getexif().get(0x0112, 1)
            needs_rotate = orientation in _ROTATED_TAGS
            needs_resize = max(img.width, img.height) > MAX_LONG_EDGE

            fmt = img.format or ("PNG" if media_type == "image/png" else "JPEG")
            was = f"{img.width}x{img.height}"

            target = _capped(img.width, img.height)

            # For a JPEG this decodes at a reduced scale in the DCT domain —
            # the full-size bitmap is never materialised. An 8064x6048 photo
            # comes off the decoder at 2016x1512 before anything else touches
            # it. A no-op for other formats.
            #
            # The target must match the image's ASPECT RATIO, not be a square
            # box: draft picks its scale from min(w // want_w, h // want_h), so
            # a square (2576, 2576) against a 4:3 photo scores 1 on the short
            # edge and silently declines to reduce at all — which is exactly
            # the full-size decode this exists to avoid.
            img.draft("RGB", target)

            pixels = (img.width or 0) * (img.height or 0)
            if pixels > MAX_DECODE_PIXELS:
                logger.warning(
                    "Image decodes to %.1f MP — above the %.0f MP ceiling; "
                    "storing as uploaded, unrotated and unresized",
                    pixels / 1e6, MAX_DECODE_PIXELS / 1e6,
                )
                return image_bytes, media_type

            # Shrink BEFORE rotating. exif_transpose() allocates a second copy,
            # so doing it second means that copy is of the small image rather
            # than of the original; rotating does not change which edge is
            # longer, so the order does not change the result. Peak here is the
            # drafted decode plus the reduced copy.
            small = img.resize(target, Image.LANCZOS) if needs_resize else img
            try:
                if needs_rotate:
                    # resize() carries the source's info dict across, so the
                    # orientation tag survives the shrink and is applied here.
                    upright = ImageOps.exif_transpose(small) or small
                else:
                    upright = small
                try:
                    # Measured only once the page is the right way up: the
                    # profile is of horizontal rows, and a sideways page has
                    # none. Measured at the send size too, which is what the
                    # correction is applied to.
                    skew = measure_skew(upright)
                    if not needs_rotate and not needs_resize and skew == 0.0:
                        return image_bytes, media_type
                    level = straighten(upright, skew) if skew else upright
                    try:
                        now = f"{level.width}x{level.height}"
                        data, out_type = _encode(level, fmt, media_type)
                    finally:
                        if level is not upright:
                            level.close()
                finally:
                    if upright is not small:
                        upright.close()
            finally:
                if small is not img:
                    small.close()

            logger.info(
                "Prepared upload for OCR: orientation %s, skew %+.1f°, %s -> %s, "
                "%.1f -> %.1f MB",
                orientation, skew, was, now, len(image_bytes) / 1e6, len(data) / 1e6,
            )
            return data, out_type
    except Exception as exc:
        logger.warning("Image prepare failed, using original: %s", exc)
        return image_bytes, media_type


def _capped(width: int, height: int) -> tuple[int, int]:
    """The size with the long edge at MAX_LONG_EDGE, aspect ratio preserved.

    Returns the size unchanged when it is already in range — the caller still
    hands this to draft(), which is happy to be told "no reduction".
    """
    longest = max(width, height)
    if longest <= MAX_LONG_EDGE:
        return width, height
    scale = MAX_LONG_EDGE / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode(img: Image.Image, fmt: str, media_type: str) -> tuple[bytes, str]:
    """Serialise in the source format, falling back to JPEG if it comes out too big.

    A PNG re-encode of a photograph can run to tens of megabytes, which the OCR
    provider would refuse outright. Better a JPEG the model can read than a PNG
    it never receives.
    """
    out = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
        return out.getvalue(), "image/jpeg"

    img.save(out, format=fmt)
    data = out.getvalue()
    if len(data) <= MAX_PAYLOAD_BYTES:
        return data, media_type

    logger.info(
        "%s re-encode is %.1f MB, above the %.1f MB send limit — using JPEG instead",
        fmt, len(data) / 1e6, MAX_PAYLOAD_BYTES / 1e6,
    )
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
    return out.getvalue(), "image/jpeg"
