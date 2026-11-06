"""Data shapes inside the Hub.

``Bet`` and ``Report`` are the placement engine's own (``placement/engine.py``): a bet it is asked
to place and what it records about it. ``Tip`` is a row stored from the Scraper's drop files or
the tipster mailbox (``inbox.py``). ``FILE_TYPES`` names the two envelopes of the Scraper's drop
files (``PROTOCOL.md``, ``schemas/protocol.schema.json``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# The Scraper's drop files: one envelope per file, of one of these types.
FILE_TYPES = ("scraper_tips", "scraper_event")


# ---- A bet for the placement engine -----------------------------------------------------------

@dataclass
class BetLeg:
    event_id: str
    market: str
    selection: str
    price: float

    @classmethod
    def from_dict(cls, d: dict) -> "BetLeg":
        return cls(event_id=str(d["event_id"]), market=d["market"], selection=d["selection"], price=float(d["price"]))


@dataclass
class Bet:
    bet_id: str
    account_id: str
    legs: list[BetLeg]
    stake: float
    expected_price: float
    bet_type: str                      # single | double | treble | fourfold | cover
    not_before: str | None = None      # ISO timestamps; placement window
    not_after: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Bet":
        return cls(
            bet_id=str(d["bet_id"]),
            account_id=str(d["account_id"]),
            legs=[BetLeg.from_dict(x) for x in d.get("legs", [])],
            stake=float(d["stake"]),
            expected_price=float(d["expected_price"]),
            bet_type=d.get("bet_type", "single"),
            not_before=d.get("not_before"),
            not_after=d.get("not_after"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---- What the placement engine records about a bet --------------------------------------------

REPORT_KINDS = ("placed", "price_changed", "failed", "settled", "balance")


@dataclass
class Report:
    kind: str
    body: dict
    ts: str = field(default_factory=utcnow_iso)

    def __post_init__(self) -> None:
        if self.kind not in REPORT_KINDS:
            raise ValueError(f"unknown report kind {self.kind!r}; expected one of {REPORT_KINDS}")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ts": self.ts, **self.body}


def placed(bet_id: str, actual_price: float, *, dry_run: bool = False) -> Report:
    return Report("placed", {"bet_id": bet_id, "actual_price": actual_price, "dry_run": dry_run})


def price_changed(bet_id: str, new_price: float) -> Report:
    return Report("price_changed", {"bet_id": bet_id, "new_price": new_price})


def failed(bet_id: str, reason: str) -> Report:
    return Report("failed", {"bet_id": bet_id, "reason": reason})


def settled(bet_id: str, outcome: str, return_amount: float) -> Report:
    return Report("settled", {"bet_id": bet_id, "outcome": outcome, "return": return_amount})


def balance(account_id: str, amount: float) -> Report:
    return Report("balance", {"account_id": account_id, "balance": amount})


# ---- A tip from the Scraper's sources or the tipster mailbox ----------------------------------

@dataclass
class Tip:
    source_id: str
    event: str
    market: str
    selection: str
    advised_price: float | None
    advised_stake_units: float | None
    tip_time: str
    expiry: str | None = None
    origin: str = "scrape"             # tipster | scrape
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
