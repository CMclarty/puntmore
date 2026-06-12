from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class MatchQuery:
    match_id: int
    match_name: str
    market: str


@dataclass
class OddsResult:
    match_id: int
    fair_odds: float | None
    # The fixture name the provider fuzzy-matched the query to (None if no
    # match). Surfaced so a wrong match is visible instead of silently
    # producing a wrong fair price.
    matched_fixture: str | None = None


class OddsProvider(ABC):
    @abstractmethod
    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        ...


@dataclass
class ExtractedRow:
    match: str
    market: str
    your_odds: float


class OCRProvider(ABC):
    @abstractmethod
    def extract(self, image_bytes: bytes, media_type: str) -> list[ExtractedRow]:
        ...
