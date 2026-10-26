from datetime import date, datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


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
    # Added Oct 2026 for machine consumers (the value feed); the page ignores
    # them. Additive only — nothing above changed.
    event_id: Optional[str] = None
    home: Optional[str] = None
    away: Optional[str] = None
    commence_time: Optional[str] = None
    sport_key: Optional[str] = None
    fair: Optional[float] = None      # de-vigged Pinnacle price of `pick`


# Cards & corners feed (grouped by match)
class CardFeedBet(BaseModel):
    pick: str
    odds: float
    fair: float          # de-vigged fair decimal odds, shown beside the value %
    value: float
    # corners | cards | goals | saves | team_corners | team_cards — what the
    # page filters on.
    market: str = ""
    # The line and side, so the UI can group a selection's ladder (tap a bet to
    # drop down its other value lines) and label each rung.
    line: float = 0.0
    side: str = ""       # "Over" | "Under"
    # How the fair was reached: ladder | hybrid | matched | saves (value.py).
    basis: str = "ladder"


class CardFeedFair(BaseModel):
    """A FAIR three-way price with no Paddy quote on the feed (most corners /
    most cards — cards/most.py): read against the terminal, which does quote it."""
    pick: str            # "Arsenal most corners" | "Tie most corners"
    market: str          # most_corners | most_cards
    fair: float
    basis: str = "hybrid"


class CardFeedItem(BaseModel):
    event_id: str
    match: str
    # The two sides, so a machine consumer never has to split `match`.
    # Added Oct 2026, additive.
    home: Optional[str] = None
    away: Optional[str] = None
    commence_time: str
    sport_key: str = ""
    best_value: float
    bets: list[CardFeedBet]
    fairs: list[CardFeedFair] = []


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
    ev: float | None = None
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


# AW 26 — the acca project's slips
class AwLegOut(BaseModel):
    id: int
    position: int
    event: str
    market: str | None = None
    selection: str | None = None
    odds: float | None = None
    kickoff: datetime | None = None
    kind: str | None = None
    line: float | None = None
    side: str | None = None
    fair_odds: float | None = None
    fixture_id: str | None = None
    result: str
    # Married snapshot line (aw26/marry.py): what was offered vs `odds` taken.
    offered_odds: float | None = None
    snapshot_at: datetime | None = None
    edge: float | None = None                   # odds / fair - 1
    fair_source: str | None = None              # paddy | bb (aw26/attribution.py)

    model_config = {"from_attributes": True}


class AwSlipOut(BaseModel):
    id: int
    placed_at: datetime
    user: str
    receipt: str | None = None
    offer: str
    stake: float | None = None
    potential_returns: float | None = None
    status: str
    returns: float | None = None
    settled_at: datetime | None = None
    ev: float | None = None
    ev_note: str | None = None
    edited: bool = False
    has_image: bool = False
    ev_by_source: dict[str, float] | None = None   # sums to ev (aw26/attribution.py)
    legs: list[AwLegOut] = []

    model_config = {"from_attributes": True}


# --- The cross-tool value feed (GET /api/value — app/value/service.py) --------
class ValueSelection(BaseModel):
    """ONE row shape for every tool's value: a selection a bookmaker is
    offering above its fair price. Read-only — nothing here places a bet.

    `edge` is odds / fair - 1 (0.05 = 5% over fair). `line` is null where the
    market has none, and is always an x.5 where it has one (pushes are never
    listed). `kickoff` and `seen_at` are UTC. `url` is null unless known.
    """
    source: str                      # paddy | 365 | scorers | coupon
    bookmaker: str                   # paddypower | bet365 | williamhill
    event_id: Optional[str] = None   # the source's own fixture id, when it has one
    event: str                       # "Home v Away" as the source names it
    home: Optional[str] = None
    away: Optional[str] = None
    kickoff: Optional[datetime] = None
    sport: Optional[str] = None      # sport / league key, e.g. "soccer_epl"
    market: str                      # corners | cards | goals | saves | team_corners | most_corners | h2h | first_goalscorer | <coupon type code>
    selection: str                   # what to back, as the tool prints it
    line: Optional[float] = None
    side: Optional[str] = None       # over | under | home | away | draw | tie
    odds: float                      # the bookmaker's price
    fair: float                      # de-vigged fair price
    edge: float
    basis: Optional[str] = None      # how the fair was reached (ladder, hybrid, saves, devig, grid …)
    url: Optional[str] = None
    seen_at: datetime                # when this price/fair was read or computed


class ValueFeedOut(BaseModel):
    generated_at: datetime
    # Sources this caller may see and that were asked for, in feed order.
    sources: list[str]
    # A source that FAILED (as opposed to having no value) is named here with
    # the reason, so an empty list is never mistaken for "nothing on offer".
    errors: dict[str, str] = Field(default_factory=dict)
    selections: list[ValueSelection]


# --- Typed responses for routes that used to return bare dicts (Oct 2026) ------
#
# Every route declares a response_model so /api/docs describes it. A model here
# lists the fields the route returns today. The ones marked OPEN also allow
# extra keys (`extra="allow"`): diagnostic and config payloads whose contents
# are vendor- or report-shaped, where dropping an unlisted key would be a
# behaviour change. Never remove a field from these — additive only.


class OpenModel(BaseModel):
    """OPEN: the listed fields are documented; any others pass through as-is."""
    model_config = ConfigDict(extra="allow")


class OkOut(BaseModel):
    ok: bool


class DeletedOut(BaseModel):
    deleted: int


class UploadOptionOut(BaseModel):
    code: str
    label: str
    period_codes: list[str]
    types: list[str]


class DiagnosticsOut(OpenModel):
    """A tool's "why does the screen look like this" report. OPEN."""
    verdict: Optional[str] = None


class IngestRejectOut(OpenModel):
    match: str
    reason: str


class IngestOut(BaseModel):
    stored: int
    rejected: list[IngestRejectOut]


class AwSlipsOut(BaseModel):
    # The page's headline figures (aw26/service.summary) — today, total,
    # targets, value_on_offer. Kept as a mapping: its keys are report-shaped.
    summary: dict[str, Any]
    slips: list[AwSlipOut]


class AwFairsOut(BaseModel):
    stored: int
    priced: int
    still_unpriced: int


class AwSlipIngestOut(BaseModel):
    id: int
    receipt: Optional[str] = None
    offer: str
    stake: Optional[float] = None
    ev: Optional[float] = None
    ev_note: Optional[str] = None
    status: str
    legs: int
    unpriced_legs: int


class AwResultsOut(OpenModel):
    matches: int
    resolved: int
    settled: int


class OpenLeaguesOut(BaseModel):
    events: list[str]
    sport_keys: list[str]
    unknown: list[str]


class ScraperLeagueOut(OpenModel):
    id: int
    name: str
    site: str
    url: Optional[str] = None
    enabled: bool


class ScraperMarketOut(OpenModel):
    key: str
    label: str
    site: str
    tabs: list[str]
    parser: Optional[str] = None
    post: Optional[str] = None
    url: Optional[str] = None
    enabled: bool
    page: bool


class ScraperTimingOut(OpenModel):
    site: str
    every_min: Optional[float] = None
    stale_min: Optional[float] = None
    max_tabs: Optional[int] = None
    open_gap_min: Optional[float] = None
    relist_min: Optional[float] = None
    tab_max_hours: Optional[float] = None
    listings: Optional[bool] = None


class ScraperStatusOut(OpenModel):
    reported_at: Optional[str] = None
    targets: list[dict[str, Any]]
    sites: list[dict[str, Any]]


class ScraperStateOut(BaseModel):
    leagues: list[ScraperLeagueOut]
    markets: list[ScraperMarketOut]
    timings: list[ScraperTimingOut]
    status: ScraperStatusOut


class ScraperConfigOut(OpenModel):
    """What the scraper box runs — config._target's shape. OPEN."""
    targets: list[dict[str, Any]]
    timings: dict[str, dict[str, Any]]


class ScraperStatusAckOut(BaseModel):
    targets: int
    sites: int


class ErrorsOut(OpenModel):
    """The diagnostics ring buffer (app/diagnostics.snapshot). OPEN."""
    boot_id: Optional[str] = None
    count: Optional[int] = None
    records: list[dict[str, Any]] = Field(default_factory=list)


class ClearedOut(BaseModel):
    cleared: int


class ClientErrorAckOut(BaseModel):
    id: str


class HealthOut(BaseModel):
    status: str


class ReadyOut(OpenModel):
    status: str
    checks: dict[str, bool]
    providers: dict[str, Any]
