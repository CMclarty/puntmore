import json
import base64

import google.genai as genai
import google.genai.types as gtypes

from .base import OCRProvider, ExtractedCoupon, OcrHint
from .claude_ocr import SYSTEM_PROMPT, build_user_prompt, _parse_ladder, parse_rows

MODEL = "gemini-2.0-flash"


class GeminiOCRProvider(OCRProvider):
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    def extract(
        self, image_bytes: bytes, media_type: str, hint: OcrHint | None = None
    ) -> ExtractedCoupon:
        part = gtypes.Part.from_bytes(data=image_bytes, mime_type=media_type)
        # One contract across providers: a second prompt meant a second set of
        # bugs to find.
        response = self._client.models.generate_content(
            model=MODEL,
            contents=[part, SYSTEM_PROMPT + "\n\n" + build_user_prompt(hint)],
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
        return ExtractedCoupon(
            rows=parse_rows(data),
            title=data.get("title") or None,
            period_text=data.get("period_text") or None,
            payout_ladder=_parse_ladder(data.get("payout_ladder")),
        )
