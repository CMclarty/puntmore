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
    # Bundle coupons only: the ready-made acca this selection belongs to, and
    # that acca's printed price. Null everywhere else. `odds` stays null on
    # these legs — the sheet prices the bundle, not the selection — so a
    # per-row edge is meaningless and `value` is null too.
    bundle: Optional[str] = None
    bundle_odds: Optional[float] = None
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


class TypePeriodOut(BaseModel):
    """One issue of a coupon type, as the landing page needs it.

    A type can have SEVERAL of these live at once — the windows overlap by
    design (Mon–Wed and Wed–Fri both contain Wednesday) — which is exactly what
    the landing page has to show.
    """
    coupon_id: int
    period: Optional[str] = None
    label: Optional[str] = None          # "Mon–Wed"
    status: Optional[str] = None         # "active" | "upcoming" | "past"
    match_day: date
    period_ends: Optional[date] = None
    leg_count: int = 0
    priced_count: int = 0                # legs carrying fair odds
    # LIVENESS COMES FROM THE FIXTURES, not from the printed window. A weekend
    # GG issue prints 7-10 August, so a window test called it live all day
    # Monday with every match played. `live_count` is selections still to kick
    # off; `undated_count` is selections whose kickoff was never read, which are
    # NEVER counted as live — an unread time must not hold a coupon open.
    live_count: int = 0
    undated_count: int = 0
    next_kickoff: Optional[datetime] = None
    # Set ONLY when the grid scan didn't come back whole — a sheet is a
    # rectangle of fixtures x market blocks, so a read that lost part of it is
    # measurable (coupon_grid.GridAudit). None means clean, or a coupon read
    # before the check existed. Deliberately one line for the whole SHEET: the
    # per-row anomaly badges were removed in July 2026 and stay removed.
    scan_note: Optional[str] = None
    # This ISSUE's own best ladder rung. Per-issue because the ladder and the
    # fixture list both belong to an issue: two issues of one type are two
    # different bets, and a single type-level figure can only ever describe one
    # of them — which left the live issue's combination unreadable while a
    # newer one existed.
    best_combination: Optional[dict] = None
    # Bundle coupons: this issue's best ready-made acca, same shape as
    # best_combination plus the bundle's printed name. Per-issue for the same
    # reason — a bundle's legs are struck on one sheet.
    best_bundle: Optional[dict] = None


class CouponTypeOut(BaseModel):
    """One of the seven coupon types — a top-level entity, not an accumulator."""
    code: str
    label: str
    max_fair_odds: Optional[float] = None
    selection_count: int = 0
    # Independent flags: a coupon may print per-fixture prices, pay a ladder
    # for N correct, or (William Hill's "Hills") both.
    prices_each_selection: bool = True
    pays_ladder: bool = False
    # A sheet of ready-made accumulators rather than a fixture list. The value
    # is per BUNDLE, so the tracker renders these grouped instead of as a
    # ranked row list.
    bundles: bool = False
    period_codes: list[str] = Field(default_factory=list)
    # Every issue of this type that is live or still to come, newest window
    # first. Drives the landing page's status and its select buttons.
    periods: list[TypePeriodOut] = Field(default_factory=list)
    # Best rung of the ladder for the LATEST issue: {"picks", "payout", "fair",
    # "value"}. The only value that means anything on a ladder coupon. Every
    # issue also carries its own in `periods` — read that one when a specific
    # issue is on screen, since this is only ever the newest.
    best_combination: Optional[dict] = None
    # Best bundle on the LATEST issue of a bundle type. Same caveat as
    # best_combination: every issue carries its own in `periods`.
    best_bundle: Optional[dict] = None


class BundleOut(BaseModel):
    """One ready-made accumulator off a bundle sheet.

    `payout` is what the coupon prints for the whole group; `fair` is the
    product of its legs' fair odds. `value` is null — never a number — when any
    leg is unpriced, because an acca is only as knowable as its worst leg.
    """
    name: str
    coupon_id: int
    picks: int
    payout: Optional[float] = None
    fair: Optional[float] = None
    value: Optional[float] = None
    unpriced: int = 0
    legs: list[LegOut] = Field(default_factory=list)


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


# Private P&L bet tracker — one settled bet's net result. stake/returned are
# present only when the bet was logged in stake/return mode; None means "not
# recorded", not zero.
class BetOut(BaseModel):
    id: int
    category: str
    amount: float
    stake: float | None = None
    returned: float | None = None
    price_taken: float | None = None
    bfsp: float | None = None
    each_way: bool = False
    place_fraction: float | None = None
    is_tip: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


# Shop visit route tracker
class ShopVisitOut(BaseModel):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ShopModelOut(BaseModel):
    """The route's target arithmetic, served rather than hardcoded in the app.

    The page shows this sum as its headline, so it has to be one source of
    truth: two copies drift and the tracker then counts down to a total it no
    longer displays.
    """
    # shops_per_day × days_per_week is week_size, and week_size × weeks is
    # goal. The factors are sent as well as the products because the page
    # prints the sum in factored form ("£500/shop × 5/day × 4/wk × 40 wks").
    shops_per_day: int
    days_per_week: int
    week_size: int
    weeks: int
    goal: int
    value_per_shop: float
    roi_rate: float


class ShopStateOut(BaseModel):
    model: ShopModelOut
    visits: list[ShopVisitOut]


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
