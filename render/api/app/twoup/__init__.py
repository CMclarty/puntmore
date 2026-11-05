"""The 2-Up engine (part of the 365 tool): prices bet365's 2 Goals Ahead Early
Payout off OddsPapi, decides stakes per client and queues bet lists in its
ledger. It never places a bet and never connects to the Hub.

- model.py     pure maths: de-vig, rescue table, edge, Kelly, rounding, limits
- feed.py      today's candidates from OddsPapi (bet365 + Pinnacle 1X2)
- ledger.py    clients, accounts, bets, balances; the Hub endpoint's functions
- decision.py  per client per day: rank, size, split, constrain -> bet lists
The scheduled pass is `scheduler.run_twoup` (every `twoup_decision_minutes`).

Spec: docs/2up-engine-spec.md. Wire contract: hub/PROTOCOL.md.
"""
