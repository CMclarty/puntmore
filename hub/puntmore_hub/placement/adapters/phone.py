"""Phone adapter: the Bet Placer's phone interface (docs/placer-spec.md).

Not implemented yet. The contract the real adapter must meet:

* The phone is driven through DeviceFarmer at a local port on the Hub; bet365 bets go through the
  phone's browser, not the app.
* ``account`` is a sub_clients.yaml entry with at least ``account_id``, ``device`` (the phone, as
  DeviceFarmer names it) and ``bookmaker``.
* ``place`` opens the slip for every leg, confirms the stake, reads back the price actually taken
  and the bookmaker's reference, and returns ``PlacedResult``.
* If the price on the slip differs from ``bet.expected_price`` beyond the tolerance in the
  account config, raise ``PriceChanged(new_price)`` without placing.
* Any other failure (login, limits, market suspended, app error) raises ``PlacementFailed`` with
  a reason a human can act on. Never retry inside the adapter.
* No stealth tooling, no synthetic input beyond what a person would do on the phone.
"""
from __future__ import annotations

from ...schemas import Bet
from .base import PlacedResult, PlacementAdapter, PlacementFailed


class PhoneAdapter(PlacementAdapter):
    name = "phone"

    def place(self, bet: Bet, account: dict) -> PlacedResult:
        raise PlacementFailed("phone adapter not implemented; run in dry-run until it is")
