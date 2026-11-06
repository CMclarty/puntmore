"""The placement engine: where the Bet Placer starts (docs/placer-spec.md).

Takes one bet at a time, drives a placement adapter for the account it names, and records the
result. It never decides what to bet; sizing is the Bet Placer's staking logic. Rules enforced
here: idempotent on ``bet_id``, dry-run by default, the kill switch, no auto-retry, no
re-routing, failures escalated to HubAI.
"""
