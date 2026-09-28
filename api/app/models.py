import json

from sqlalchemy import (
    Boolean, Column, Integer, String, Float, DateTime, Date, ForeignKey, Text,
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
