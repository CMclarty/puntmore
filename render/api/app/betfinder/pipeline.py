"""The Bet Finder pipeline: hub_tips -> priced, owner-only candidates.

Steps (spec, "Bet Finder" -> Pipeline; 1 and 2 happen on the Hub):

- **ingest** — every `hub_tips` row not yet seen becomes ONE `betfinder_tips`
  row. Its text is truncated and kept as untrusted data. A tip from a Hub
  other than the owner's, with an unreadable tip_time, with advised units
  out of range, or in a market other than Match Result is QUARANTINED here
  with a reason. Its source is created inactive (trust 0.5) if unknown.
- **dedupe** — source + event + market + selection + London day (of
  tip_time) is one tip, compared on the NORMALISED text, so the Hub's
  tip_id differing for the same tip does not double it. Later copies are
  `duplicate` of the first.
- **match and price** — off the 2-Up feed's candidates (OddsPapi: bet365's
  1X2 and Pinnacle's de-vigged line), never a request of its own. The event
  is matched with the coupon tracker's fixture comparison
  (`GridCouponProvider._pair_score`: both sides must clear
  `MATCH_THRESHOLD` on `normalise.team_similarity`) and its senior /
  youth check (`fixture_checks`); the selection's side with `picks.side_of`.
  p_win and fair_price are the feed's; rescue is 0; edge = p_win - 1/price.
  A tip that cannot be matched, a draw (the feed carries no draw price), an
  unresolved selection, or a side bet365 does not price is QUARANTINED,
  never guessed. "No fixture" and "no bet365 price" are retried each pass
  until `betfinder_match_days` after the tip (the fixture may not be in the
  feed's horizon yet); every other reason is final.
- **link and settle** — read-only on the 2-Up ledger: a priced tip with a
  bet for the owner client on its (event_id, selection) is `decided`; once none of
  its bets can still change, it is `settled` with the ledger's outcome and
  returns, or `not_placed` if none settled. The ledger is never written.

ONE log line per pass, counts only — never a tip's text.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..fixture_checks import excluded_reason, query_is_senior
from ..models import BetFinderTip, HubTip, TwoUpBet
from ..normalise import normalise_match, normalise_team, split_sides
from ..picks import SIDE_MARGIN, side_of, team_fragment
from ..providers.grid_coupon import MATCH_THRESHOLD, GridCouponProvider
from ..time_utils import utcnow
from ..twoup import model
from ..twoup.model import Candidate
from . import sources

logger = logging.getLogger("uvicorn.error")

TEXT_MAX = 200
INGEST_BATCH = 500
RETENTION = timedelta(days=90)      # settled tips are kept: they ARE the source record

MATCH_RESULT = "match_result"
# Spellings of the full-time 1X2 a tipster prints. Compared after lower-casing
# and collapsing spaces/punctuation. Anything else is not priced.
_MATCH_RESULT_NAMES = {
    "match result", "1x2", "full time result", "fulltime result", "ft result",
    "match odds", "match betting", "result", "win draw win", "wdw", "1 x 2",
    "full time 1x2", "match winner 1x2",
}
_DRAW_NAMES = {"draw", "the draw", "x", "tie"}

# Quarantine reasons. Fixed strings: a reason never carries the tip's text.
REASONS = {
    "not_owner_hub": "tip came from a Hub other than the owner's (Bet Finder is owner-only)",
    "no_source": "tip names no usable source",
    "bad_tip_time": "tip_time is not a readable timestamp",
    "bad_stake_units": "advised_stake_units is negative, not a number, or above BETFINDER_MAX_UNITS",
    "market_not_priced": "only Match Result is priced; any other market is never guessed",
    "event_unreadable": "event is not an 'A v B' fixture",
    "no_fixture": "no 2-Up feed fixture matches the event (retried until the match window ends)",
    "ambiguous_fixture": "two different feed fixtures match the event about equally well",
    "draw_not_priced": "the 2-Up feed carries no draw price",
    "selection_unresolved": "the selection does not clearly name either side",
    "no_bet365_price": "bet365 has no price on that side (retried until the match window ends)",
}
RETRYABLE = ("no_fixture", "no_bet365_price")

# What the last pass did, for the routes. One dict: bounded by construction.
LAST_RUN: dict = {}


# --- normalising --------------------------------------------------------------

def _text(v) -> str:
    """Untrusted text, bounded and with control characters removed."""
    s = "".join(ch for ch in str(v if v is not None else "") if ch.isprintable())
    return s.strip()[:TEXT_MAX]


def normalise_market(raw: str) -> str:
    key = " ".join(str(raw or "").lower().replace("-", " ").replace("_", " ")
                   .replace("/", " ").split())
    if key in _MATCH_RESULT_NAMES:
        return MATCH_RESULT
    return key[:60] or "unknown"


def _naive_utc(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def london_day(dt_naive_utc: datetime) -> date:
    return _aware(dt_naive_utc).astimezone(ZoneInfo(settings.twoup_timezone)).date()


def dedupe_key(source: str, event: str, market: str, selection: str, day: date | None) -> str:
    """Source + event + market + selection + day, on normalised text — the
    same tip however the source re-printed its spacing, accents or "vs"."""
    sel = normalise_team(team_fragment(selection) or selection)
    return "|".join((source, normalise_match(event), market, sel,
                     day.isoformat() if day else "-"))[:500]


def _units(body: dict) -> tuple[float | None, bool]:
    """(advised units or None, ok). 0 or absent is "no units"; negative,
    non-finite or above the cap is not believed."""
    raw = body.get("advised_stake_units")
    if raw is None:
        return None, True
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, False
    u = float(raw)
    if not math.isfinite(u) or u < 0 or u > settings.betfinder_max_units:
        return None, False
    return (u if u > 0 else None), True


def _price_or_none(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and v > 1.0 else None


def _quarantine(tip: BetFinderTip, reason: str) -> None:
    tip.status, tip.quarantine = "quarantined", reason


# --- ingest + dedupe ----------------------------------------------------------

def ingest(db: Session, now: datetime | None = None) -> dict:
    """Bring every unseen hub_tips row into betfinder_tips. Returns counts."""
    counts = {"new": 0, "quarantined": 0, "duplicate": 0, "sources_created": 0}
    seen_ids = select(BetFinderTip.tip_id)
    rows = (db.query(HubTip).filter(HubTip.tip_id.notin_(seen_ids))
            .order_by(HubTip.received_at, HubTip.id).limit(INGEST_BATCH).all())
    if not rows:
        return counts
    owner = settings.hub_owner_client_id
    keys_this_pass: dict[str, str] = {}
    for h in rows:
        try:
            body = json.loads(h.body or "{}")
            if not isinstance(body, dict):
                body = {}
        except ValueError:
            body = {}
        src = sources.source_name(h.source_id)
        event, market_raw, selection = _text(h.event), _text(h.market), _text(h.selection)
        market = normalise_market(market_raw)
        tip_time = _naive_utc(h.tip_time)
        day = london_day(tip_time) if tip_time else None
        units, units_ok = _units(body)
        origin = body.get("origin") if body.get("origin") in ("tipster", "scrape") else None
        tip = BetFinderTip(
            tip_id=h.tip_id, hub_client_id=str(h.client_id)[:64],
            source=src or "-", origin=origin, event_raw=event, market_raw=market_raw,
            selection_raw=selection, market=market,
            dedupe_key=dedupe_key(src or "-", event, market, selection, day),
            tip_time=tip_time, tip_day=day, expiry=_naive_utc(body.get("expiry")),
            advised_price=_price_or_none(h.advised_price), advised_stake_units=units,
            status="new", bet_ids="[]", created_at=utcnow(), updated_at=utcnow(),
        )
        if h.client_id != owner:
            _quarantine(tip, "not_owner_hub")
        elif not src:
            _quarantine(tip, "no_source")
        else:
            _src, made = sources.get_or_create(db, src)
            counts["sources_created"] += int(made)
            first = keys_this_pass.get(tip.dedupe_key) or _first_with_key(db, tip.dedupe_key)
            if first is not None:
                tip.status, tip.duplicate_of = "duplicate", first
            elif tip_time is None:
                _quarantine(tip, "bad_tip_time")
            elif not units_ok:
                _quarantine(tip, "bad_stake_units")
            elif market != MATCH_RESULT:
                _quarantine(tip, "market_not_priced")
            if tip.status != "duplicate":
                keys_this_pass.setdefault(tip.dedupe_key, tip.tip_id)
        db.add(tip)
        counts[{"new": "new", "duplicate": "duplicate"}.get(tip.status, "quarantined")] += 1
    db.commit()
    return counts


def _first_with_key(db: Session, key: str) -> str | None:
    row = (db.query(BetFinderTip.tip_id)
           .filter(BetFinderTip.dedupe_key == key, BetFinderTip.status != "duplicate")
           .order_by(BetFinderTip.id).first())
    return row[0] if row else None


# --- match + price ------------------------------------------------------------

@dataclass
class _Fixture:
    event_id: str
    home: str
    away: str
    kickoff: datetime                 # aware UTC
    competition: str
    sides: dict = field(default_factory=dict)    # "home" | "away" -> Candidate


def fixtures_from(candidates: list[Candidate]) -> dict[str, _Fixture]:
    """The 2-Up feed's priced sides, grouped back into fixtures."""
    out: dict[str, _Fixture] = {}
    for c in candidates:
        f = out.get(c.event_id)
        if f is None:
            f = out[c.event_id] = _Fixture(event_id=c.event_id, home=c.home, away=c.away,
                                           kickoff=_aware(c.kickoff),
                                           competition=c.competition)
        f.sides[c.side] = c
    return out


def retry_until(tip: BetFinderTip) -> datetime | None:
    """Naive UTC: until when an unmatched tip is tried again."""
    if tip.tip_time is None:
        return None
    end = tip.tip_time + timedelta(days=settings.betfinder_match_days)
    return min(end, tip.expiry) if tip.expiry else end


def match_tip(tip: BetFinderTip, fixtures: dict[str, _Fixture]):
    """(fixture, side, score) or (None, reason, None). Never a guess: every
    path that is not a clear match returns a reason."""
    sides = split_sides(tip.event_raw)
    if sides is None:
        return None, "event_unreadable", None
    senior = query_is_senior(tip.event_raw)
    tip_at = _aware(tip.tip_time)
    window = timedelta(days=settings.betfinder_match_days)
    scored = []
    for f in fixtures.values():
        # The fixture must kick off after the tip and inside the match window:
        # a tip names a coming game, not last week's or next month's.
        if not (tip_at < f.kickoff <= tip_at + window):
            continue
        if excluded_reason(senior, None, f.competition, f.kickoff) is not None:
            continue
        score = GridCouponProvider._pair_score(sides, (f.home, f.away))
        if score >= MATCH_THRESHOLD:
            scored.append((score, f))
    if not scored:
        return None, "no_fixture", None
    scored.sort(key=lambda sf: (-sf[0], sf[1].kickoff))
    best_score, best = scored[0]
    for score, other in scored[1:]:
        if best_score - score >= SIDE_MARGIN:
            break
        if normalise_match(f"{other.home} v {other.away}") != normalise_match(
                f"{best.home} v {best.away}"):
            return None, "ambiguous_fixture", None
        # Same two names twice (a league game and a cup tie): the nearest
        # kickoff after the tip is the one meant — already first by the sort.
    if normalise_team(tip.selection_raw) in _DRAW_NAMES:
        return None, "draw_not_priced", None
    side = side_of(tip.selection_raw, best.home, best.away)
    if side is None:
        return None, "selection_unresolved", None
    side = "home" if side else "away"
    if side not in best.sides:
        return None, "no_bet365_price", None
    return best, side, best_score


def price_open(db: Session, candidates: list[Candidate] | None, now: datetime | None = None,
               *, degraded: bool = False) -> dict:
    """Match and price every open Match Result tip off these feed candidates;
    expire what has run out. A tip already priced is re-priced (the decision
    reads the stored price, so it must be current). `candidates=None`, or
    an empty list from a degraded feed, prices nothing: a feed that cannot
    price is not a reason to quarantine a tip."""
    now = now or datetime.now(timezone.utc)
    now_n = _aware(now).astimezone(timezone.utc).replace(tzinfo=None)
    counts = {"priced": 0, "quarantined": 0, "expired": 0}
    open_rows = (db.query(BetFinderTip)
                 .filter(BetFinderTip.status.in_(("new", "priced", "quarantined")),
                         BetFinderTip.market == MATCH_RESULT)
                 .order_by(BetFinderTip.id).all())
    usable = candidates is not None and not (degraded and not candidates)
    fixtures = fixtures_from(candidates) if usable else {}
    for tip in open_rows:
        if tip.status == "quarantined" and tip.quarantine not in RETRYABLE:
            continue
        until = retry_until(tip)
        if tip.status == "quarantined" and (until is None or now_n >= until):
            continue                    # its retry window is over: final
        if tip.expiry is not None and now_n >= tip.expiry and tip.status != "quarantined":
            tip.status, tip.updated_at = "expired", utcnow()
            counts["expired"] += 1
            continue
        if tip.status == "priced" and tip.kickoff is not None and tip.kickoff <= now_n:
            tip.status, tip.updated_at = "expired", utcnow()
            counts["expired"] += 1
            continue
        if not usable or tip.tip_time is None:
            continue
        f, side_or_reason, score = match_tip(tip, fixtures)
        if f is None:
            if tip.status == "priced":
                # Was matched, now is not: the fixture left the feed (kicked
                # off, or past the lead time). Keep the old price; it ages out
                # of the decision by betfinder_max_price_age_minutes.
                continue
            _quarantine(tip, side_or_reason)
            tip.updated_at = utcnow()
            counts["quarantined"] += 1
            continue
        c = f.sides[side_or_reason]
        tip.status, tip.quarantine = "priced", None
        tip.event_id, tip.event = c.event_id, c.event
        tip.home, tip.away, tip.selection, tip.side = c.home, c.away, c.selection, c.side
        tip.kickoff = f.kickoff.astimezone(timezone.utc).replace(tzinfo=None)
        tip.competition, tip.match_score = c.competition, round(score, 4)
        tip.price, tip.p_win, tip.fair_price = c.price, c.p_win, c.fair_price
        tip.edge = model.edge(c.p_win, 0.0, c.price)
        tip.priced_at = now_n
        tip.updated_at = utcnow()
        counts["priced"] += 1
    db.commit()
    return counts


# --- link + settle (reads the ledger, never writes it) ------------------------

def _owner_bets(db: Session, event_id: str, selection: str) -> list[TwoUpBet]:
    return (db.query(TwoUpBet)
            .filter(TwoUpBet.client_id == settings.hub_owner_client_id,
                    TwoUpBet.event_id == event_id,
                    TwoUpBet.selection == selection)
            .order_by(TwoUpBet.created_at, TwoUpBet.bet_id).all())


def link_and_settle(db: Session) -> dict:
    """Tie priced tips to the owner client's ledger bets on their selection
    and copy the outcome back once those bets are done. Every tip on a
    selection shares its bets, whichever row the decision used: when two
    sources tipped it, both are credited; when the 2-Up feed offered the
    same selection at a higher edge, the decision bet the 2-Up row (one bet
    per selection) and that bet is the tip's record. Returns are bet365's
    real ones, so an early payout counts as a win — said in the docs."""
    counts = {"decided": 0, "settled": 0, "not_placed": 0}
    rows = (db.query(BetFinderTip)
            .filter(BetFinderTip.status.in_(("priced", "decided", "expired")),
                    BetFinderTip.event_id.isnot(None)).all())
    for tip in rows:
        bets = _owner_bets(db, tip.event_id, tip.selection)
        if not bets:
            continue
        ids = [b.bet_id for b in bets]
        if ids != tip.bet_id_list:
            tip.bet_ids = json.dumps(ids)
            tip.updated_at = utcnow()
        live = [b for b in bets if b.status != "price_changed"]
        if tip.status in ("priced", "expired") and live:
            tip.status = "decided"
            counts["decided"] += 1
        if tip.status != "decided":
            continue
        if any(b.status in ("queued", "sent", "placed") for b in live):
            continue
        settled = [b for b in live if b.status == "settled"]
        if not settled:
            tip.status = "not_placed"
            counts["not_placed"] += 1
            continue
        outcomes = {b.outcome for b in settled}
        tip.outcome = outcomes.pop() if len(outcomes) == 1 else "partial"
        tip.staked = round(sum(b.stake or 0.0 for b in settled), 2)
        tip.returned = round(sum(b.return_amount or 0.0 for b in settled), 2)
        tip.settled_at = max((b.settled_at for b in settled if b.settled_at), default=utcnow())
        tip.status = "settled"
        tip.updated_at = utcnow()
        counts["settled"] += 1
    db.commit()
    return counts


def sweep(db: Session, now: datetime | None = None) -> int:
    """Drop tips that will never count for anything after RETENTION.
    Settled tips are kept: they are each source's record."""
    now = now or datetime.now(timezone.utc)
    cutoff = _aware(now).astimezone(timezone.utc).replace(tzinfo=None) - RETENTION
    n = (db.query(BetFinderTip)
         .filter(BetFinderTip.status.in_(("quarantined", "duplicate", "expired", "not_placed")),
                 BetFinderTip.created_at < cutoff)
         .delete(synchronize_session=False))
    db.commit()
    return n


# --- the scheduled pass -------------------------------------------------------

def run_pass(db: Session, feed=None, now: datetime | None = None) -> dict:
    """Ingest new tips, price open ones off the feed's CACHE (never a
    refresh, so no OddsPapi request), link and settle against the ledger.
    ONE log line, counts only."""
    now = now or datetime.now(timezone.utc)
    summary: dict = {"at": _aware(now).isoformat()}
    summary["ingest"] = ingest(db, now)
    cached = feed.cached() if feed is not None else None
    if cached is None:
        summary["pricing"] = "skipped: no fresh 2-Up feed in cache"
    else:
        degraded = bool(feed.status().get("degraded"))
        summary["pricing"] = price_open(db, cached, now, degraded=degraded)
    summary["ledger"] = link_and_settle(db)
    summary["swept"] = sweep(db, now)
    ing, pr, led = summary["ingest"], summary["pricing"], summary["ledger"]
    logger.info(
        "betfinder: %d new tip(s), %d duplicate, %d quarantined at ingest, %d source(s) "
        "created; pricing %s; %d decided, %d settled, %d not placed",
        ing["new"], ing["duplicate"], ing["quarantined"], ing["sources_created"],
        pr if isinstance(pr, str) else
        f"{pr['priced']} priced / {pr['quarantined']} quarantined / {pr['expired']} expired",
        led["decided"], led["settled"], led["not_placed"])
    LAST_RUN.clear()
    LAST_RUN.update(summary)
    return summary
