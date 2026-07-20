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
            # tag was recorded. Either way there is nothing to do.
            if exif.get(0x0112, 1) == 1:
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
            logger.info("Applied EXIF orientation %s to upload", exif.get(0x0112))
            return out.getvalue()
    except Exception as exc:
        logger.warning("EXIF orientation normalise failed, using original: %s", exc)
        return image_bytes
