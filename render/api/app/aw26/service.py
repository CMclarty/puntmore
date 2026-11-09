"""AW 26: building a slip from its record, pricing it, settling it, and the
figures the page reports.

Times are stored naive UTC like every other table; "today" and "a day" are
LONDON days — the shop's day, which is what an EV-per-day target counts.
"""
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..models import AwLeg, AwSlip
from ..time_utils import utcnow
from .offers import expected_value, problems, settle

LONDON = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")

# The first day of AW 26 (Callum).
START = date(2026, 11, 9)

# Callum's targets (Oct 2026).
EV_PER_DAY_TARGET = 1000.0
PROFIT_TARGET = 100_000.0

# Refund/boost accas always stake £50 (Callum); the terminal slip doesn't print
# the stake, so this fills it when the record carries none. Other bets vary.
ACCA_STAKE = 50.0
ACCA_OFFERS = ("refund", "boost")

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


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The London day `day` as naive UTC: its start, and the next day's start
    (exclusive)."""
    return (_to_utc(datetime.combine(day, time())),
            _to_utc(datetime.combine(day + timedelta(days=1), time())))


def parse_when(text: str | None, ref: datetime) -> datetime | None:
    """A time as the terminal prints it (London) -> naive UTC, or None.

    Full dates first; then day and month without a year (the year of `ref`,
    or the next one if that lands more than a month before it); then a weekday
    and time, the next such day on or after `ref`'s London date."""
    if not text:
        return None
    if "T" in text and text[:4].isdigit():
        # ISO 8601: UTC unless it says otherwise.
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


def build_slip(body, *, user: str, now: datetime | None = None) -> AwSlip:
    """A new AwSlip (not yet added to a session) from a schemas.AwSlipRecordIn.

    The legs are kept as recorded: kind, line, side and fixture_id are what
    the record carries (HubAI's reading), and Render reads nothing off the
    words and looks no fixture up. A leg whose fair is assumed takes its odds
    as its fair, whatever fair the record carried."""
    now = now or utcnow()
    placed = parse_when(body.placed_at, now) or now
    stake = body.stake
    if stake is None and body.offer in ACCA_OFFERS:
        stake = ACCA_STAKE
    slip = AwSlip(placed_at=placed, user=user, image_path=None, receipt=body.receipt,
                  offer=body.offer, stake=stake, status="open")
    for i, lg in enumerate(body.legs):
        slip.legs.append(AwLeg(position=i, event=lg.event, market=lg.market,
                               selection=lg.selection, odds=lg.odds,
                               kickoff=parse_when(lg.kickoff, placed),
                               kind=lg.kind, line=lg.line, side=lg.side,
                               fixture_id=lg.fixture_id, fair_assumed=lg.fair_assumed,
                               fair_odds=lg.odds if lg.fair_assumed else lg.fair_odds,
                               result="pending"))
    return slip


def price_slip(slip: AwSlip) -> None:
    """Fix the EV once EVERY leg has its odds and a fair price.

    A leg's fair is the one on the leg (recorded by HubAI or typed on the
    page); nothing is looked up. A slip is never valued off the legs that did
    price: until the last one has a fair, EV is NULL and the note says how
    many are waiting. An EV already fixed is left alone (an edit clears it
    first — a correction, not a re-pricing)."""
    if slip.ev is not None:
        return
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


def decided_at(slip: AwSlip) -> datetime | None:
    """The kick-off of the game that decided the slip, or None.

    Replays the results in kick-off order: at each kick-off, only the legs
    kicking off at or before it keep their results; the first at which the
    slip is no longer open decided it. None when a leg with a result has no
    kick-off, or when the results so far decide nothing."""
    resulted = [leg for leg in slip.legs if leg.result != "pending"]
    if any(leg.kickoff is None for leg in resulted):
        return None
    for k in sorted({leg.kickoff for leg in resulted}):
        legs = [(leg.odds, leg.result if leg.kickoff is not None and leg.kickoff <= k else "pending")
                for leg in slip.legs]
        if settle(slip.offer, slip.stake or 0.0, legs)[0] != "open":
            return k
    return None


def settle_slip(slip: AwSlip, now: datetime | None = None) -> None:
    """Status and returns from the legs' results. A decided slip is dated by
    the kick-off of the game that decided it (`decided_at`), whenever its
    results were typed; failing that, by the date already stored, else `now`."""
    if slip.stake is None or not slip.legs or any(leg.odds is None for leg in slip.legs):
        return
    status, returns = settle(slip.offer, slip.stake, [(leg.odds, leg.result) for leg in slip.legs])
    slip.status, slip.returns = status, returns
    if status == "open":
        slip.settled_at = None
    else:
        slip.settled_at = decided_at(slip) or slip.settled_at or now or utcnow()


def profit(slip: AwSlip) -> float | None:
    return None if slip.returns is None else round(slip.returns - slip.stake, 2)


def _figures(slips, ev_target: float, decided=None) -> dict:
    """EV, staked, slips and open over `slips`; profit over `decided` (the
    same slips unless given). Slip columns only: no leg is read."""
    decided = slips if decided is None else decided
    return {
        "ev": round(sum((s.ev for s in slips if s.ev is not None), 0.0), 2),
        "ev_target": round(float(ev_target), 2),
        "profit": round(sum((profit(s) for s in decided if s.returns is not None), 0.0), 2),
        "staked": round(sum((s.stake or 0.0 for s in slips), 0.0), 2),
        "slips": len(slips),
        "open": sum(1 for s in slips if s.status == "open"),
    }


def day_summary(slips, selections, day: date, today: date) -> dict:
    """The page's figures for the London day `day`.

    `slips` are every slip placed on or after START; `selections` are
    selections.group's rows for the day. `chosen`: EV, staked, slips and open
    over the slips placed on `day`, profit over the slips decided on it
    (`settled_at`). `to_date`: every slip, against £1,000 EV for each day on
    which a slip was placed."""
    placed = [s for s in slips if london_day(s.placed_at) == day]
    decided = [s for s in slips if s.settled_at is not None and london_day(s.settled_at) == day]
    days = len({london_day(s.placed_at) for s in slips})
    return {
        "day": day.isoformat(),
        "today": today.isoformat(),
        "first_day": START.isoformat(),
        "days": days,
        "targets": {"ev_per_day": EV_PER_DAY_TARGET, "profit": PROFIT_TARGET},
        "chosen": _figures(placed, EV_PER_DAY_TARGET, decided),
        "to_date": _figures(slips, days * EV_PER_DAY_TARGET),
        "selections": list(selections),
    }
