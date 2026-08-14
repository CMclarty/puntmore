"""Image normalisation for OCR uploads.

Coupons are photographed on a phone. Two things have to happen before the photo
can be sent to a vision model, and both are done here, once, at upload:

1. **Orientation.** Phone cameras record rotation as an EXIF tag rather than
   rotating the pixels, so a portrait shot can reach the OCR provider sideways.
2. **Size.** The vision APIs cap the resolution they will look at. Anything
   above the cap is downscaled *by them* before the model sees it, so sending a
   50 MP original buys no fidelity — it only costs us the memory to decode it.

Deliberately minimal otherwise — no deskew, threshold, denoise, or sharpening.
That pipeline is tuned for classic OCR engines that want clean black-on-white;
vision models read the original photograph better, and binarising throws away
information they use (ink separating from paper texture, faint pen marks,
partially shadowed digits). Add preprocessing when real failures show it is
needed, not before.
"""
import io
import logging

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


def prepare_for_ocr(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Return (bytes, media_type) ready to store and to send to the OCR provider.

    Applies the EXIF orientation tag to the pixels and caps the long edge at the
    vision API's own maximum resolution. Returns the ORIGINAL bytes unchanged
    when neither is needed — re-encoding a JPEG that was already upright and in
    range would lose detail for nothing.

    Any failure also returns the original: a mis-rotated or oversized image
    still OCRs imperfectly, but a raised exception loses the upload entirely.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # 0x0112 = Orientation. 1 means "already upright"; absent means no
            # tag was recorded. Reading the EXIF block never touches the pixels,
            # since Image.open is lazy — so the early return below costs only
            # the bytes we were handed.
            orientation = img.getexif().get(0x0112, 1)
            needs_rotate = orientation in _ROTATED_TAGS
            needs_resize = max(img.width, img.height) > MAX_LONG_EDGE
            if not needs_rotate and not needs_resize:
                return image_bytes, media_type

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
                    now = f"{upright.width}x{upright.height}"
                    data, out_type = _encode(upright, fmt, media_type)
                finally:
                    if upright is not small:
                        upright.close()
            finally:
                if small is not img:
                    small.close()

            logger.info(
                "Prepared upload for OCR: orientation %s, %s -> %s, %.1f -> %.1f MB",
                orientation, was, now, len(image_bytes) / 1e6, len(data) / 1e6,
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
        img.save(out, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
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
