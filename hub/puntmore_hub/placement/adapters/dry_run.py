"""Dry-run adapter: logs what would have been placed and returns the expected price."""
from __future__ import annotations

import logging

from ...schemas import Bet
from .base import PlacedResult, PlacementAdapter

log = logging.getLogger("puntmore.placement.dry_run")


class DryRunAdapter(PlacementAdapter):
    name = "dry_run"

    def place(self, bet: Bet, account: dict) -> PlacedResult:
        legs = "; ".join(f"{l.event_id} {l.market} {l.selection} @ {l.price}" for l in bet.legs)
        log.info("DRY RUN would place %s %s stake %.2f @ %.3f on %s: %s",
                 bet.bet_id, bet.bet_type, bet.stake, bet.expected_price, account.get("account_id"), legs)
        return PlacedResult(actual_price=bet.expected_price, reference=f"dry-{bet.bet_id}")
