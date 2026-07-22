from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class LegOut(BaseModel):
    id: int
    coupon_id: int
    match_name: str
    pick: str
    odds: Optional[float]
    fair_odds: Optional[float]
    value: Optional[float]
    fair_odds_updated_at: Optional[datetime]
    external_event_id: Optional[int]
    rank: int
    matched_fixture: Optional[str] = None
    match_score: Optional[float] = None
    kickoff: Optional[datetime] = None
    fair_odds_home: Optional[float] = None
    fair_odds_away: Optional[float] = None
    side: Optional[str] = None
    edited: bool = False
    flags: list[str] = Field(default_factory=list, validation_alias="flag_list")
    # Period this selection's coupon runs on, and where today sits in it, so the
    # tracker can separate the live set from the next one to upload.
    period_label: Optional[str] = None       # "Mon–Wed"
    period_status: Optional[str] = None       # "active" | "upcoming" | "past"

    model_config = {"from_attributes": True, "populate_by_name": True}


class CouponOut(BaseModel):
    id: int
    name: Optional[str]
    type_code: str
    period: Optional[str] = None
    match_day: date
    period_ends: Optional[date] = None
    status: str
    leg_count: int
    created_at: datetime
    uploaded_by: Optional[str]

    model_config = {"from_attributes": True}


class CouponTypeOut(BaseModel):
    """One of the seven coupon types — a top-level entity, not an accumulator."""
    code: str
    label: str
    max_fair_odds: Optional[float] = None
    selection_count: int = 0
    # False when the coupon prints no odds per fixture and pays off a ladder.
    prices_each_selection: bool = True
    period_codes: list[str] = Field(default_factory=list)
    # Best rung of the ladder for the latest issue: {"picks", "payout", "fair",
    # "value"}. The only value that means anything on a ladder coupon.
    best_combination: Optional[dict] = None


class CouponPeriodOut(BaseModel):
    """A window a coupon type is issued on."""
    code: str
    label: str
    # Null on variable windows (GG midweek/weekend): their dates are printed on
    # the coupon and only known once it has been read.
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    variable: bool = False
    suggested: bool = False


# Upload flow schemas. OCR auto-confirms, so an upload returns saved+priced
# legs rather than candidate rows awaiting a review step.
class UploadInitResponse(BaseModel):
    coupon_id: int
    legs: list[LegOut]


class LegEditRequest(BaseModel):
    """Inline correction. Omitted fields keep their current value."""
    match_name: Optional[str] = None
    pick: Optional[str] = None
    odds: Optional[float] = Field(default=None, gt=1.0)


# AccaBonus feed (simplified card shape)
class AccaBonusFeedItem(BaseModel):
    match: str
    sport: str  # league/tour label, e.g. "EPL", "ATP French Open"
    pick: str
    odds: float
    value: float
    is_arb: bool


# Cards & corners feed (grouped by match)
class CardFeedBet(BaseModel):
    pick: str
    odds: float
    value: float


class CardFeedItem(BaseModel):
    event_id: str
    match: str
    commence_time: str
    best_value: float
    bets: list[CardFeedBet]


# Private P&L bet tracker — one settled bet's net result
class BetOut(BaseModel):
    id: int
    amount: float
    created_at: datetime

    model_config = {"from_attributes": True}


# First-goalscorer value feed (bet365 vs de-vigged Pinnacle)
class GoalscorerFeedItem(BaseModel):
    match: str
    commence_time: str | None = None
    player: str
    matched_player: str | None = None
    match_score: float | None = None
    low_confidence: bool = False   # match_score below the confident band
    bet365_price: float
    pinnacle_price: float
    fair_price: float
    edge: float
    inplay: bool


# AccaBonus arbs (full detail)
class ArbOut(BaseModel):
    event_id: str
    sport_title: str
    home_team: str
    away_team: str
    commence_time: str
    bet365_home: float
    bet365_away: float
    pinnacle_home: float
    pinnacle_away: float
    fair_home: float
    fair_away: float
    pinnacle_margin: float
    edge_home: float
    edge_away: float
    best_edge: float
    is_arb: bool
    # Only when both books price a draw (e.g. MMA) — null on 2-way markets:
    bet365_draw: float | None = None
    pinnacle_draw: float | None = None
    fair_draw: float | None = None
    edge_draw: float | None = None


# Whole-app state snapshot (GET /state — machine consumers, API-key auth)
class CouponStateOut(CouponOut):
    legs: list[LegOut]


class StateOut(BaseModel):
    generated_at: datetime
    coupons: list[CouponStateOut]
    accabonus: list[AccaBonusFeedItem]
    cards: list[CardFeedItem]


# Cards & corners value bets
class CardValueOut(BaseModel):
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    sport_key: str
    market_key: str
    market_label: str
    line: float
    side: str
    paddy_odds: float
    pinnacle_odds: float
    fair_odds: float
    pinnacle_margin: float
    edge: float
