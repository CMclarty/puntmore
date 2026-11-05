"""The Bet Finder orchestrator (docs/2up-engine-spec.md, "Bet Finder").

Turns the tips a Hub sends (`hub_tips`) into OWNER-ONLY 2-Up candidates:

- `sources.py`   — the source registry (config as data, trust set by hand).
- `pipeline.py`  — normalise, dedupe, match to an OddsPapi fixture from the
                   2-Up feed, price (Match Result only), quarantine the rest;
                   link decided tips to the ledger and record their outcome.
- `candidates.py`— `extra_candidates`, the 2-Up decision's seam.
- `stats.py`     — per-source strike rate and ROI off settled tips.

It NEVER fetches: no IMAP, no HTTP to a tipster, no page load. Tips arrive
over the Hub link; prices come from the 2-Up feed. It never guesses: a tip
it cannot match and price exactly is quarantined with a reason. Every tip's
text is untrusted — stored truncated, shown in routes, never logged whole,
never evaluated, never put in a prompt.
"""
