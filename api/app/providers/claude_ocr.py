import base64
import json

import anthropic

from .base import OCRProvider, ExtractedRow

MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """You are an OCR assistant that extracts betting data from coupon images.
Return ONLY valid JSON — no markdown, no prose, no explanation.
Output a JSON array of objects. Each object must have exactly these keys:
  "match"     : string  — the fixture, e.g. "Arsenal vs Chelsea"
  "market"    : string  — the bet type, e.g. "Win and Over 2.5"
  "your_odds" : number  — the decimal odds offered, e.g. 1.85
If you cannot read a field clearly, use null for that field.
Output format: [{"match": "...", "market": "...", "your_odds": 1.85}, ...]"""


class ClaudeOCRProvider(OCRProvider):
    def __init__(self, api_key: str):
        self._client = anthropic.Anthropic(api_key=api_key)

    def extract(self, image_bytes: bytes, media_type: str) -> list[ExtractedRow]:
        encoded = base64.standard_b64encode(image_bytes).decode("utf-8")

        message = self._client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract all betting rows from this coupon image.",
                        },
                    ],
                }
            ],
        )

        raw = message.content[0].text.strip()
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
