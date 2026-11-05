from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...schemas import Bet


class PlacementFailed(Exception):
    """The bet could not be placed. The engine reports ``failed`` and escalates; it never retries."""


class PriceChanged(Exception):
    """The price moved before placement. Carries the new price; the engine reports it and stops."""

    def __init__(self, new_price: float):
        super().__init__(f"price changed to {new_price}")
        self.new_price = new_price


@dataclass
class PlacedResult:
    actual_price: float
    reference: str | None = None   # bookmaker's own bet reference, if any


class PlacementAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def place(self, bet: Bet, account: dict) -> PlacedResult:
        """Place ``bet`` on ``account`` (a sub_clients.yaml entry). Raise PriceChanged or PlacementFailed."""

    def balance(self, account: dict) -> float | None:  # optional
        return None
