import base64
import json

import anthropic

from .base import OCRProvider, ExtractedRow, ExtractedCoupon

# Sonnet 5 (not Haiku 4.5) for the resolution: Haiku caps images at 1568px on
# the long edge, Sonnet 5 accepts 2576px. Coupon photos are dense small print —
# downscaling is where misread odds come from. ~150 images/month is ~£1.50.
# Do NOT send resized/compressed images; the fidelity is the whole point.
MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You read printed bookmaker football coupons.
Return ONLY valid JSON — no markdown, no prose, no explanation.

A coupon is a printed LIST OF FIXTURES for ONE market (e.g. "GG BONUS" = both
teams to score). Most list coupons print NO odds beside each fixture. Instead
they pay a fixed price for getting N selections correct, printed as a ladder
("All 3 correct 9/2", "All 4 correct 9/1", ...).

Return exactly this shape:
{
  "title": string or null,
  "period_text": string or null,
  "payout_ladder": [ {"picks": 3, "fractional": "9/2"}, ... ],
  "selections": [
    {"match": "...", "kickoff": "...", "market": null, "your_odds": null}, ...
  ]
}

Rules:
- Extract EVERY fixture row, in reading order. Coupons are usually laid out in
  TWO COLUMNS — read the left column fully, then the right. Do not stop early
  and do not summarise; a partial list is worse than none.
- "match": the fixture exactly as printed, e.g. "Mariehamn v Lahti".
- "kickoff": day and time exactly as printed, e.g. "MON 17:00". Null if absent.
- "your_odds": the decimal odds printed beside THAT fixture, or null. Most list
  coupons have none — return null rather than inventing a price or copying one
  from the payout ladder.
- "market": only when a per-fixture selection is printed beside the fixture
  (e.g. a named team). Null otherwise — the market is known from which coupon
  this is, so do not guess it from the header.
- "payout_ladder": every rung you can read, as printed fractions. Return [] if
  the coupon shows none. Omit rungs cut off or illegible rather than guessing.
- "period_text": the date range printed on the coupon, e.g.
  "Monday 20th - Thursday 23rd July"."""

USER_PROMPT = (
    "Read this coupon. Return every fixture, both columns, plus the payout "
    "ladder and date range."
)


def _fractional_to_decimal(fraction: str) -> float | None:
    """"9/2" -> 5.5. Ladders are printed as fractions, priced as decimals."""
    try:
        num, den = fraction.replace(",", "").split("/")
        return float(num) / float(den) + 1.0
    except (ValueError, ZeroDivisionError, AttributeError):
        return None


def _parse_ladder(raw) -> dict[int, float]:
    """Payout ladder as {picks: decimal}, skipping anything unreadable.

    A rung we cannot parse is dropped rather than guessed — the ladder decides
    what a combination pays, so a wrong rung is worse than a missing one.
    """
    ladder: dict[int, float] = {}
    for rung in raw or []:
        try:
            picks = int(rung.get("picks"))
        except (TypeError, ValueError):
            continue
        decimal = _fractional_to_decimal(rung.get("fractional", ""))
        if decimal is not None and picks > 0:
            ladder[picks] = round(decimal, 4)
    return ladder


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

    def extract(self, image_bytes: bytes, media_type: str) -> ExtractedCoupon:
        encoded = base64.standard_b64encode(image_bytes).decode("utf-8")

        message = self._client.messages.create(
            model=MODEL,
            # A long list runs to ~75 fixtures; 2048 truncated it mid-array.
            max_tokens=8192,
            # Sonnet 5 runs extended thinking BY DEFAULT, and thinking tokens
            # count against max_tokens. On a long coupon the thinking block
            # (~2k tokens) plus the JSON exceeded 8192, truncating the JSON
            # mid-array → JSONDecodeError → a 502 that deleted the upload. This
            # is structured extraction; thinking adds only overhead and variance,
            # so disable it and keep the whole budget for the JSON (the JSON
            # alone is ~4k tokens for a full 75-fixture coupon).
            thinking={"type": "disabled"},
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
                        {"type": "text", "text": USER_PROMPT},
                    ],
                }
            ],
        )

        # If the model still hits the cap, the JSON is truncated and will not
        # parse. Fail LOUDLY with the cause rather than as an opaque
        # JSONDecodeError — a bare 502 hid this exact bug for a long time.
        if message.stop_reason == "max_tokens":
            raise ValueError(
                "OCR output truncated (hit max_tokens) — coupon too long for the "
                "token budget; raise max_tokens"
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

        data = json.loads(_strip_code_fence(raw.strip()))
        # Tolerate a bare array as well as the documented object: an older
        # prompt returned one, and a stray format change should not lose a
        # coupon the user has already photographed.
        if isinstance(data, list):
            data = {"selections": data}

        rows = []
        for row in data.get("selections") or []:
            match = (row.get("match") or "").strip()
            if not match:
                continue  # a row with no fixture cannot be matched or priced
            odds = row.get("your_odds")
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
