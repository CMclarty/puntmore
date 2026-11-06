"""The 2-Up engine (part of the 365 tool): prices bet365's 2 Goals Ahead Early
Payout off OddsPapi. It never places a bet and never connects to the Hub;
staking lives in the client.

- model.py     pure maths: de-vig, rescue table, edge, filters
- feed.py      today's candidates from OddsPapi (bet365 + Pinnacle 1X2)

Spec: docs/2up-engine-spec.md. Wire contract: hub/PROTOCOL.md.
"""
