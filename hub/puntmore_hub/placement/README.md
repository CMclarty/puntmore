# Placement: the adapter interface

The placement engine here is where the Bet Placer starts (`docs/placer-spec.md`, which holds
everything else: what it reads, how it sizes, its open work). The engine is real and tested; the
part that touches a bookmaker account is a stub (`adapters/phone.py`) that refuses every bet, so
the Hub runs in dry-run. **Nothing calls the engine yet**: it has no caller in production until
the Bet Placer exists to read the feeds and hand it bets; only the tests drive it today.

## The engine

`engine.py::ClientEngine` owns the rules and never changes for a new adapter: idempotency on
`bet_id`, the kill switch (`data/KILL`), dry-run (`PUNTMORE_DRY_RUN`, on by default), the
placement window, the account lookup, the record in the `bets` table, and escalation to HubAI on
failure. It takes one bet at a time:

```json
{"bet_id": "2026-11-05-00123", "account_id": "acc-phone-1",
 "legs": [{"event_id": "ev-88412", "market": "Match Result", "selection": "Arsenal", "price": 2.10}],
 "stake": 20.0, "expected_price": 2.10, "bet_type": "single",
 "not_before": "2026-11-05T10:00:00+00:00", "not_after": "2026-11-05T14:45:00+00:00"}
```

and records exactly one result per bet: `placed` (`actual_price`), `price_changed` (`new_price`;
nothing placed, the staking logic sizes again at the new price) or `failed` (`reason`; nothing
placed, escalated, never resent).

## The adapter

An adapter is a `PlacementAdapter` (`adapters/base.py`) with one method:

```python
def place(self, bet: Bet, account: dict) -> PlacedResult:   # or raise PriceChanged / PlacementFailed
```

`account` is the account's entry in `sub_clients.yaml` (the phone, the bookmaker, the price
tolerance). `adapters/dry_run.py` is the reference implementation; `adapters/phone.py` is the stub
the phone interface replaces. The adapter must not retry, must not re-route to another account,
and must read the price actually taken off the confirmed slip rather than echo
`expected_price`.

The phone interface drives real phones through **DeviceFarmer** at a local port on the Hub;
a phone holds one or more bookmaker accounts, in the bookmaker's app and in the phone's browser,
and **bet365 bets go through the browser** (register, 6 Nov 2026). Settlement and the book are
the Bet Placer's.

## Testing a new adapter without money

1. Unit: subclass `PlacementAdapter` in a test and run it through `ClientEngine` as
   `tests/test_placement.py` does.
2. Live, dry-run: point the adapter at a real phone with `PUNTMORE_DRY_RUN=1`; the engine still
   substitutes the dry-run adapter, so nothing is placed, but the phone setup, the account lookup
   and the records are exercised.
3. Two weeks of clean dry-run, then Callum sets `PUNTMORE_DRY_RUN=0`.
