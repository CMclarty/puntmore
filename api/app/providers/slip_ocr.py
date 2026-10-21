"""Reading a photo of a bet at the Paddy Power terminal (AW 26).

Same rule as the coupons: the model TRANSCRIBES, the app INTERPRETS. It copies
what the screen shows — every selection, its price, the stake, the returns, the
bet reference — as printed, and is never asked what the bet is worth, which
offer it is under, or which team a price belongs to. Prices and money are
parsed here; the offer is chosen on the upload form.

One prompt for every provider (Claude and Gemini share it), so a fix lands in
both.
"""
import re
from dataclasses import dataclass, field

from .claude_ocr import parse_odds

SLIP_SYSTEM = (
    "You transcribe photographs of a bookmaker's betting terminal screen showing "
    "a single bet. Copy text exactly as printed. Never calculate, infer or "
    "correct anything. Reply with JSON only, no prose and no code fences."
)

SLIP_PROMPT = """Transcribe the bet on this screen into this JSON shape:

{
  "receipt": "the bet reference / receipt / ticket number, or null",
  "placed_at": "the date and time the bet was placed, as printed, or null",
  "bet_type": "the bet type as printed (e.g. 5 Fold, Single, Acca), or null",
  "stake": "the stake as printed, or null",
  "total_stake": "the total stake as printed, or null",
  "potential_returns": "the potential / estimated returns as printed, or null",
  "selections": [
    {
      "event": "the match as printed, teams in printed order (e.g. Arsenal v Chelsea)",
      "market": "the market name as printed (e.g. Total Match Corners)",
      "selection": "the selection as printed (e.g. Over 9.5)",
      "odds": "the price as printed (e.g. 5/6, EVS, 1.83)",
      "kickoff": "the event's date/time as printed, or null"
    }
  ]
}

Every selection on the bet, in the order shown. Use null for anything not on
the screen or not legible — never guess a value."""

_MONEY_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parse_money(value) -> float | None:
    """"£1,250.00" -> 1250.0; anything unreadable -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    m = _MONEY_RE.search(str(value))
    if not m:
        return None
    amount = float(m.group(0).replace(",", ""))
    return amount if amount >= 0 else None


@dataclass
class ExtractedSlipLeg:
    event: str
    market: str | None
    selection: str | None
    odds: float | None
    kickoff_text: str | None = None


@dataclass
class ExtractedSlip:
    receipt: str | None = None
    placed_at_text: str | None = None
    bet_type: str | None = None
    stake: float | None = None
    potential_returns: float | None = None
    legs: list[ExtractedSlipLeg] = field(default_factory=list)


def _text(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def parse_slip(data: dict) -> ExtractedSlip:
    """The model's JSON -> an ExtractedSlip. Selections with no event are
    dropped (nothing to price or settle); an unreadable price is kept as None,
    so the leg shows up blank to be filled in rather than vanishing."""
    legs = []
    for s in data.get("selections") or []:
        if not isinstance(s, dict) or not _text(s.get("event")):
            continue
        legs.append(ExtractedSlipLeg(
            event=_text(s.get("event")),
            market=_text(s.get("market")),
            selection=_text(s.get("selection")),
            odds=parse_odds(s.get("odds")),
            kickoff_text=_text(s.get("kickoff")),
        ))
    # Total stake first: on a multiple the per-line stake is not what left
    # the till.
    stake = parse_money(data.get("total_stake"))
    if stake is None:
        stake = parse_money(data.get("stake"))
    return ExtractedSlip(
        receipt=_text(data.get("receipt")),
        placed_at_text=_text(data.get("placed_at")),
        bet_type=_text(data.get("bet_type")),
        stake=stake,
        potential_returns=parse_money(data.get("potential_returns")),
        legs=legs,
    )
