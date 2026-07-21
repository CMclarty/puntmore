import json
import base64

import google.genai as genai
import google.genai.types as gtypes

from .base import OCRProvider, ExtractedRow, ExtractedCoupon
from .claude_ocr import SYSTEM_PROMPT, USER_PROMPT, _parse_ladder

MODEL = "gemini-2.0-flash"

# One contract across providers: a second prompt meant a second set of
# bugs to find.
_PROMPT = SYSTEM_PROMPT + "\n\n" + USER_PROMPT


class GeminiOCRProvider(OCRProvider):
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    def extract(self, image_bytes: bytes, media_type: str) -> ExtractedCoupon:
        part = gtypes.Part.from_bytes(data=image_bytes, mime_type=media_type)
        response = self._client.models.generate_content(
            model=MODEL,
            contents=[part, _PROMPT],
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

        rows = []
        for row in data.get("selections") or []:
            match = (row.get("match") or "").strip()
            if not match:
                continue
            odds = row.get("your_odds")
            # Rows without odds are KEPT: list coupons print none, and dropping
            # them discards the entire coupon.
            rows.append(
                ExtractedRow(
                    match=match,
                    market=(row.get("market") or None),
                    your_odds=float(odds) if odds is not None else None,
                    kickoff_text=(row.get("kickoff") or None),
                )
            )
        return ExtractedCoupon(
            rows=rows,
            title=data.get("title") or None,
            period_text=data.get("period_text") or None,
            payout_ladder=_parse_ladder(data.get("payout_ladder")),
        )
