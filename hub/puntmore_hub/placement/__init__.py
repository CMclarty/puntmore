"""Client engine: the placement side.

Receives bet lists from Render, drives a placement adapter per sub-client (a phone with one
bookmaker account), and reports back. It never decides what to bet. Spec rules enforced here:
idempotent on ``bet_id``, no auto-retry, no re-routing, failures go to the local AI.
"""
