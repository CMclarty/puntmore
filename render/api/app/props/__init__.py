"""Props: Paddy Power's player props against Pinnacle, Callum's alone (`props`
grant, `ENABLE_PROPS`). Reads the boards the Paddy sweep already holds; makes
no odds request of its own and never places a bet.

- model.py     pure: catalogue to statements, boards to quotes, the join and value
- service.py   the feed and its diagnostics off `app.state.cards_client`, names

Spec: docs/superpowers/specs/2026-11-17-player-props-design.md.
"""
