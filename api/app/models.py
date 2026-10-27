import json

from sqlalchemy import (
    Boolean, Column, Integer, String, Float, DateTime, Date, ForeignKey, Index, Text,
)
from sqlalchemy.orm import relationship
from .database import Base
from .time_utils import utcnow


class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=True)            # e.g. "Saturday Acca" (optional)
    type_code = Column(String, nullable=False)       # e.g. "TTS2", "WO25"
    # Coupons run on fixed windows (Mon–Wed / Wed–Fri / Sat / Sun), not single
    # days — see coupon_periods.py. match_day holds the window's FIRST fixture
    # date so existing ordering keeps working.
    period = Column(String, nullable=True)
    match_day = Column(Date, nullable=False)         # first fixture date of the period
    period_ends = Column(Date, nullable=True)        # last fixture date of the period
    # Payout ladder READ OFF THIS COUPON as JSON {"3": 5.5, ...}. Printed on
    # each issue and can change between them, so it is stored per coupon rather
    # than assumed from config.
    payout_ladder = Column(Text, nullable=True)
    # processing = OCR still running; confirmed = rows saved and priced. There
    # is no review state: OCR auto-confirms (SPEC §7).
    status = Column(String, nullable=False, default="processing")
    # How much of the printed GRID this scan actually recovered, as JSON — see
    # coupon_grid.GridAudit. A sheet is a rectangle of rows x market blocks, so
    # the cell count is knowable whether or not every cell could be read, and
    # this is where that arithmetic is kept. Null on coupons read before the
    # check existed, and on ladder coupons, which print no grid.
    scan_audit = Column(Text, nullable=True)
    image_path = Column(Text, nullable=True)         # OCR source image
    uploaded_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    legs = relationship(
        "Leg", back_populates="coupon",
        cascade="all, delete-orphan",
        order_by="Leg.rank",
    )

    @property
    def leg_count(self) -> int:
        return len(self.legs)

    @property
    def period_label(self) -> str | None:
        """Human label of this coupon's period, e.g. "Mon–Wed"."""
        from .coupon_periods import get as get_period
        if not self.period:
            return None
        p = get_period(self.period)
        return p.label if p else self.period

    @property
    def period_status(self) -> str | None:
        """Where today sits in this coupon's window: "active" (contains today),
        "upcoming" (not started), "past" (ended), or None when the window is
        unknown (a variable coupon not yet resolved from its fixtures).

        Windows OVERLAP by design — Mon–Wed and Wed–Fri both contain Wednesday —
        so more than one coupon of a type can be active at once.
        """
        from datetime import date
        if self.match_day is None:
            return None
        start = self.match_day
        end = self.period_ends or start
        today = date.today()
        if today < start:
            return "upcoming"
        if today > end:
            return "past"
        return "active"

    @property
    def ladder(self) -> dict[int, float]:
        """Payout ladder as {picks: decimal}. Empty when the coupon prices rows.

        Sanitised against the type's printed board on the way OUT as well as on
        the way in (upload.py). Reading it here too is what repairs a coupon
        already stored with a bad rung — the scan that produced it isn't going
        to run again on its own, and until it does, the combination figure on
        screen is the one thing the coupon is bet on.
        """
        if not self.payout_ladder:
            return {}
        from . import coupon_types  # local: coupon_types has no model imports
        try:
            raw = {int(k): float(v) for k, v in json.loads(self.payout_ladder).items()}
        except (ValueError, TypeError, AttributeError):
            return {}
        ctype = coupon_types.get(self.type_code)
        clean, _notes = coupon_types.sanitise_ladder(
            raw, ctype.default_ladder if ctype else {}
        )
        return clean


class Leg(Base):
    __tablename__ = "legs"

    id = Column(Integer, primary_key=True)
    coupon_id = Column(Integer, ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False)
    match_name = Column(String, nullable=False)
    pick = Column(String, nullable=False)            # selection e.g. "PSG HT/FT"
    # Nullable: GG Bonus and GG No Draw coupons print NO per-selection price —
    # any 3 selections pay a flat 5.5 — so those rows genuinely have no odds.
    odds = Column(Float, nullable=True)
    fair_odds = Column(Float, nullable=True)
    value = Column(Float, nullable=True)
    fair_odds_updated_at = Column(DateTime, nullable=True)
    external_event_id = Column(Integer, nullable=True)
    rank = Column(Integer, nullable=False, default=0)
    matched_fixture = Column(String, nullable=True)  # fixture the fuzzy match linked
    match_score = Column(Float, nullable=True)       # similarity behind that match
    kickoff = Column(DateTime, nullable=True)        # fixture start (UTC), drives KO countdown
    # The kickoff as PRINTED on the coupon. Kept apart from `kickoff`, which
    # pricing overwrites with the matched board fixture's time — so it cannot
    # be what checks the match, or a wrong match would vouch for itself.
    printed_kickoff = Column(DateTime, nullable=True)
    # Both sides of the market, so value on the opposite side stays visible.
    fair_odds_home = Column(Float, nullable=True)
    fair_odds_away = Column(Float, nullable=True)
    side = Column(String, nullable=True)             # 'home' | 'away' | None (unresolved)
    # BUNDLE coupons (Fred Bundles): which ready-made acca this selection is
    # part of, as printed, and that acca's single printed price. Null on every
    # other type. The bundle is the BET — value belongs to the group, not to
    # the row — so `odds` stays null on these legs and `bundle_odds` carries
    # the number the group is struck at (coupon_types.bundles_on).
    bundle = Column(String, nullable=True)
    bundle_odds = Column(Float, nullable=True)
    notified_tier = Column(String, nullable=True)    # last value tier sent to Discord (dedupe)
    # True once a human has corrected this row. A re-scan must never overwrite
    # an edited row with a fresh machine read.
    # server_default is REQUIRED, not cosmetic: the ensure_schema legs rebuild
    # recreates this table from the model and copies old rows with an INSERT
    # that omits `edited`. Without a SQL DEFAULT the NOT NULL column rejects
    # those rows (the Python-side default never reaches a raw INSERT ... SELECT).
    edited = Column(Boolean, nullable=False, default=False, server_default="0")

    coupon = relationship("Coupon", back_populates="legs")

    @property
    def period_label(self) -> str | None:
        """Human label of this selection's coupon period, e.g. "Mon–Wed"."""
        return self.coupon.period_label if self.coupon else None

    @property
    def period_status(self) -> str | None:
        """Where today sits in this selection's coupon window — see
        Coupon.period_status, which is the single definition of "active"."""
        return self.coupon.period_status if self.coupon else None


class GoalscorerAlert(Base):
    """A first-goalscorer value row found by the scheduler (bet365 > fair).

    Written by the pre-kickoff / in-play scan and read by GET /goalscorers/feed.
    Rows for an event are replaced on each scan, so the table always reflects
    the latest picture rather than accumulating history.
    """
    __tablename__ = "goalscorer_alerts"

    id = Column(Integer, primary_key=True)
    event_id = Column(String, nullable=False, index=True)
    match_name = Column(String, nullable=False)
    commence_time = Column(DateTime, nullable=True)   # fixture kickoff (UTC)
    player = Column(String, nullable=False)            # bet365's spelling
    matched_player = Column(String, nullable=True)     # Pinnacle's spelling paired to
    match_score = Column(Float, nullable=True)         # name-match confidence [0,1]
    bet365_price = Column(Float, nullable=False)
    pinnacle_price = Column(Float, nullable=False)
    fair_price = Column(Float, nullable=False)         # de-vigged Pinnacle
    edge = Column(Float, nullable=False)               # bet365/fair - 1
    inplay = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)


class Bet(Base):
    """A single settled bet's net result in the private P&L tracker.

    Owned by the user who logged it (the tracker is a per-user ledger, gated to
    the "pnl" tool). amount is the net change: positive = win, negative = loss.
    Cumulative total, month-to-date and the chart are all derived from these.

    A bet can be logged two ways: the flat net result on its own, or a
    stake/return pair when there's time to enter both. In the second case
    amount is still stored (= returned − stake) so every derived figure reads
    one field regardless of entry mode; stake/returned are extra detail and are
    NULL on flat entries. Anything needing them (turnover, ROI) must skip rows
    where they are NULL rather than treat a missing stake as zero.
    """
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True)
    user = Column(String, nullable=False, index=True)
    # Which ledger this bet belongs to: "dogs" (greyhounds) or "virtuals"
    # (virtual racing). Two independent P&L instances share one table, split by
    # this column — every query is scoped to (user, category). server_default
    # backfills rows written before the split (they land in the dogs ledger).
    category = Column(
        String, nullable=False, default="dogs", server_default="dogs", index=True
    )
    amount = Column(Float, nullable=False)          # net change, + win / − loss
    stake = Column(Float)                           # NULL on flat entries
    returned = Column(Float)                        # NULL on flat entries
    # Price taken vs the Betfair starting price, as decimal odds. Independent of
    # the money columns and of each other: you know the price you took at bet
    # time but the BFSP only after the off, so either can be recorded alone.
    # The price-vs-BFSP series uses only rows where BOTH are present.
    price_taken = Column(Float)
    bfsp = Column(Float)
    # Each-way: two bets on one line, so `stake` holds the TOTAL outlay (2 ×
    # unit) and `returned` the combined return. place_fraction is the place
    # terms the return was computed at (0.25 = 1/4 odds) and is NULL unless
    # each_way — without it a stored EW bet can't be reconstructed.
    each_way = Column(Boolean, nullable=False, default=False, server_default="0")
    place_fraction = Column(Float)
    # A staff tip (virtuals): real money out of the till, so it moves the
    # cumulative total like any loss, but it is NOT a bet — it never counts
    # toward the win rate, the bet count, turnover or EV. amount is negative and
    # stake/price columns stay NULL. server_default backfills existing rows as
    # ordinary bets (0), which is what every row before this column was.
    is_tip = Column(Boolean, nullable=False, default=False, server_default="0")
    # Expected profit in POUNDS at the moment the bet was struck, from the
    # virtuals boost model (frontend boost.js: the card's own price, overround
    # stripped, places by Harville) on the outlay actually staked. Virtuals has
    # no Betfair SP to measure against, so this is its only EV. NULL on every
    # bet without a boost entered — an unboosted bet is not measured, the same
    # way a dogs bet with no BFSP is not.
    ev = Column(Float)
    created_at = Column(DateTime, default=utcnow, index=True)


class ShopVisit(Base):
    """One logged stop on the field route, owned by the user who tapped it.

    Deliberately one row per visit rather than a counter column: the count is
    `COUNT(*)`, and keeping the timestamps means later questions ("how many
    yesterday?", "which weeks were slow?") need no migration, only a query. The
    row carries nothing else — a visit is the event, and the money figures on
    the page are all `count × a constant` (see routers/shop.py).
    """
    __tablename__ = "shop_visits"

    id = Column(Integer, primary_key=True)
    user = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # Tool access. "*" = every tool, present and future (admin). Otherwise the
    # granted tool names joined by "+" (e.g. "coupons+accabonus"). Seeing a tool
    # means full use of it — there is no view-only tier. Default "*" so any user
    # created without an explicit grant keeps the pre-split behaviour.
    permissions = Column(String, nullable=False, default="*")
    created_at = Column(DateTime, default=utcnow)


class ApiClient(Base):
    """A machine caller (a script, a future bet-placer) with its own key.

    The key itself is NEVER stored: only its sha256, so a copy of the database
    is not a copy of the keys. It is shown once, by `python -m app.apikeys
    create`, and cannot be recovered afterwards — lose it and create another.
    `permissions` uses the User grammar ("*" or tools joined by "+"), and is
    resolved through the same `security.user_permissions` seam, so every
    `require_tool` guard admits a client exactly as it would a user with that
    grant. Revoking sets `enabled` False; the row stays as the record.
    """
    __tablename__ = "api_clients"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    key_hash = Column(String, unique=True, nullable=False, index=True)
    permissions = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=utcnow)
    last_used_at = Column(DateTime, nullable=True)


class LadderShadow(Base):
    """One measured prediction error of the counts model (pricing/counts/shadow.py).

    A Pinnacle rung hidden from the fit and predicted from the rest — inside
    the ladder or from beyond its end — against Pinnacle's own de-vigged price.
    Collected by the Paddy sweep to MEASURE the beyond-ladder penalty instead
    of assuming it. Pruned to the last 30 days (cards/shadow_store.py).
    """
    __tablename__ = "ladder_shadow"

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=utcnow, index=True)
    market = Column(String, nullable=False)          # corners | cards | goals
    fixture_id = Column(String, nullable=False)
    line = Column(Float, nullable=False)
    beyond = Column(Float, nullable=False)           # counts outside the fit's rungs
    p_pred = Column(Float, nullable=False)           # model p(over), rung hidden
    p_actual = Column(Float, nullable=False)         # Pinnacle's de-vigged p(over)


class AwSlip(Base):
    """One bet the AW 26 acca project placed at the Paddy terminal (aw26/).

    Read from a photo of the terminal and saved straight away, like a coupon —
    every field editable afterwards. The team is ONE pot: `user` records who
    uploaded it, nothing is split by it.

    EV is fixed from the day's fair prices (Bookie Bashing's Group Tracker,
    posted by the local script) once EVERY leg has one, and never recomputed
    after — it is what the bet was worth when it was struck. Until then it is
    NULL and `ev_note` says why.
    """
    __tablename__ = "aw_slips"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    # When the bet was struck: the terminal's own time if the photo shows one,
    # else the upload. The EV-per-day target counts slips by this.
    placed_at = Column(DateTime, nullable=False, index=True)
    user = Column(String, nullable=False)
    image_path = Column(String, nullable=True)
    # The terminal's bet reference, as printed. Unique where present, so the
    # same slip photographed twice is refused rather than counted twice.
    receipt = Column(String, nullable=True, unique=True)
    offer = Column(String, nullable=False, default="refund")   # refund | boost | other
    stake = Column(Float, nullable=True)            # NULL if the read missed it — fill in
    # Printed potential returns, kept for reference against our settlement.
    potential_returns = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="open")    # open | won | lost | refunded | void
    returns = Column(Float, nullable=True)                     # NULL while open
    settled_at = Column(DateTime, nullable=True)
    ev = Column(Float, nullable=True)                          # pounds
    ev_note = Column(String, nullable=True)                    # why EV is NULL / off-terms
    # Set once a person changes a field: a re-scan never overwrites a correction.
    edited = Column(Boolean, nullable=False, default=False)

    legs = relationship("AwLeg", back_populates="slip", cascade="all, delete-orphan",
                        order_by="AwLeg.position")

    @property
    def has_image(self) -> bool:
        return bool(self.image_path)

    @property
    def ev_by_source(self) -> dict | None:
        from .aw26.attribution import ev_by_source
        return ev_by_source(self)


class AwLeg(Base):
    """One selection on an AW 26 slip, as printed, plus what it was worth."""
    __tablename__ = "aw_legs"

    id = Column(Integer, primary_key=True)
    slip_id = Column(Integer, ForeignKey("aw_slips.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    event = Column(String, nullable=False)          # "Arsenal v Chelsea", as printed
    market = Column(String, nullable=True)          # "Total Match Corners", as printed
    selection = Column(String, nullable=True)       # "Over 9.5", as printed
    odds = Column(Float, nullable=True)             # decimal, the price taken; NULL = unread
    kickoff = Column(DateTime, nullable=True)
    # Read off the printed market/selection (aw26/legs.py::classify), so the
    # leg can be matched to a fair price and settled: corners | cards | goals |
    # saves | other, the line, and over | under (None outside totals).
    kind = Column(String, nullable=True)
    line = Column(Float, nullable=True)
    side = Column(String, nullable=True)
    fair_odds = Column(Float, nullable=True)
    # OddsPapi's fixture id, from the Paddy sweep's events, for settling.
    fixture_id = Column(String, nullable=True)
    result = Column(String, nullable=False, default="pending")  # pending | won | lost | void

    slip = relationship("AwSlip", back_populates="legs")
    # The line this leg was married to (aw_leg_lines; aw26/marry.py) — a side
    # table because aw_legs takes no new columns without a migration.
    line_link = relationship("AwLegLine", uselist=False, back_populates="leg",
                             cascade="all, delete-orphan")

    @property
    def offered_odds(self) -> float | None:
        return self.line_link.offered_odds_at_snapshot if self.line_link else None

    @property
    def snapshot_at(self):
        return self.line_link.snapshot_at if self.line_link else None

    @property
    def fair_source(self) -> str | None:
        """Where `fair_odds` came from: the married snapshot line's source
        (paddy, ...) when the fair IS that line's, else "bb" (Bookie Bashing,
        or typed in by hand — the two are not told apart). None: no fair."""
        if self.fair_odds is None:
            return None
        link = self.line_link
        if link is not None and abs(self.fair_odds - link.fair_odds) < 1e-9:
            return link.source
        return "bb"

    @property
    def edge(self) -> float | None:
        from .aw26.attribution import leg_edge
        return leg_edge(self)


class AwFair(Base):
    """A fair price from Bookie Bashing's Group Tracker, posted daily by the
    local script (local/bookiebashing_tracker.py). Kept 14 days: a slip
    uploaded after the day's scrape still finds its fairs."""
    __tablename__ = "aw_fairs"

    id = Column(Integer, primary_key=True)
    received_at = Column(DateTime, default=utcnow, index=True)
    home = Column(String, nullable=False)
    away = Column(String, nullable=False)
    kickoff = Column(DateTime, nullable=True)
    market = Column(String, nullable=True)
    selection = Column(String, nullable=True)
    kind = Column(String, nullable=True)
    line = Column(Float, nullable=True)
    side = Column(String, nullable=True)
    fair_odds = Column(Float, nullable=False)


class ValueSnapshot(Base):
    """What value was on offer at one moment (aw26/snapshots.py).

    The Paddy sweep's rows are computed every two minutes and otherwise never
    stored; a snapshot keeps them, throttled to AW26_SNAPSHOT_MINUTES, so a
    slip can be married to the line it came from and the day's "value on
    offer" can be reported. `source` says which tool produced the lines:
    paddy (the sweep) today; coupon | bb | market fit the same columns later.
    Kept 30 days (snapshots.KEEP_DAYS)."""
    __tablename__ = "value_snapshots"

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, nullable=False, default=utcnow, index=True)   # naive UTC
    source = Column(String, nullable=False)          # paddy | coupon | bb | market
    n_lines = Column(Integer, nullable=False, default=0)
    n_value = Column(Integer, nullable=False, default=0)     # edge >= 0
    best_edge = Column(Float, nullable=True)                 # NULL: no lines
    sum_edge_positive = Column(Float, nullable=False, default=0.0)

    lines = relationship("ValueLine", back_populates="snapshot",
                         cascade="all, delete-orphan", passive_deletes=True)


class ValueLine(Base):
    """One priced line inside a ValueSnapshot. `market` is the Paddy page's
    market name (corners | cards | goals | saves | team_corners | team_cards);
    a team line says which team in `team` (home | away), since the pick alone
    is prose. `side` is lower-case over | under, as AwLeg.side is."""
    __tablename__ = "value_lines"
    __table_args__ = (
        Index("ix_value_lines_key", "event_id", "market", "line", "side"),
    )

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey("value_snapshots.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    event_id = Column(String, nullable=True)
    home = Column(String, nullable=False)
    away = Column(String, nullable=False)
    kickoff = Column(DateTime, nullable=True)        # naive UTC
    sport_key = Column(String, nullable=True)        # the league, e.g. soccer_epl
    market = Column(String, nullable=False)
    team = Column(String, nullable=True)             # home | away for a team line
    line = Column(Float, nullable=True)
    side = Column(String, nullable=True)             # over | under
    pick = Column(String, nullable=True)             # "Over 9.5 corners", as the page prints it
    offered_odds = Column(Float, nullable=False)     # the bookmaker's price (Paddy)
    fair_odds = Column(Float, nullable=False)
    edge = Column(Float, nullable=False)             # offered / fair - 1
    basis = Column(String, nullable=True)            # ladder | hybrid | matched | saves

    snapshot = relationship("ValueSnapshot", back_populates="lines")


class AwLegLine(Base):
    """An AW 26 leg married to the snapshot line it came from (aw26/marry.py):
    the nearest snapshot at or before the slip's placed_at that quoted the
    same fixture, market, line and side.

    The line's prices are COPIED here, so the marriage survives the snapshot
    being pruned (line_id then points nowhere and is cleared). `offered_odds`
    is what the bookmaker showed in that snapshot; the leg's own `odds` is the
    price taken, so the two together are offered-vs-taken."""
    __tablename__ = "aw_leg_lines"

    id = Column(Integer, primary_key=True)
    leg_id = Column(Integer, ForeignKey("aw_legs.id", ondelete="CASCADE"),
                    nullable=False, unique=True, index=True)
    line_id = Column(Integer, ForeignKey("value_lines.id", ondelete="SET NULL"), nullable=True)
    source = Column(String, nullable=False)          # the snapshot's source (paddy, ...)
    snapshot_at = Column(DateTime, nullable=False)
    offered_odds_at_snapshot = Column(Float, nullable=False)
    fair_odds = Column(Float, nullable=False)        # the line's fair at that snapshot
    edge_at_snapshot = Column(Float, nullable=False)
    match_score = Column(Float, nullable=False)      # the weaker team-name similarity
    basis = Column(String, nullable=True)            # ladder | hybrid | matched | saves

    leg = relationship("AwLeg", back_populates="line_link")


# --- Scraper control plane (routers/scraper.py, scraper_control.py) ---------
# The scraper (top-level scraper/, its own Docker image) asks the server what
# to read and reports back. These rows are WHAT it reads and how often; HOW
# each site is treated (budgets, quiet hours, blocks) stays in the scraper's
# own sites.py, which no edit here can loosen.

class ScraperLeague(Base):
    """A competition listing page whose match links the scraper opens."""
    __tablename__ = "scraper_leagues"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)   # "epl" -> target "pp-epl-most"
    site = Column(String, nullable=False)                # paddypower
    url = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)


class ScraperMarket(Base):
    """What is read on a site. A market with a `url` is a page target of its
    own (bb_saves); one without is read on every enabled league of its site."""
    __tablename__ = "scraper_markets"

    key = Column(String, primary_key=True)               # most | bb_saves
    label = Column(String, nullable=False)
    site = Column(String, nullable=False)
    tabs = Column(Text, nullable=False, default="[]")    # JSON list: in-page tabs to click
    parser = Column(String, nullable=True)
    post = Column(String, nullable=True)                 # ingest path the rows go to
    url = Column(String, nullable=True)                  # page target only
    enabled = Column(Boolean, nullable=False, default=True)

    @property
    def tab_list(self) -> list[str]:
        try:
            return [str(t) for t in json.loads(self.tabs or "[]")]
        except ValueError:
            return []


class ScraperTiming(Base):
    """Per-site timings, clamped to the scraper's floors on write."""
    __tablename__ = "scraper_timings"

    site = Column(String, primary_key=True)
    every_min = Column(Float, nullable=True)             # None = reload only when stale
    stale_min = Column(Float, nullable=False)
    max_tabs = Column(Integer, nullable=False)
    open_gap_min = Column(Float, nullable=False)
    relist_min = Column(Float, nullable=False)
    tab_max_hours = Column(Float, nullable=False)


class ScraperTargetStatus(Base):
    """The scraper's last report, one row per target. Replaced whole on each
    report, so a target the scraper no longer has drops off."""
    __tablename__ = "scraper_target_status"

    name = Column(String, primary_key=True)
    site = Column(String, nullable=False)
    url = Column(String, nullable=False)
    state = Column(String, nullable=False)               # running | paused | blocked | retired
    detail = Column(String, nullable=True)
    last_load = Column(DateTime, nullable=True)
    last_parse = Column(DateTime, nullable=True)
    rows_posted = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class ScraperSiteStatus(Base):
    """Per-site budget spent and cool-off, from the same report."""
    __tablename__ = "scraper_site_status"

    site = Column(String, primary_key=True)
    loads_hour = Column(Integer, nullable=False, default=0)
    per_hour = Column(Integer, nullable=False, default=0)
    loads_day = Column(Integer, nullable=False, default=0)
    per_day = Column(Integer, nullable=False, default=0)
    cooloff = Column(String, nullable=True)              # reason, None when not cooling off
    cooloff_left_h = Column(Float, nullable=True)
    needs_resume = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class FeedFixture(Base):
    """A fixture the bet feed (GET /api/value-bets) has SERVED, kept for ever
    so GET /api/results can answer for it once it is played — whether or not
    anyone bet on it. Written by the feed on serve (betfeed/service.py), never
    by a caller. `id` is the source's own fixture id (TheOddsApi's event id for
    the 365 h2h feed). Times are naive UTC.

    status: pending -> finished (scores recorded) | void (not played). Results
    arrive through the existing FlashScore ingest (POST /api/aw26/results);
    result_at is when they were recorded here, which is what `since` reads."""
    __tablename__ = "feed_fixtures"

    id = Column(String, primary_key=True)
    sport = Column(String, nullable=False)               # football | tennis | ...
    competition = Column(String, nullable=True)          # "EPL", as the source titles it
    competition_key = Column(String, nullable=True)      # soccer_epl
    home = Column(String, nullable=False)
    away = Column(String, nullable=False)
    kickoff = Column(DateTime, nullable=False, index=True)
    first_served_at = Column(DateTime, nullable=False, default=utcnow)
    status = Column(String, nullable=False, default="pending", server_default="pending")
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    result_at = Column(DateTime, nullable=True, index=True)

    selections = relationship(
        "FeedSelection", back_populates="fixture",
        cascade="all, delete-orphan", order_by="FeedSelection.first_served_at",
    )


class FeedSelection(Base):
    """One selection the bet feed served, under its fixture. `id` is stable for
    the same fixture + market + side + line, so a poll that serves it again
    finds the row rather than adding one. The prices are those it was FIRST
    served at; the feed itself always carries the current ones."""
    __tablename__ = "feed_selections"

    id = Column(String, primary_key=True)
    fixture_id = Column(String, ForeignKey("feed_fixtures.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    bookmaker = Column(String, nullable=False)
    market = Column(String, nullable=False)              # h2h
    selection = Column(String, nullable=False)           # as the source prints it
    side = Column(String, nullable=True)                 # home | away | draw
    line = Column(Float, nullable=True)
    odds = Column(Float, nullable=False)
    fair_odds = Column(Float, nullable=False)
    value_pct = Column(Float, nullable=False)
    first_served_at = Column(DateTime, nullable=False, default=utcnow)

    fixture = relationship("FeedFixture", back_populates="selections")
