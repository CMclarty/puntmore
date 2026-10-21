"""AW 26: building a slip from a read, pricing it, settling it, and the figures
the page and the daily update report.

Times are stored naive UTC like every other table; "today" and "a day" are
LONDON days — the shop's day, which is what an EV-per-day target counts.
"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import AwFair, AwLeg, AwSlip
from ..time_utils import utcnow
from .legs import classify, find_fair, find_fixture_id
from .offers import expected_value, problems, settle

LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")
FAIRS_KEEP_DAYS = 14

# Callum's targets (Oct 2026).
EV_PER_DAY_TARGET = 1000.0
PROFIT_TARGET = 100_000.0

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_FORMATS = (
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%d-%m-%Y %H:%M",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d %b %Y %H:%M", "%d %B %Y %H:%M",
    "%H:%M %d/%m/%Y", "%H:%M %d/%m/%y",
)
_YEARLESS = ("%d/%m %H:%M", "%d %b %H:%M", "%d %B %H:%M")


def _to_utc(local: datetime) -> datetime:
    return local.replace(tzinfo=LONDON).astimezone(UTC).replace(tzinfo=None)


def london_day(utc: datetime) -> date:
    return utc.replace(tzinfo=UTC).astimezone(LONDON).date()


def parse_when(text: str | None, ref: datetime) -> datetime | None:
    """A time as the terminal prints it (London) -> naive UTC, or None.

    Full dates first; then day and month without a year (the year of `ref`,
    or the next one if that lands more than a month before it); then a weekday
    and time, the next such day on or after `ref`'s London date."""
    if not text:
        return None
    if "T" in text and text[:4].isdigit():
        # ISO 8601 from a script: UTC unless it says otherwise.
        try:
            iso = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            iso = None
        if iso is not None:
            return iso.astimezone(UTC).replace(tzinfo=None) if iso.tzinfo else iso
    s = re.sub(r"\s+", " ", text.strip().replace(",", " "))
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    for fmt in _FORMATS:
        try:
            return _to_utc(datetime.strptime(s, fmt))
        except ValueError:
            pass
    ref_local = ref.replace(tzinfo=UTC).astimezone(LONDON).replace(tzinfo=None)
    for fmt in _YEARLESS:
        try:
            d = datetime.strptime(f"{ref_local.year} {s}", f"%Y {fmt}")
        except ValueError:
            continue
        if d < ref_local - timedelta(days=31):
            d = d.replace(year=d.year + 1)
        return _to_utc(d)
    m = re.match(r"^([a-z]{3})[a-z]* (\d{1,2}):(\d{2})$", s, re.I)
    if m and m.group(1).lower() in _WEEKDAYS:
        ahead = (_WEEKDAYS.index(m.group(1).lower()) - ref_local.weekday()) % 7
        d = (ref_local + timedelta(days=ahead)).replace(
            hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
        return _to_utc(d)
    return None


def build_slip(read, *, offer: str, user: str, image_path: str | None,
               events=None, now: datetime | None = None) -> AwSlip:
    """A new AwSlip (not yet added to a session) from an ExtractedSlip."""
    now = now or utcnow()
    placed = parse_when(read.placed_at_text, now) or now
    slip = AwSlip(placed_at=placed, user=user, image_path=image_path, receipt=read.receipt,
                  offer=offer, stake=read.stake, potential_returns=read.potential_returns,
                  status="open")
    for i, r in enumerate(read.legs):
        kind, line, side = classify(r.market, r.selection)
        leg = AwLeg(position=i, event=r.event, market=r.market, selection=r.selection,
                    odds=r.odds, kickoff=parse_when(r.kickoff_text, placed),
                    kind=kind, line=line, side=side, result="pending")
        leg.fixture_id = find_fixture_id(leg, events)
        slip.legs.append(leg)
    return slip


def recent_fairs(db, now: datetime | None = None) -> list[AwFair]:
    cutoff = (now or utcnow()) - timedelta(days=FAIRS_KEEP_DAYS)
    return db.query(AwFair).filter(AwFair.received_at >= cutoff).all()


def price_slip(slip: AwSlip, fairs) -> None:
    """Fill missing fair prices and, once EVERY leg has one, fix the EV.

    An EV already fixed is left alone: it is what the bet was worth when it was
    struck, and a later scrape must not move it. (An edit to the slip clears it
    first — that is a correction of the read, not a re-pricing.)"""
    if slip.ev is not None:
        return
    for leg in slip.legs:
        if leg.fair_odds is None:
            leg.fair_odds = find_fair(leg, fairs)
    missing = sum(1 for leg in slip.legs if leg.fair_odds is None or leg.odds is None)
    if not slip.legs or slip.stake is None:
        slip.ev_note = "nothing to price"
    elif missing:
        slip.ev_note = f"{missing} of {len(slip.legs)} legs have no fair price yet"
    else:
        odds = [leg.odds for leg in slip.legs]
        slip.ev = round(expected_value(slip.offer, slip.stake, odds,
                                       [leg.fair_odds for leg in slip.legs]), 2)
        why = problems(slip.offer, odds)
        slip.ev_note = f"valued without the {slip.offer}: {'; '.join(why)}" if why else None


def settle_slip(slip: AwSlip, now: datetime | None = None) -> None:
    if slip.stake is None or not slip.legs or any(leg.odds is None for leg in slip.legs):
        return
    status, returns = settle(slip.offer, slip.stake, [(leg.odds, leg.result) for leg in slip.legs])
    slip.status, slip.returns = status, returns
    slip.settled_at = None if status == "open" else (slip.settled_at or now or utcnow())


def profit(slip: AwSlip) -> float | None:
    return None if slip.returns is None else round(slip.returns - slip.stake, 2)


def _totals(slips) -> dict:
    priced = [s for s in slips if s.ev is not None]
    settled = [s for s in slips if s.returns is not None]
    return {
        "slips": len(slips),
        "legs": sum(len(s.legs) for s in slips),
        "staked": round(sum(s.stake or 0 for s in slips), 2),
        "ev": round(sum(s.ev for s in priced), 2),
        "unpriced": len(slips) - len(priced),
        "profit": round(sum(profit(s) for s in settled), 2),
        "open": sum(1 for s in slips if s.status == "open"),
    }


def summary(slips, today: date | None = None) -> dict:
    """The page's headline and the daily update's figures, from the slips."""
    today = today or london_day(utcnow())
    by_day = [s for s in slips if london_day(s.placed_at) == today]
    first = min((london_day(s.placed_at) for s in slips), default=today)
    days = (today - first).days + 1
    total = _totals(slips)
    return {
        "today": {"date": today.isoformat(), **_totals(by_day)},
        "total": {**total, "days": days, "ev_target": round(days * EV_PER_DAY_TARGET, 2)},
        "targets": {"ev_per_day": EV_PER_DAY_TARGET, "profit": PROFIT_TARGET},
    }


def _gbp(v: float) -> str:
    return ("−" if v < 0 else "") + f"£{abs(v):,.0f}"


def daily_message(slips, day: date) -> str:
    """The morning post: the day just gone, then the running totals."""
    s = summary([x for x in slips if london_day(x.placed_at) <= day], day)
    d, t = s["today"], s["total"]
    settled_that_day = [x for x in slips if x.settled_at and london_day(x.settled_at) == day]
    lines = [
        f"**AW 26 — {day:%a %d %b}**",
        f"EV {_gbp(d['ev'])} / {_gbp(EV_PER_DAY_TARGET)} · {d['slips']} slips · "
        f"{d['legs']} legs · {_gbp(d['staked'])} staked"
        + (f" · {d['unpriced']} unpriced" if d["unpriced"] else ""),
        f"Settled {_gbp(round(sum(profit(x) for x in settled_that_day), 2))} "
        f"({len(settled_that_day)} slips)",
        f"To date: EV {_gbp(t['ev'])} / {_gbp(t['ev_target'])} ({t['days']} days) · "
        f"profit {_gbp(t['profit'])} / {_gbp(PROFIT_TARGET)} · {t['open']} open",
    ]
    return "\n".join(lines)


def run_daily(db, notifier, day: date | None = None, results_client=None) -> str:
    """The morning job: set what leg results it can from OddsPapi, settle every
    open slip from its legs' results, then post the day just gone. Returns the
    message (posted when a notifier is set).

    Auto-results runs first (aw26/results.py), dormant until its reader is
    written from real /scores + /settlements output. A leg with no result —
    auto or by hand — stays pending and its slip stays open, never assumed."""
    from ..notifications.routes import notify_rows
    from .results import apply_results
    now = utcnow()
    apply_results(db, results_client, now=now)
    slips = db.query(AwSlip).all()
    for slip in slips:
        if slip.status == "open":
            settle_slip(slip, now)
    db.commit()
    day = day or (london_day(now) - timedelta(days=1))
    message = daily_message(slips, day)
    head, *rest = message.split("\n")
    notify_rows(notifier, "aw26", head, [(None, line) for line in rest])
    return message
