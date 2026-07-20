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
    match_day = Column(Date, nullable=False)         # date the legs play
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


class Leg(Base):
    __tablename__ = "legs"

    id = Column(Integer, primary_key=True)
    coupon_id = Column(Integer, ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False)
    match_name = Column(String, nullable=False)
    pick = Column(String, nullable=False)            # selection e.g. "PSG HT/FT"
    odds = Column(Float, nullable=False)             # your odds
    fair_odds = Column(Float, nullable=True)
    value = Column(Float, nullable=True)
    fair_odds_updated_at = Column(DateTime, nullable=True)
    external_event_id = Column(Integer, nullable=True)
    rank = Column(Integer, nullable=False, default=0)
    matched_fixture = Column(String, nullable=True)  # fixture the fuzzy match linked
    match_score = Column(Float, nullable=True)       # similarity behind that match
    notified_tier = Column(String, nullable=True)    # last value tier sent to Discord (dedupe)
    flags = Column(String, nullable=True)            # comma-separated codes (checks.py)
    # True once a human has corrected this row. A re-scan must never overwrite
    # an edited row with a fresh machine read.
    edited = Column(Boolean, nullable=False, default=False)

    coupon = relationship("Coupon", back_populates="legs")

    @property
    def flag_list(self) -> list[str]:
        return decode_flags(self.flags)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)
