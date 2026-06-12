import json
import base64

import google.genai as genai
import google.genai.types as gtypes

from .base import OCRProvider, ExtractedRow

MODEL = "gemini-2.0-flash"

_PROMPT = """Extract all betting rows from this coupon image.
Return ONLY valid JSON — no markdown, no prose, no explanation.
Output a JSON array of objects. Each object must have exactly these keys:
  "match"     : string  — the fixture, e.g. "Arsenal vs Chelsea"
  "market"    : string  — the bet type, e.g. "Win and Over 2.5"
  "your_odds" : number  — the decimal odds offered, e.g. 1.85
If you cannot read a field clearly, use null for that field.
Output format: [{"match": "...", "market": "...", "your_odds": 1.85}, ...]"""


class GeminiOCRProvider(OCRProvider):
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    def extract(self, image_bytes: bytes, media_type: str) -> list[ExtractedRow]:
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

        rows = json.loads(raw)
        results = []
        for row in rows:
            odds = row.get("your_odds")
            if odds is None:
                continue
            results.append(
                ExtractedRow(
                    match=row.get("match") or "",
                    market=row.get("market") or "",
                    your_odds=float(odds),
                )
            )
        return results
