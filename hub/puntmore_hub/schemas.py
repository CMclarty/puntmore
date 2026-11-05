"""Message shapes shared with Render.

These mirror the "Interfaces" section of the 2-Up Engine spec. Cal controls both ends, so this
file is the authoritative contract for the Hub side. Every message travels in an ``Envelope``
whose ``msg_id`` makes it idempotent: a replayed envelope is acknowledged and ignored.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


# ---- Bet list in (Render -> Hub) --------------------------------------------------------------

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
    source_scope: str = "shared"       # shared (2-up) | owner (Bet Finder)

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
            source_scope=d.get("source_scope", "shared"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---- Reports out (Hub -> Render) --------------------------------------------------------------

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


# ---- Candidate tips out (Bet Finder Hub side -> Render) -----------------------------------------

@dataclass
class CandidateTip:
    source_id: str
    event: str
    market: str
    selection: str
    advised_price: float | None
    advised_stake_units: float | None
    tip_time: str
    expiry: str | None = None
    origin: str = "scrape"             # tipster | scrape
    source_scope: str = "owner"        # Bet Finder output is Cal-only by spec
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---- Scrape jobs in (Render -> Hub) -----------------------------------------------------------

@dataclass
class ScrapeJob:
    job_id: str
    source: str
    params: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ScrapeJob":
        return cls(job_id=str(d["job_id"]), source=d["source"], params=d.get("params", {}))


# ---- Envelope ---------------------------------------------------------------------------------

INBOUND_TYPES = ("hello_ack", "bet_list", "scrape_job", "kill_switch", "ping", "ack")
OUTBOUND_TYPES = ("hello", "report", "tip", "scrape_result", "escalation", "escalation_outcome", "pong", "ack")


@dataclass
class Envelope:
    type: str
    client_id: str
    body: dict = field(default_factory=dict)
    msg_id: str = field(default_factory=new_id)
    ts: str = field(default_factory=utcnow_iso)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | bytes) -> "Envelope":
        d: dict[str, Any] = json.loads(raw)
        return cls(type=d["type"], client_id=d.get("client_id", ""), body=d.get("body", {}),
                   msg_id=d.get("msg_id") or new_id(), ts=d.get("ts") or utcnow_iso())
