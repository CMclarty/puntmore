from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class LegOut(BaseModel):
    id: int
    coupon_id: int
    match_name: str
    pick: str
    odds: float
    fair_odds: Optional[float]
    value: Optional[float]
    fair_odds_updated_at: Optional[datetime]
    external_event_id: Optional[int]
    rank: int
    matched_fixture: Optional[str] = None
    is_approx: bool = False

    model_config = {"from_attributes": True}


class CouponOut(BaseModel):
    id: int
    name: Optional[str]
    type_code: str
    match_day: date
    status: str
    leg_count: int
    created_at: datetime
    uploaded_by: Optional[str]

    model_config = {"from_attributes": True}


# Upload flow schemas
class ExtractedRowOut(BaseModel):
    match: str
    pick: str
    odds: float


class UploadInitResponse(BaseModel):
    coupon_id: int
    rows: list[ExtractedRowOut]


class ConfirmLeg(BaseModel):
    match: str
    pick: str
    odds: float


class ConfirmRequest(BaseModel):
    rows: list[ConfirmLeg]


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
