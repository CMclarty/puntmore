"""The Bookie Bashing bet builder, kept up so it can be looked at.

This is the start of the general preset-driven bet-builder job in
docs/superpowers/specs/2026-11-12-wop-check-design.md ("A general bet-builder
job": a job names the presets to load and the leagues to cover and returns
the fair for each game and team). For now it reads nothing: it only brings
the builder up and keeps it up, so its states (the presets panel, a preset
loaded, the tracker's Save panel) can be learned from real pages with
`python -m scraper look <job> '<steps>'`, which works on this job's open page
and saves it. Parsers here are written only from such saved pages.

Each call (every runner.PARSE_EVERY_S): "Update Data" until "Standard View"
shows, again every `bb.update_every_s`, and a page that is not the builder
(logged out) said at once - bb_saves.bring_up, the same code bb_saves uses.
It returns no rows and marks the capture active, so the runner never reloads
the page as stale. It does NOT apply the 48-hour window and never rests:
the job exists so the builder can be looked at. Nothing here loads a page.
"""
import time

# Names, not the module: the package re-exports the FUNCTION bb_saves, which
# shadows the module's name.
from .bb_saves import _state, bring_up

# Per page: when the data was last pulled (bring_up's "updated"), by id(page);
# an entry whose page has closed is dropped on the next call (bb_saves._state).
_STATE: dict[int, dict] = {}


def bb_builder(page, capture) -> list[dict]:
    """Bring the builder up (and keep it up); no rows."""
    page.set_default_timeout(8_000)         # a control that is not there is a failure, not a stall
    bring_up(page, _state(page, _STATE), time.time())
    capture.last_activity = time.time()     # kept up, not idle: never reload as stale
    return []
