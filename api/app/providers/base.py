from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


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
    # Similarity score behind that match (0–1). Feeds the low-confidence leg
    # flag, so a loose match is badged in the UI rather than only logged.
    match_score: float | None = None
    # Fixture kickoff (UTC). Drives the tracker's KO countdown, and comes from
    # the matched fixture rather than the coupon's match_day, which is only a
    # date and cannot order legs within a day.
    kickoff: datetime | None = None
    # BOTH sides of a team-specific market, always. Value can be on either, so
    # neither is assumed and both are carried through for comparison.
    # `side` is which one the pick names: True home, False away, None
    # unresolved (in which case fair_odds is None rather than a guess).
    fair_odds_home: float | None = None
    fair_odds_away: float | None = None
    side: bool | None = None


class OddsProvider(ABC):
    @abstractmethod
    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        ...


@dataclass
class ExtractedRow:
    match: str
    # None when the coupon prints no per-fixture selection. The market is known
    # from WHICH coupon was photographed, so OCR is not asked to guess it.
    market: str | None = None
    # None on list coupons (GG Bonus / GG No Draw), which print no price per
    # fixture and pay off a ladder instead.
    your_odds: float | None = None
    # Kickoff exactly as printed, e.g. "MON 17:00". Resolved to a date against
    # the coupon's window, since the coupon only prints a weekday.
    kickoff_text: str | None = None


@dataclass
class ExtractedCoupon:
    """A whole coupon: its fixtures plus the metadata printed on it.

    Coupons carry more than rows — the payout ladder and date range are printed
    and vary between issues, so they are read from the image rather than
    configured and assumed.
    """
    rows: list[ExtractedRow]
    title: str | None = None
    period_text: str | None = None
    # picks -> decimal return, e.g. {3: 5.5, 4: 10.0}. Empty when the coupon
    # prices each fixture individually instead.
    payout_ladder: dict[int, float] | None = None


class OCRProvider(ABC):
    @abstractmethod
    def extract(self, image_bytes: bytes, media_type: str) -> ExtractedCoupon:
        ...
