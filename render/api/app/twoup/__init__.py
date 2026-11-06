"""The 2-Up module (part of the 365 tool): prices bet365's 2 Goals Ahead Early
Payout off OddsPapi and serves the value bets as a one-way feed. It never
places a bet and keeps no account; sizing, placing and the books are the Bet
Placer's (docs/placer-spec.md).

- model.py     pure maths: de-vig, rescue table, edge, filters, the row
- feed.py      the served board from OddsPapi (bet365 + Pinnacle 1X2),
               refreshed by the `twoup_refresh` job

Spec: docs/2up-engine-spec.md, "Decisions, 6 Nov 2026".
"""
