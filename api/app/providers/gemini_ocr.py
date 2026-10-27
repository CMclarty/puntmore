import json
import base64

from .base import OCRProvider, ExtractedCoupon, OcrHint
from .claude_ocr import (
    MAX_TOKENS,
    SLIP_MAX_TOKENS,
    SYSTEM_PROMPT,
    OCRTruncated,
    build_user_prompt,
    _parse_ladder,
    _strip_code_fence,
    read_rows,
)
from .slip_ocr import SLIP_PROMPT, SLIP_SYSTEM, parse_slip

MODEL = "gemini-2.0-flash"


def _hit_token_cap(response) -> bool:
    """Did the response stop because it ran out of output tokens?

    Read defensively: the finish reason is an enum whose spelling has moved
    between SDK versions, and a truncated read that goes UNDETECTED is the
    failure this exists to prevent — so match on the name, and treat an absent
    or unreadable reason as "not truncated" (the JSON parse still fails loudly
    below).
    """
    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        name = getattr(reason, "name", None) or str(reason or "")
        if "MAX_TOKEN" in name.upper():
            return True
    return False


class GeminiOCRProvider(OCRProvider):
    def __init__(self, api_key: str):
        # Imported here, not at the top: the SDK is ~49 MB resident and this
        # module is imported by every boot, while Gemini is only the provider
        # when GOOGLE_API_KEY is set (production uses Claude).
        import google.genai as genai
        self._client = genai.Client(api_key=api_key)

    @property
    def _types(self):
        import google.genai.types as gtypes
        return gtypes

    def extract(
        self, image_bytes: bytes, media_type: str, hint: OcrHint | None = None
    ) -> ExtractedCoupon:
        gtypes = self._types
        part = gtypes.Part.from_bytes(data=image_bytes, mime_type=media_type)
        # One contract across providers: a second prompt meant a second set of
        # bugs to find. Same output budget too — a whole-sheet read is the same
        # size whoever produces it.
        response = self._client.models.generate_content(
            model=MODEL,
            contents=[part, SYSTEM_PROMPT + "\n\n" + build_user_prompt(hint)],
            config=gtypes.GenerateContentConfig(max_output_tokens=MAX_TOKENS),
        )

        # Truncation, checked before parsing for the same reason as the Claude
        # path: half a coupon looks exactly like a short coupon.
        if _hit_token_cap(response):
            raise OCRTruncated(
                f"OCR output truncated at max_output_tokens ({MAX_TOKENS}) — the "
                f"read is incomplete and has been discarded rather than saved "
                f"in part"
            )

        raw = response.text.strip()
        # Strip markdown code fences if the model wraps its output
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw)
        if isinstance(data, list):
            data = {"selections": data}

        # Rows without odds are KEPT: list coupons print none, and dropping
        # them discards the entire coupon.
        rows, malformed = read_rows(data)
        return ExtractedCoupon(
            rows=rows,
            title=data.get("title") or None,
            period_text=data.get("period_text") or None,
            payout_ladder=_parse_ladder(data.get("payout_ladder")),
            malformed_pairs=malformed,
        )

    def read_slip(self, image_bytes: bytes, media_type: str):
        """One terminal bet (AW 26) — the shared slip prompt (slip_ocr.py)."""
        gtypes = self._types
        part = gtypes.Part.from_bytes(data=image_bytes, mime_type=media_type)
        response = self._client.models.generate_content(
            model=MODEL,
            contents=[part, SLIP_SYSTEM + "\n\n" + SLIP_PROMPT],
            config=gtypes.GenerateContentConfig(max_output_tokens=SLIP_MAX_TOKENS),
        )
        if _hit_token_cap(response):
            raise OCRTruncated(f"slip read truncated at max_output_tokens ({SLIP_MAX_TOKENS})")
        return parse_slip(json.loads(_strip_code_fence(response.text.strip())))
