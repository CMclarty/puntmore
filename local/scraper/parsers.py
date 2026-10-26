"""Parsers: turn a kept-open page into rows.

A parser is `fn(page, capture) -> list[dict]`, named in a target's `parser`.
It may read the page's DOM, or the payloads the page received
(`capture.recent`: (ts, kind, url, text) tuples, newest last).

None are written yet — each needs the real page or payloads first, which is
what v0.1 collects into data/samples/ and data/dumps/. A parser that raises
NotImplementedError is reported once and its target keeps capturing.
"""


def bb_saves(page, capture) -> list[dict]:
    """Bookie Bashing fair saves prices, as POST /api/paddy/saves-fair wants:

        {"home": "Arsenal", "away": "Chelsea", "kickoff": "2026-10-18T15:00:00Z",
         "line": 7.5, "over": 1.91, "under": 2.10}   # fair decimal odds
    """
    raise NotImplementedError("bb_saves: needs a dump of the page")


def pp_match(page, capture) -> list[dict]:
    """Paddy Power prices on a match page (saves, cards, corners, ...)."""
    raise NotImplementedError("pp_match: needs captured samples")


PARSERS = {"bb_saves": bb_saves, "pp_match": pp_match}
