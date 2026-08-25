"""The input contract: what the scraper/provider hands the model."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rung:
    """One quoted line. Either side may be absent."""

    line: float
    over: float | None = None
    under: float | None = None


@dataclass(frozen=True)
class Ladder:
    """A book's full or partial over/under ladder for one fixture and market.

    `market` is one of corners_ft | corners_1h | cards_ft | cards_1h. Half
    markets go through the identical pipeline as independent markets with their
    own mean and dispersion — no attempt is made to link a first half to a full
    match, which would need a within-match dependence model this does not have.
    """

    fixture_id: str
    market: str
    rungs: list[Rung] = field(default_factory=list)
    league: str | None = None
    # Cards only: count_simple | count_weighted | points_10_25 | None (detect).
    card_unit: str | None = None
