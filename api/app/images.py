"""Image normalisation for OCR uploads.

Coupons are photographed on a phone. Phone cameras record orientation as an
EXIF tag rather than rotating the pixels, so a portrait shot can reach the OCR
provider sideways. This applies the tag to the pixels.

Deliberately minimal — no deskew, threshold, denoise, or sharpening. That
pipeline is tuned for classic OCR engines that want clean black-on-white;
vision models read the original photograph better, and binarising throws away
information they use (ink separating from paper texture, faint pen marks,
partially shadowed digits). Add preprocessing when real failures show it is
needed, not before.
"""
import io
import logging

from PIL import Image, ImageOps

logger = logging.getLogger("uvicorn.error")

# Quality for the re-encode after rotating. Only applies when a rotation was
# actually needed; unrotated images are returned byte-identical.
JPEG_QUALITY = 95

# Ceiling on the pixels we will DECODE. Nothing here resizes an image — the
# decode is the memory cost, at roughly 3 bytes per pixel for the source plus
# another copy for the rotated result. 80 MP is far above any phone camera
# (a 2026 flagship is ~50 MP) and bounds that pair at ~480 MB... which is why
# the check exists at all: past this point rotating in memory would take the
# instance down, and an image that arrives sideways is a far smaller problem
# than an image that arrives after a restart. Pillow's own decompression-bomb
# guard sits much higher and is about malice, not about our RAM.
MAX_DECODE_PIXELS = 80_000_000


def normalise_orientation(image_bytes: bytes, media_type: str) -> bytes:
    """Apply the EXIF orientation tag to the pixels.

    Returns the ORIGINAL bytes unchanged when no rotation is needed — re-encoding
    a JPEG that was already upright would lose detail for nothing. Any failure
    also returns the original: a mis-rotated image still OCRs imperfectly, but a
    raised exception loses the upload entirely.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            exif = img.getexif()
            # 0x0112 = Orientation. 1 means "already upright"; absent means no
            # tag was recorded. Either way there is nothing to do — and nothing
            # has been decoded yet, since Image.open is lazy and reading the
            # EXIF block never touches the pixels. This is the common path and
            # it costs only the bytes we were handed.
            if exif.get(0x0112, 1) == 1:
                return image_bytes

            # Everything below DECODES, and holds two full-size copies while it
            # does. Refuse rather than risk the process on an image no phone
            # produces; a sideways coupon still OCRs, a dead instance doesn't.
            pixels = (img.width or 0) * (img.height or 0)
            if pixels > MAX_DECODE_PIXELS:
                logger.warning(
                    "Image is %.1f MP — above the %.0f MP rotate ceiling; "
                    "storing as uploaded without applying EXIF orientation",
                    pixels / 1e6, MAX_DECODE_PIXELS / 1e6,
                )
                return image_bytes

            rotated = ImageOps.exif_transpose(img)
            if rotated is None:
                return image_bytes

            out = io.BytesIO()
            fmt = img.format or ("PNG" if media_type == "image/png" else "JPEG")
            if fmt == "JPEG":
                rotated.save(out, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
            else:
                rotated.save(out, format=fmt)
            # Free the rotated copy before returning: the caller is about to
            # base64-encode the result for the OCR request, so the peak is
            # whatever is still alive at that moment.
            rotated.close()
            logger.info("Applied EXIF orientation %s to upload", exif.get(0x0112))
            return out.getvalue()
    except Exception as exc:
        logger.warning("EXIF orientation normalise failed, using original: %s", exc)
        return image_bytes
