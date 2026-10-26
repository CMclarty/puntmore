"""Bookie Bashing fair saves prices, for POST /api/paddy/saves-fair.

NOT WRITTEN: there is no Bookie Bashing data yet. The saves page's url was
never set (targets.toml had a placeholder), so nothing was captured or dumped.
Set the page on Puntmore's /scraper page (market "Saves fair"), log in over
VNC, run `python -m scraper dump bb-saves`, and write this from that dump.

Row shape the endpoint takes (fair decimal odds):
    {"home": "Arsenal", "away": "Chelsea", "kickoff": "2026-10-18T15:00:00Z",
     "line": 7.5, "over": 1.91, "under": 2.10}
"""


def bb_saves(page, capture) -> list[dict]:
    raise NotImplementedError("bb_saves: needs a dump of the page")
