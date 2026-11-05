# Placement: the bet-placing bot

**Status: placeholder.** The engine around it is real and tested; the part that touches a
bookmaker account is a stub (`adapters/phone.py`) that refuses every bet. Until it exists the
home box runs in dry-run and reports simulated placements.

## The contract

Input, one at a time, from Render via the link (`home/PROTOCOL.md`, "Bet"):

```json
{"bet_id": "2026-11-05-00123", "account_id": "acc-phone-1",
 "legs": [{"event_id": "ev-88412", "market": "Match Result", "selection": "Arsenal", "price": 2.10}],
 "stake": 20.0, "expected_price": 2.10, "bet_type": "single",
 "not_before": "2026-11-05T10:00:00+00:00", "not_after": "2026-11-05T14:45:00+00:00"}
```

Output, exactly one of these per bet, then optionally `settled` later:

| result | when | what Render does |
|---|---|---|
| `placed` (`actual_price`) | the slip was confirmed | records it in the ledger |
| `price_changed` (`new_price`) | the slip price differs from `expected_price` beyond the account's tolerance; nothing was placed | re-runs the decision, may send a new bet |
| `failed` (`reason`) | anything else; nothing was placed | drops it; never resends that `bet_id` |

Later: `settled` (`outcome`, `return`) and `balance` snapshots.

## Where the bot plugs in

`engine.py::ClientEngine` owns the rules and never changes for a new bot: idempotency on
`bet_id`, the kill switch, dry-run, the placement window, the account lookup, reporting, and
escalation on failure. A bot is a `PlacementAdapter` (`adapters/base.py`) with one method:

```python
def place(self, bet: Bet, account: dict) -> PlacedResult:   # or raise PriceChanged / PlacementFailed
```

`account` is the sub-client entry from `sub_clients.yaml`: the device, the bookmaker, the price
tolerance. `adapters/dry_run.py` is the reference implementation; `adapters/phone.py` is the
stub to replace. The adapter must not retry, must not re-route to another account, and must
read the price actually taken off the confirmed slip rather than echo `expected_price`.

## How to test a new adapter without money

1. Unit: subclass `PlacementAdapter` in a test and run it through `ClientEngine` as
   `tests/test_placement.py` does.
2. End to end: `tests/test_link_integration.py` drives the real link against the mock Render;
   swap the adapter in and watch the `placed` report arrive.
3. Live, dry-run: point the adapter at a real device with `PUNTMORE_DRY_RUN=1`; the engine
   still substitutes the dry-run adapter, so nothing is placed, but the device setup, account
   lookup and reporting are exercised.
4. Two weeks of clean dry-run reports, then Cal sets `PUNTMORE_DRY_RUN=0`.

## Open questions this placeholder leaves

- Which mechanism drives the phone (ADB and the bookmaker app, a mobile browser session,
  something else). The spec says only "the client engine drives placement on them".
- How a confirmed slip's price and reference are read back reliably.
- Settlement: who detects a result and sends `settled`, the phone or Render from the odds API.
