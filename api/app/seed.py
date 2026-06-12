from datetime import date, timedelta
from sqlalchemy.orm import Session
from .models import Coupon, Leg
from .time_utils import utcnow

_today = date.today()


def _next_weekday(weekday: int) -> date:
    """Return the next occurrence of weekday (0=Mon … 6=Sun), or today if it matches."""
    days_ahead = (weekday - _today.weekday()) % 7
    return _today + timedelta(days=days_ahead)


SAMPLE_COUPONS = [
    {
        "name": "Saturday Acca",
        "type_code": "TTS2",
        "match_day": _next_weekday(5),   # Saturday
        "status": "live",
        "legs": [
            ("PSG vs Lyon",             "HT/FT PSG/PSG",       2.20, 0.12),
            ("Arsenal vs Chelsea",      "Win and Over 2.5",    1.85, 0.07),
            ("Man City vs Liverpool",   "Both Teams to Score", 1.72, 0.04),
            ("Barcelona vs Real Madrid","Over 2.5 Goals",      1.90, 0.02),
        ],
    },
    {
        "name": "Tuesday Picks",
        "type_code": "WO25",
        "match_day": _today - timedelta(days=2),
        "status": "live",
        "legs": [
            ("Bayern vs Dortmund",  "Win and Over 3.5", 2.40, 0.08),
            ("Juventus vs Inter",   "Over 2.5 Goals",  1.90, 0.03),
            ("Sevilla vs Atletico", "Win DNB",         2.10, None),
        ],
    },
]


def seed(db: Session) -> None:
    if db.query(Coupon).first():
        return  # already seeded

    for c_data in SAMPLE_COUPONS:
        legs_data = c_data["legs"]
        coupon = Coupon(
            name=c_data["name"],
            type_code=c_data["type_code"],
            match_day=c_data["match_day"],
            status=c_data["status"],
            uploaded_by="admin",
            created_at=utcnow(),
        )
        db.add(coupon)
        db.flush()
        for rank, (match_name, pick, odds, value) in enumerate(legs_data):
            db.add(Leg(
                coupon_id=coupon.id,
                match_name=match_name,
                pick=pick,
                odds=odds,
                value=value,
                rank=rank,
            ))

    db.commit()
