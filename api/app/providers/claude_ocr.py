import base64
import json

import anthropic

from .base import OCRProvider, ExtractedRow

# Sonnet 5 (not Haiku 4.5) for the resolution: Haiku caps images at 1568px on
# the long edge, Sonnet 5 accepts 2576px. Coupon photos are dense small print —
# downscaling is where misread odds come from. ~150 images/month is ~£1.50.
# Do NOT send resized/compressed images; the fidelity is the whole point.
MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are an OCR assistant that extracts betting data from coupon images.
Return ONLY valid JSON — no markdown, no prose, no explanation.
Output a JSON array of objects. Each object must have exactly these keys:
  "match"     : string  — the fixture, e.g. "Arsenal vs Chelsea"
  "market"    : string  — the SELECTION, starting with the selected team where
                the bet names one, e.g. "Arsenal Win and Over 2.5" or
                "Arsenal to Score 2". For bets that name no team, give the bet
                alone, e.g. "Over 2.5", "GG No Draw".
  "your_odds" : number  — the decimal odds offered, e.g. 1.85
The selected team MUST appear in "market" when the bet is on one team. Pricing
resolves which side was backed from this field alone — "Win and Over 2.5"
without a team cannot be priced, because it does not say who is expected to win.
If you cannot read a field clearly, use null for that field.
Output format: [{"match": "...", "market": "...", "your_odds": 1.85}, ...]"""


def _strip_code_fence(text: str) -> str:
    """Tolerate ```json fences despite the prompt forbidding them.

    Models add them often enough that failing the whole upload over formatting
    would be the wrong trade.
    """
    if not text.startswith("```"):
        return text
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return body.rsplit("```", 1)[0].strip()


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

        # Take the first TEXT block, not content[0]: the response can lead with
        # a thinking block, and indexing blindly raised AttributeError on every
        # single image ('ThinkingBlock' object has no attribute 'text').
        raw = next(
            (b.text for b in message.content if getattr(b, "type", None) == "text"),
            None,
        )
        if raw is None:
            raise ValueError("OCR response contained no text block")

        rows = json.loads(_strip_code_fence(raw.strip()))
        results = []
        for row in rows:
            odds = row.get("your_odds")
            # Odds are OPTIONAL: flat-payout coupons (GG Bonus / GG No Draw)
            # print no per-selection price. A row with no odds is still a real
            # selection — dropping it would silently lose the whole coupon.
            results.append(
                ExtractedRow(
                    match=row.get("match") or "",
                    market=row.get("market") or "",
                    your_odds=float(odds) if odds is not None else None,
                )
            )
        return results
