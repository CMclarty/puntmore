import base64
import json
import re

import anthropic

from .base import OCRProvider, ExtractedRow, ExtractedCoupon, OcrHint

# Sonnet 5 (not Haiku 4.5) for the resolution: Haiku caps images at 1568px on
# the long edge, Sonnet 5 accepts 2576px. Coupon photos are dense small print —
# downscaling is where misread odds come from. ~150 images/month is ~£1.50.
# Do NOT send resized/compressed images; the fidelity is the whole point.
MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You read printed bookmaker football coupons.
Return ONLY valid JSON — no markdown, no prose, no explanation.

A coupon is a printed LIST OF FIXTURES for ONE market (e.g. "GG BONUS" = both
teams to score). Coupons come in two shapes:

  LADDER coupons print NO odds beside each fixture. They pay a fixed price for
  getting N selections correct, printed as a ladder ("All 3 correct 9/2", ...).

  PRICED coupons print odds beside every fixture, usually as a GRID: the
  fixtures run down the left, and each market has its own labelled column block
  across the page (e.g. "TEAM TO SCORE 1 OR MORE GOALS", then "2 OR MORE", then
  "3 OR MORE"). Most such blocks are split into TWO sub-columns.

THE COLUMN ORDER IS ALWAYS THE SAME, ON EVERY COUPON:

    LEFT sub-column  = the HOME team's price   -> "home_odds"
    RIGHT sub-column = the AWAY team's price   -> "away_odds"

Use the POSITION of the price on the page. The sub-columns are usually headed
"H" and "A", but read the left-to-right order, not the letters: a smudged or
missing header does not change which price is which. The fixture is printed the
same way round — "Mariehamn v Lahti" means Mariehamn (named first) is at home,
so Mariehamn's price is the LEFT one of that row's pair.

NEVER swap the pair because one price "looks" like it belongs to the other
team. A short price on the right is an away favourite, not a mistake to fix.
Copy the two prices in the order they are printed and nothing else.

Return exactly this shape:
{
  "title": string or null,
  "period_text": string or null,
  "payout_ladder": [ {"picks": 3, "fractional": "9/2"}, ... ],
  "selections": [
    {"match": "...", "kickoff": "...", "market": null,
     "your_odds": null, "home_odds": null, "away_odds": null}, ...
  ]
}

Rules:
- Extract EVERY fixture row, in reading order — including rows under a date
  sub-heading ("Wednesday 29th July"), which are part of the same list. A long
  coupon runs to 78 rows. Do not stop early and do not summarise; a partial
  list is worse than none.
- "match": the fixture exactly as printed, e.g. "Mariehamn v Lahti".
- "kickoff": ALWAYS "DAY HH:MM" with a three-letter day, e.g. "MON 17:00".
  Long lists group rows under a date sub-heading ("Wednesday 29th July") and
  print only a time on each row — carry that heading's day down onto every row
  beneath it, so a 16:00 row under "Wednesday 29th July" is "WED 16:00". Null
  only if no time is printed at all.
- ODDS ARE PRINTED AS FRACTIONS and often use a HYPHEN, not a slash: "6-4"
  means 6/4, "4-11" means 4/11, "10-1" means 10/1. "EVS" means evens (1/1).
  Copy them EXACTLY as printed, as strings — do NOT convert to decimal.
- "home_odds" / "away_odds": for a PRICED grid, the LEFT and the RIGHT price of
  the REQUESTED market block's pair, on that fixture's row — left into
  "home_odds", right into "away_odds", always that way round. Read straight
  across the row and take care not to drift into a neighbouring market's
  columns — they look alike.
- "your_odds": only for a priced coupon that prints ONE price per fixture (no
  H/A split). Otherwise null.
- Return null for any price you cannot read rather than guessing or copying a
  neighbouring row. A missing price is recoverable; a wrong one is not.
- "market": only when a per-fixture selection is printed beside the fixture
  (e.g. a named team). Null otherwise — the market is known from which coupon
  this is, so do not guess it from the header.
- "payout_ladder": every rung you can read, as printed fractions. Return [] if
  the coupon shows none. Omit rungs cut off or illegible rather than guessing.
- "period_text": the date range printed on the coupon, e.g.
  "Monday 20th - Thursday 23rd July"."""

USER_PROMPT = (
    "Read this coupon. Return every fixture, both columns (left = home, "
    "right = away), plus the payout ladder and date range."
)


def build_user_prompt(hint: OcrHint | None) -> str:
    """The per-upload instruction. The coupon TYPE is chosen on the upload form,
    so which market block to read is known — telling the model narrows a grid of
    look-alike columns to the one that was actually photographed for."""
    if hint is None or not hint.priced:
        return USER_PROMPT
    parts = [
        "Read this coupon. Return EVERY fixture row (a long coupon has 78), "
        "plus the date range."
    ]
    if hint.board_label:
        parts.append(
            f"This is a PRICED grid and the market to read is the column block "
            f'headed "{hint.board_label}". Ignore every other market block on '
            f"the sheet."
        )
    else:
        parts.append("This is a PRICED coupon: read the price beside each fixture.")
    if hint.two_sided:
        # Position, not the column letters: the letters are what a smudged or
        # cropped header takes away, and the left-to-right order is fixed on
        # every coupon. Reading them as labels is how prices ended up on the
        # wrong team.
        parts.append(
            "That block has two sub-columns per fixture. The LEFT one is the "
            'HOME team\'s price and goes in "home_odds"; the RIGHT one is the '
            'AWAY team\'s and goes in "away_odds". Always that way round — go '
            "by the left-to-right position, not by the H/A headers, and do not "
            "swap them because a price looks like it suits the other team. "
            'Copy them exactly as printed (fractions such as "6-4", "4-11", '
            'or "EVS").'
        )
    else:
        parts.append('Put each fixture\'s price in "your_odds", exactly as printed.')
    return " ".join(parts)


def _fractional_to_decimal(fraction: str) -> float | None:
    """"9/2" -> 5.5. Ladders are printed as fractions, priced as decimals.

    Boards print the separator as a slash OR a hyphen ("6-4" is 6/4, not a
    range), and evens as "EVS"/"EVENS". All three are the same price to us.
    """
    if fraction is None:
        return None
    text = str(fraction).strip().upper().replace(",", "")
    if not text:
        return None
    if text in ("EVS", "EVENS", "EVEN", "1-1", "1/1"):
        return 2.0
    try:
        num, den = re.split(r"[/\-]", text, maxsplit=1)
        return float(num) / float(den) + 1.0
    except (ValueError, ZeroDivisionError, AttributeError):
        return None


def parse_odds(value) -> float | None:
    """A printed price -> decimal odds, or None if it can't be read.

    Coupons print fractions ("6-4", "4-11", "EVS"); a decimal may still arrive
    from a differently-shaped sheet or another provider, so both are accepted.
    Anything unreadable is None — an unpriced leg beats a wrongly priced one,
    and this used to raise ValueError and 502 the whole upload.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        odds = float(value)
        return odds if odds > 1.0 else None
    text = str(value).strip()
    if not text:
        return None
    # A bare number is already decimal odds; anything else is fractional.
    if re.fullmatch(r"\d+(\.\d+)?", text):
        odds = float(text)
        return odds if odds > 1.0 else None
    decimal = _fractional_to_decimal(text)
    return round(decimal, 4) if decimal is not None else None


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


def parse_rows(data: dict) -> list[ExtractedRow]:
    """The `selections` array -> rows. Shared by every OCR provider so a fix
    here (fractional odds, H/A prices) lands for all of them at once."""
    rows = []
    for row in data.get("selections") or []:
        match = (row.get("match") or "").strip()
        if not match:
            continue  # a row with no fixture cannot be matched or priced
        rows.append(
            ExtractedRow(
                match=match,
                market=(row.get("market") or None),
                your_odds=parse_odds(row.get("your_odds")),
                kickoff_text=(row.get("kickoff") or None),
                home_odds=parse_odds(row.get("home_odds")),
                away_odds=parse_odds(row.get("away_odds")),
            )
        )
    return rows


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

    def extract(
        self, image_bytes: bytes, media_type: str, hint: OcrHint | None = None
    ) -> ExtractedCoupon:
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
                        {"type": "text", "text": build_user_prompt(hint)},
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

        return ExtractedCoupon(
            rows=parse_rows(data),
            title=data.get("title") or None,
            period_text=data.get("period_text") or None,
            payout_ladder=_parse_ladder(data.get("payout_ladder")),
        )
