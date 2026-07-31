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

# The whole sheet is transcribed in one response: 78 fixtures × six market
# blocks, each a heading and a price pair. That is several times the JSON a
# single-block read produced, and the old 8192 budget truncated it — which
# reads downstream as a coupon that was simply shorter than it is.
#
# Sonnet 5 allows 128k output tokens; this leaves generous headroom over a
# full sheet without pretending a bigger number is free (unused budget costs
# nothing, a truncated read costs the upload). Requests are STREAMED because
# the SDK times out a non-streaming request long before a response this size
# finishes.
MAX_TOKENS = 64000


class OCRTruncated(ValueError):
    """The model hit its output cap mid-JSON, so the read is incomplete.

    A ValueError subclass so existing handling still catches it, but its own
    type because a partial coupon must never be saved as a whole one:
    the fixtures that didn't fit are indistinguishable from fixtures the sheet
    never carried. Callers let this fail the upload — re-scanning is cheap,
    a silently half-read coupon is not detectable at all.
    """


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

TRANSCRIBE EVERY BLOCK, DO NOT CHOOSE ONE. Give each row a "prices" object
keyed by the block's heading COPIED AS PRINTED, with that block's two prices as
a two-item list in the order they are printed, left first:

  "prices": {
    "TEAM TO SCORE 1 OR MORE GOALS": ["1-3", "4-11"],
    "TEAM TO SCORE 2 OR MORE GOALS": ["6-4", "8-13"],
    "WIN & 2 OR MORE GOALS":         ["11-8", "9-4"]
  }

DO NOT LABEL THE TWO PRICES in a pair. Which team each belongs to is decided
from the print order by the software reading your output, not by you. The
sub-columns are usually headed "H" and "A"; ignore those headers, ignore which
price looks like a favourite's, and do not reorder a pair for any reason. If
only one of the two is legible, put null in the other slot and keep the
positions: ["6-4", null].

Blocks you cannot read, or that are cut off by the edge of the photo, are left
OUT of "prices" — never invented, and never filled from a neighbouring block.

Return exactly this shape:
{
  "title": string or null,
  "period_text": string or null,
  "payout_ladder": [ {"picks": 3, "fractional": "9/2"}, ... ],
  "selections": [
    {"match": "...", "kickoff": "...", "market": null, "your_odds": null,
     "prices": {"<BLOCK HEADING AS PRINTED>": ["6-4", "4-11"], ...}}, ...
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
- "prices": for a PRICED grid, one entry per market block on that fixture's
  row, keyed by the block heading exactly as printed, each a ["left", "right"]
  pair. Read straight across the row: the blocks look alike, so the heading you
  file a pair under must be the heading of the columns you actually read it
  from. Transcribe, do not interpret.
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
    """The per-upload instruction.

    A coupon sheet is a fixed grid: the same blocks, in the same order, with the
    same two sub-columns, every week. So the model is asked to TRANSCRIBE it —
    every block, keyed by its printed heading, each pair in printed order — and
    the software picks the block it wants by matching that heading and assigns
    the sides by position.

    It used to be told which single block to read and to ignore the rest, which
    made it responsible for finding one of six look-alikes and for saying which
    team each price belonged to. Both were guesses it could get wrong in ways
    nothing downstream could see; neither is asked of it now. The hint's
    board_label is still passed, but only as the heading to expect — not as a
    filter to apply.
    """
    if hint is None or not hint.priced:
        return USER_PROMPT
    parts = [
        "Read this coupon. Return EVERY fixture row (a long coupon has 78), "
        "plus the date range."
    ]
    if hint.two_sided:
        parts.append(
            "This is a PRICED GRID: each row carries several market blocks "
            "side by side, each split into two sub-columns. Transcribe them "
            'ALL into "prices", keyed by each block\'s heading exactly as '
            "printed — do not pick one block and do not leave one out."
        )
        if hint.board_label:
            parts.append(
                f'The sheet should include a block headed "{hint.board_label}"; '
                f"report it under that heading as printed. Return the others "
                f"too — they are read from the same rows and are not extra work "
                f"to include."
            )
        parts.append(
            "Each block's pair goes in PRINTED order, left first: "
            '["<left>", "<right>"]. Do not label them, do not reorder them, '
            "and do not judge which team either belongs to — that is decided "
            "from the order. Copy them exactly as printed (fractions such as "
            '"6-4", "4-11", or "EVS"), and use null for a price you cannot '
            "read rather than shifting the other one across."
        )
    elif hint.board_label:
        parts.append(
            f"This is a PRICED coupon. The market to read is the block headed "
            f'"{hint.board_label}"; read the price beside each fixture into '
            f'"your_odds", exactly as printed.'
        )
    else:
        parts.append(
            'This is a PRICED coupon: read the price beside each fixture into '
            '"your_odds", exactly as printed.'
        )
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


def normalise_board_label(label: str | None) -> str:
    """A block heading reduced to what identifies it.

    The heading is transcribed from a photo, so case, spacing, punctuation and
    the "&"/"AND" spelling all vary between reads of the same sheet. Everything
    that isn't a letter or a digit goes, so "WIN & 2 OR MORE GOALS",
    "Win and 2 or more goals" and "WIN &  2 OR MORE  GOALS" are one key.
    """
    if not label:
        return ""
    text = str(label).upper().replace("&", " AND ")
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text).split())


def _pair_from(value) -> tuple[float | None, float | None]:
    """A ["left", "right"] pair -> (home, away).

    THIS is where a price becomes a team's price, and print order alone decides
    it: coupons are laid out left-to-right, so the first is the home team's and
    the second the away team's.

    A pair that arrives with the wrong number of items yields nothing: one price
    with no way to know which sub-column it came from is ambiguous, and an
    unpriced leg beats a leg priced on the wrong team.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None, None
    return parse_odds(value[0]), parse_odds(value[1])


def _prices_from(row: dict) -> dict[str, tuple[float | None, float | None]]:
    """Every market block on a row, keyed by its normalised heading.

    The model transcribes the whole grid; the software chooses. Blocks whose
    pair is entirely unreadable are dropped — an absent block reads as "not on
    this sheet", which is what it is.
    """
    blocks = row.get("prices")
    if not isinstance(blocks, dict):
        return {}
    out = {}
    for label, value in blocks.items():
        key = normalise_board_label(label)
        if not key:
            continue
        pair = _pair_from(value)
        if pair != (None, None):
            out[key] = pair
    return out


def _sides_from(row: dict) -> tuple[float | None, float | None]:
    """A row's two prices as (home, away).

    THIS is where a price becomes a team's price, and it is decided by print
    order alone: coupons are laid out left-to-right, so the first of the pair is
    the home team's and the second the away team's. OCR reports the pair in
    "odds" without labelling it — it is a transcription job, and asking a model
    which team a price belonged to is what crossed prices onto the wrong team.

    A response that still labels the pair itself ("home_odds"/"away_odds") is
    accepted so a provider or model that answers in an older shape keeps
    working, but a positional list wins when both are present.
    """
    if isinstance(row.get("odds"), (list, tuple)):
        return _pair_from(row["odds"])
    return parse_odds(row.get("home_odds")), parse_odds(row.get("away_odds"))


def parse_rows(data: dict) -> list[ExtractedRow]:
    """The `selections` array -> rows. Shared by every OCR provider so a fix
    here (fractional odds, the two-sided price pair) lands for all at once."""
    rows = []
    for row in data.get("selections") or []:
        match = (row.get("match") or "").strip()
        if not match:
            continue  # a row with no fixture cannot be matched or priced
        home_odds, away_odds = _sides_from(row)
        prices = _prices_from(row)
        # A single-block response still fills the flat pair; a grid response
        # fills `prices` and the flat pair is left for whichever block the
        # coupon type asks for (upload.py::_pair_for).
        rows.append(
            ExtractedRow(
                match=match,
                market=(row.get("market") or None),
                your_odds=parse_odds(row.get("your_odds")),
                kickoff_text=(row.get("kickoff") or None),
                home_odds=home_odds,
                away_odds=away_odds,
                prices=prices,
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

        # STREAMED, and the stream is not optional at this budget: the SDK
        # abandons a non-streaming request that runs past its HTTP timeout, and
        # a whole-grid read is minutes of generation. Only the final message is
        # used — nothing here needs the tokens as they arrive.
        with self._client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
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
        ) as stream:
            message = stream.get_final_message()

        # If the model still hits the cap, the JSON is truncated and will not
        # parse. Fail LOUDLY with the cause rather than as an opaque
        # JSONDecodeError — a bare 502 hid this exact bug for a long time. A
        # short read is recoverable (re-scan); a silently half-read coupon is
        # not, because the missing fixtures look exactly like fixtures the
        # sheet never had.
        if message.stop_reason == "max_tokens":
            raise OCRTruncated(
                f"OCR output truncated at max_tokens ({MAX_TOKENS}) — the read is "
                f"incomplete and has been discarded rather than saved in part"
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
