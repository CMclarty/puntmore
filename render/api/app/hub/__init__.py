"""The Render end of the Hub link (hub/PROTOCOL.md, version 1).

- `protocol` — the vendored JSON Schema, validation and envelope building.
- `engine`   — the thin adapter over the 2-Up engine's ledger (`app.twoup.ledger`).
- `service`  — connections, inbound handling, the outbound dispatcher, the sweep.

The router is `app/routers/hub_ws.py`. Nothing here places a bet or decides
what to bet: bets come from the engine, go to the Hub, and reports come back.
"""
