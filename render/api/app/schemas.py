from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal, Optional
from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator


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


class UploadStep(BaseModel):
    key: str
    label: str
    state: str                    # todo | running | done | failed | skipped
    seconds: float | None = None
    note: str | None = None


class UploadStatusOut(BaseModel):
    """A coupon read as the upload's waiting screen draws it (upload_progress)."""
    coupon_id: int
    status: str                   # the coupon's: processing | confirmed | failed
    legs: int
    steps: list[UploadStep]
    error: str | None = None      # why it failed, in words
    stalled: bool = False         # "processing", but nothing is reading it
    elapsed: float | None = None


class LegEditRequest(BaseModel):
    """Inline correction. Omitted fields keep their current value."""
    match_name: Optional[str] = None
    pick: Optional[str] = None
    odds: Optional[float] = Field(default=None, gt=1.0)


# AccaBonus feed (simplified card shape)



# Paddy feed (the `cards` package; grouped by match)
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
    # When the book's prices behind this board were fetched from OddsPapi (the
    # oldest chunk's time); None when unknown. Added Nov 2026, additive.
    priced_at: Optional[datetime] = None


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


# Whole-app state snapshot (GET /state — machine consumers, API-key auth)
class CouponStateOut(CouponOut):
    legs: list[LegOut]


class StateOut(BaseModel):
    generated_at: datetime
    coupons: list[CouponStateOut]
    cards: list[CardFeedItem]


# AW 26 — Callum's ledger of slips (app/aw26/)
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
    fair_assumed: bool

    model_config = {"from_attributes": True}


def _lower(v):
    return v.lower() if isinstance(v, str) else v


class AwSlipLegIn(BaseModel):
    """One leg of a slip HubAI (or any client with the `aw26` grant) records.

    kind, line and side are what HubAI read off the leg's words (None where it
    read none); fixture_id is the event's id. `fair_assumed` = no fair was
    found, so the fair is taken as the odds and any fair_odds sent is ignored."""
    event: str = Field(min_length=1, max_length=120)
    market: str | None = Field(default=None, max_length=120)
    selection: str | None = Field(default=None, max_length=120)
    odds: float | None = Field(default=None, gt=1, le=1000)
    kickoff: str | None = None           # ISO 8601 UTC, or as printed (London)
    fair_odds: float | None = Field(default=None, gt=1, le=1000)
    kind: Literal["corners", "cards", "goals", "saves", "other"] | None = None
    line: float | None = Field(default=None, ge=0, le=100)
    # Lower-cased first: the board prints "Over".
    side: Annotated[Literal["over", "under"] | None, BeforeValidator(_lower)] = None
    fixture_id: str | None = Field(default=None, max_length=80)
    fair_assumed: bool = False


class AwSlipRecordIn(BaseModel):
    """A placed bet to RECORD on the AW 26 ledger (aw26/record.py)."""
    receipt: str | None = Field(default=None, max_length=80)
    placed_at: str | None = None         # as printed (London), or ISO 8601
    offer: str = "refund"                # refund | boost | other (aw26/offers.py)
    stake: float | None = Field(default=None, ge=0, lt=1_000_000)
    legs: list[AwSlipLegIn] = Field(min_length=1, max_length=40)


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
    legs: list[AwLegOut] = []

    model_config = {"from_attributes": True}


class AwSlipsOut(BaseModel):
    # The page's headline figures (aw26/service.summary) — today, total,
    # targets. Kept as a mapping: its keys are report-shaped.
    summary: dict[str, Any]
    slips: list[AwSlipOut]


# --- The cross-tool value feed (GET /api/value — app/value/service.py) --------
class ValueSelection(BaseModel):
    """ONE row shape for every tool's value: a selection a bookmaker is
    offering above its fair price. Read-only — nothing here places a bet.

    `edge` is odds / fair - 1 (0.05 = 5% over fair). `line` is null where the
    market has none, and is always an x.5 where it has one (pushes are never
    listed). `kickoff` and `seen_at` are UTC. `url` is null unless known.
    """
    source: str                      # paddy | 365 | scorers | coupon | 2up
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
    # So is one answering but not fresh: "2up" is "killed: ...", "not yet
    # refreshed", "cannot price: ...", "partly priced: ..." beside its rows,
    # or "stale: prices from HH:MM UTC ..." beside the last good
    # board's rows (each row's `seen_at` is when its prices were fetched).
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


# --- 2-Up engine (/api/2up, app/twoup/) ---------------------------------------

class TwoUpCandidateOut(BaseModel):
    """One side of one fixture priced for bet365's 2-up early payout.
    `edge` is in probability units (p_win + rescue - 1/price), what the feed
    ranks its rows by and the edge floor reads; `ev` is the expected return
    per pound (what the `2up` source of /api/value calls `edge`)."""
    event_id: str
    event: str
    home: str
    away: str
    kickoff: datetime
    competition: str = ""
    selection: str
    side: str
    market: str
    price: float
    p_win: float
    rescue: float
    fair_price: Optional[float] = None
    edge: float
    ev: float
    value: bool                      # passes the price band and the edge floor
    priced_at: Optional[datetime] = None   # when the prices behind the row were fetched


class TwoUpFeedOut(BaseModel):
    generated_at: datetime
    available: bool                  # False: no OddsPapi client on this deploy
    status: dict[str, Any]
    candidates: list[TwoUpCandidateOut]


class TwoUpDiagnosticsOut(OpenModel):
    feed: dict[str, Any]
    rescue_table: dict[str, Any]
    global_kill_switch: bool
    settings: dict[str, Any]


# --- Finder (app/finder/, docs/finder-spec.md) ------------------------------

def _as_utc(value: datetime) -> datetime:
    """A naive datetime is UTC (the table's convention); an aware one is
    converted. Used both ways: on a post and on the way out."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


UTCDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class FinderRowIn(BaseModel):
    """One tip as HubAI posts it: the tip's own prices, never looked up.
    `status` is HubAI's verdict. An `asked` or `rejected` row may leave out
    `kickoff`, `odds` and `fair` (HubAI asks when it cannot place a tip on a
    day and time, or the tip quotes no price); an `offered` row must carry all
    three, or the post is a 422."""
    tip_id: str = Field(min_length=1, max_length=128)
    origin: str = Field(min_length=1, max_length=120)
    sport: Literal["football", "horse"]
    bookmaker: str = Field(min_length=1, max_length=60)
    event: str = Field(min_length=1, max_length=200)
    home: Optional[str] = Field(default=None, max_length=100)
    away: Optional[str] = Field(default=None, max_length=100)
    event_id: Optional[str] = Field(default=None, max_length=100)
    kickoff: Optional[UTCDatetime] = None              # kick-off or the off; UTC if no offset
    market: str = Field(min_length=1, max_length=120)  # win | each_way for a horse
    selection: str = Field(min_length=1, max_length=200)
    line: Optional[float] = None
    side: Optional[str] = Field(default=None, max_length=20)
    odds: Optional[float] = Field(default=None, gt=1, lt=10000)
    fair: Optional[float] = Field(default=None, gt=1, lt=10000)
    advised_stake: Optional[float] = Field(default=None, ge=0, lt=100000)   # pounds
    reason: Optional[str] = Field(default=None, max_length=500)
    status: Literal["asked", "offered", "rejected"]
    expiry: Optional[UTCDatetime] = None               # the tip's own, if it has one

    @model_validator(mode="after")
    def _offered_is_priced(self):
        missing = [f for f in ("kickoff", "odds", "fair") if getattr(self, f) is None]
        if self.status == "offered" and missing:
            raise ValueError(f"an offered row needs {', '.join(missing)}")
        return self


class FinderRowsIn(BaseModel):
    rows: list[FinderRowIn] = Field(max_length=500)


class FinderRowOut(BaseModel):
    """A tip on Finder: every column of `finder_rows`, datetimes in UTC."""
    model_config = ConfigDict(from_attributes=True)

    tip_id: str
    origin: str
    sport: str
    bookmaker: str
    event: str
    home: Optional[str] = None
    away: Optional[str] = None
    event_id: Optional[str] = None
    kickoff: Optional[UTCDatetime] = None              # null on an asked row with no day and time
    market: str
    selection: str
    line: Optional[float] = None
    side: Optional[str] = None
    odds: Optional[float] = None                       # null on an asked row whose tip quotes none
    fair: Optional[float] = None
    edge: Optional[float] = None                       # odds / fair - 1
    advised_stake: Optional[float] = None
    reason: Optional[str] = None
    status: str          # asked | offered | rejected | placed | gone | expired
    expires_at: UTCDatetime                               # earlier of kickoff and expiry; neither: seen_at + 48 h
    asked_at: Optional[UTCDatetime] = None
    answered_at: Optional[UTCDatetime] = None
    seen_at: UTCDatetime                                  # HubAI's latest post of it
    created_at: UTCDatetime
    updated_at: UTCDatetime


class FinderPostedRow(BaseModel):
    tip_id: str
    status: Optional[str] = None                       # as stored after the post; null: no row


class FinderPostOut(BaseModel):
    stored: int
    updated: int
    refused: list[tuple[str, str]]                     # [tip_id, "final" | "status" | "<from> → <to>" | "duplicate of <tip_id>"]
    duplicates: list[str]                              # stored `rejected`, reason "duplicate of <tip_id>"
    rows: list[FinderPostedRow] = []                   # every posted row, in order, with its stored status


class FinderStatusIn(BaseModel):
    """The Bet Placer's report. Any other word is a 422."""
    status: Literal["placed", "gone"]
    at: UTCDatetime


class FinderFeedOut(BaseModel):
    generated_at: datetime
    rows: list[FinderRowOut]                           # offered, soonest kick-off first


class FinderBoardOut(BaseModel):
    generated_at: datetime
    rows: list[FinderRowOut]                           # every status, newest first
