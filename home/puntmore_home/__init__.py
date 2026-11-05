"""Puntmore home box.

The home side of the 2-Up Engine topology: this machine sits on a residential IP and owns
everything that touches a bookmaker site or a phone. Render (cloud) owns pricing and the
decision layer and never loads a bookmaker page.

Modules:
    config      settings from env / .env, nothing hard-coded
    schemas     message shapes shared with Render (bet lists in, reports out, tips out)
    store       SQLite state: runs, tips, bets, outbox, escalations, request budget
    link        persistent outbound WebSocket client to Render (idempotent both ways)
    scraper     source registry (YAML), headful Chrome fetcher, budget, runner, parsers
    placement   client engine: receives bet lists, drives placement adapters, reports back
    escalation  hands failures to the local AI (Hermes) and records its decision
    alerts      Discord/log alerts with de-dup, daily heartbeat
"""

__version__ = "0.1.0"
