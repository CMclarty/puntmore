import json

from sqlalchemy import (
    Boolean, Column, Integer, String, Float, DateTime, Date, ForeignKey, Text,
)
from sqlalchemy.orm import relationship
from .checks import decode_flags
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
    def ladder(self) -> dict[int, float]:
        """Payout ladder as {picks: decimal}. Empty when the coupon prices rows."""
        if not self.payout_ladder:
            return {}
        try:
            return {int(k): float(v) for k, v in json.loads(self.payout_ladder).items()}
        except (ValueError, TypeError, AttributeError):
            return {}


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
    # Both sides of the market, so value on the opposite side stays visible.
    fair_odds_home = Column(Float, nullable=True)
    fair_odds_away = Column(Float, nullable=True)
    side = Column(String, nullable=True)             # 'home' | 'away' | None (unresolved)
    notified_tier = Column(String, nullable=True)    # last value tier sent to Discord (dedupe)
    flags = Column(String, nullable=True)            # comma-separated codes (checks.py)
    # True once a human has corrected this row. A re-scan must never overwrite
    # an edited row with a fresh machine read.
    # server_default is REQUIRED, not cosmetic: the ensure_schema legs rebuild
    # recreates this table from the model and copies old rows with an INSERT
    # that omits `edited`. Without a SQL DEFAULT the NOT NULL column rejects
    # those rows (the Python-side default never reaches a raw INSERT ... SELECT).
    edited = Column(Boolean, nullable=False, default=False, server_default="0")

    coupon = relationship("Coupon", back_populates="legs")

    @property
    def flag_list(self) -> list[str]:
        return decode_flags(self.flags)

    @property
    def period_label(self) -> str | None:
        """Human label of this selection's coupon period, e.g. "Mon–Wed"."""
        from .coupon_periods import get as get_period
        if not self.coupon or not self.coupon.period:
            return None
        p = get_period(self.coupon.period)
        return p.label if p else self.coupon.period

    @property
    def period_status(self) -> str | None:
        """Where today sits in this selection's coupon window:
        "active" (window contains today), "upcoming" (not started yet — the next
        set to upload), or "past" (window ended). None when the window is
        unknown (variable coupon not yet resolved from its fixtures)."""
        from datetime import date
        if not self.coupon or self.coupon.match_day is None:
            return None
        start = self.coupon.match_day
        end = self.coupon.period_ends or start
        today = date.today()
        if today < start:
            return "upcoming"
        if today > end:
            return "past"
        return "active"


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
