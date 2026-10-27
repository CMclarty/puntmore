"""The bet feed — a coordinator's view of bet365 value and how it settled.

Two read paths (routers/betfeed.py), one contract (docs/api.md):

  value_bets()     the 365 h2h value rows, i.e. bet365 priced above de-vigged
                   Pinnacle. They come from value/service.py::acca_rows, which
                   wraps accabonus/feed.py — this file prices NOTHING itself and
                   reads through the same TheOddsApi cache as the 365 page, so
                   polling it costs no quota beyond what the page costs.
  results()        for every fixture the feed has ever served, its final score
                   and each served selection's outcome, once known.

To answer for "every fixture ever served", value_bets() records what it served
in feed_fixtures / feed_selections (insert-if-absent: one row per fixture and
per stable selection id). That is bookkeeping by the feed itself — no caller
input reaches it and nothing behind the key can change or delete a row.

Results come from the FlashScore ingest that already settles AW 26 slips
(POST /api/aw26/results, local/flashscore_results.py): record_scores() is
called from that route with the same scraped match stats. No new vendor call.
A fixture the scraper never reads stays pending and is simply not listed.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload

from ..models import FeedFixture, FeedSelection
from ..normalise import team_similarity
from ..schemas import FeedResultOut, FeedSelectionResultOut, FeedValueBetOut, ValueSelection
from ..time_utils import utcnow

logger = logging.getLogger("uvicorn.error")

BOOKMAKER = "bet365"

# A served fixture is matched to scraped stats only once it can have finished
# (kickoff + 105 min), and only for this long after kickoff — the scraper reads
# each league's most recent results, and the window keeps a rematch (cup tie,
# next season) from settling an old fixture.
MIN_MATCH_MINUTES = 105
RESULT_LOOKBACK = timedelta(days=5)
# Same confidence bar AW 26 settles its legs at (aw26/results.py::TEAM_MIN).
TEAM_MIN = 0.80


# --- shapes -------------------------------------------------------------------

def sport_of(sport_key: str | None) -> str:
    """TheOddsApi sport key -> the feed's sport: soccer_* is "football", any
    other is its group prefix ("tennis_atp_paris" -> "tennis")."""
    key = (sport_key or "").strip().lower()
    if not key:
        return "unknown"
    group = key.split("_", 1)[0]
    return "football" if group == "soccer" else group


def selection_id(fixture_id: str, market: str, side: str | None,
                 selection: str, line: float | None) -> str:
    """Stable id for one selection: the same fixture, market, side and line
    always give the same id, across polls and restarts."""
    who = side or selection.strip().lower()
    parts = [fixture_id, market, who]
    if line is not None:
        parts.append(f"{line:g}")
    return ":".join(parts)


def _aware(when: datetime | None) -> datetime | None:
    if when is None:
        return None
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _naive(when: datetime) -> datetime:
    return when.astimezone(timezone.utc).replace(tzinfo=None) if when.tzinfo else when


def to_bet(row: ValueSelection, competition: str | None) -> FeedValueBetOut | None:
    """A value-feed row in the bet feed's shape, or None when it lacks what the
    contract promises (a fixture id, both teams and a kickoff)."""
    if not (row.event_id and row.home and row.away and row.kickoff):
        return None
    return FeedValueBetOut(
        id=selection_id(row.event_id, row.market, row.side, row.selection, row.line),
        fixture_id=row.event_id,
        fixture=f"{row.home} v {row.away}",
        home=row.home, away=row.away,
        kickoff=_aware(row.kickoff),
        competition=competition,
        competition_key=row.sport,
        sport=sport_of(row.sport),
        bookmaker=row.bookmaker,
        market=row.market, selection=row.selection, side=row.side, line=row.line,
        odds=row.odds, fair_odds=row.fair,
        value_pct=round(row.edge * 100, 2),
        seen_at=_aware(row.seen_at),
    )


# --- value bets ---------------------------------------------------------------

def value_bets(arbs_client, *, sport: str = "football", min_edge: float | None = None,
               now: datetime | None = None) -> list[FeedValueBetOut]:
    """The 365 h2h value rows still to kick off, best value first.

    `sport` "all" keeps every sport. `min_edge` None means edge > 0, a number
    means edge >= it — the same rule as GET /api/value.
    """
    from ..accabonus.feed import build_feed
    from ..value.service import acca_row

    now = now or datetime.now(timezone.utc)
    floor = 0.0 if min_edge is None else min_edge
    out = []
    for item in build_feed(arbs_client, floor):
        row = acca_row(item, now)
        if row is None:
            continue
        if (row.edge <= 0) if min_edge is None else (row.edge < min_edge):
            continue
        bet = to_bet(row, item.sport or None)    # item.sport is the league title
        if bet is None or bet.kickoff <= now:
            continue
        if sport != "all" and bet.sport != sport:
            continue
        out.append(bet)
    out.sort(key=lambda b: b.value_pct, reverse=True)
    return out


def record_served(db: Session, bets: list[FeedValueBetOut], now: datetime | None = None) -> int:
    """Remember every fixture and selection served, so results can answer for
    them later. Insert-if-absent; a moved kickoff is followed. Returns how many
    selections were new. Commits only when something changed."""
    if not bets:
        return 0
    now = _naive(now) if now else utcnow()
    fixture_ids = {b.fixture_id for b in bets}
    fixtures = {f.id: f for f in db.query(FeedFixture).filter(FeedFixture.id.in_(fixture_ids))}
    known = {s.id for s in db.query(FeedSelection.id).filter(
        FeedSelection.fixture_id.in_(fixture_ids))}
    new = changed = 0
    for b in bets:
        kickoff = _naive(b.kickoff)
        fx = fixtures.get(b.fixture_id)
        if fx is None:
            fx = FeedFixture(
                id=b.fixture_id, sport=b.sport, competition=b.competition,
                competition_key=b.competition_key, home=b.home, away=b.away,
                kickoff=kickoff, first_served_at=now, status="pending",
            )
            db.add(fx)
            fixtures[b.fixture_id] = fx
            changed += 1
        elif fx.status == "pending" and fx.kickoff != kickoff:
            fx.kickoff = kickoff
            changed += 1
        if b.id not in known:
            db.add(FeedSelection(
                id=b.id, fixture_id=b.fixture_id, bookmaker=b.bookmaker,
                market=b.market, selection=b.selection, side=b.side, line=b.line,
                odds=b.odds, fair_odds=b.fair_odds, value_pct=b.value_pct,
                first_served_at=now,
            ))
            known.add(b.id)
            new += 1
    if new or changed:
        db.commit()
    return new


# --- results ------------------------------------------------------------------

def outcome(market: str, side: str | None, line: float | None, status: str,
            home_score: int | None, away_score: int | None) -> str | None:
    """win | lose | void for one served selection, or None when it cannot be
    settled with confidence (never guessed)."""
    if status == "void":
        return "void"
    if status != "finished" or home_score is None or away_score is None:
        return None
    if market == "h2h" and side in ("home", "away", "draw"):
        result = ("home" if home_score > away_score
                  else "away" if away_score > home_score else "draw")
        return "win" if side == result else "lose"
    return None


def record_scores(db: Session, stats_list, now: datetime | None = None) -> int:
    """Record final scores for pending served fixtures from scraped match stats
    (aw26/results.py::MatchStats — home/away in printed order, goals_*).

    A stats row settles the served football fixture whose two teams both match
    (team_similarity >= TEAM_MIN) and which kicked off between RESULT_LOOKBACK
    and MIN_MATCH_MINUTES ago; ties go to the most recent kickoff. Returns how
    many fixtures were recorded. Commits."""
    now = _naive(now) if now else utcnow()
    pending = db.query(FeedFixture).filter(
        FeedFixture.status == "pending",
        FeedFixture.sport == "football",
        FeedFixture.kickoff <= now - timedelta(minutes=MIN_MATCH_MINUTES),
        FeedFixture.kickoff >= now - RESULT_LOOKBACK,
    ).all()
    recorded = 0
    for stats in stats_list:
        if stats.goals_home is None or stats.goals_away is None:
            continue
        best, best_key = None, None
        for fx in pending:
            if fx.status != "pending":
                continue
            score = min(team_similarity(fx.home, stats.home),
                        team_similarity(fx.away, stats.away))
            if score < TEAM_MIN:
                continue
            key = (score, fx.kickoff)
            if best_key is None or key > best_key:
                best, best_key = fx, key
        if best is None:
            continue
        best.status = "finished"
        best.home_score, best.away_score = stats.goals_home, stats.goals_away
        best.result_at = now
        recorded += 1
    if recorded:
        db.commit()
        logger.info("bet feed: recorded %d result(s)", recorded)
    return recorded


def results(db: Session, since: datetime | None = None) -> list[FeedResultOut]:
    """Served fixtures with a known result, oldest result first. `since` keeps
    those recorded strictly after it."""
    q = (db.query(FeedFixture)
         .options(selectinload(FeedFixture.selections))
         .filter(FeedFixture.status != "pending", FeedFixture.result_at.isnot(None)))
    if since is not None:
        q = q.filter(FeedFixture.result_at > _naive(since))
    out = []
    for fx in q.order_by(FeedFixture.result_at, FeedFixture.id).all():
        out.append(FeedResultOut(
            fixture_id=fx.id, fixture=f"{fx.home} v {fx.away}",
            home=fx.home, away=fx.away, kickoff=_aware(fx.kickoff),
            competition=fx.competition, competition_key=fx.competition_key,
            sport=fx.sport, status=fx.status,
            home_score=fx.home_score, away_score=fx.away_score,
            result_at=_aware(fx.result_at),
            selections=[
                FeedSelectionResultOut(
                    id=s.id, market=s.market, selection=s.selection,
                    side=s.side, line=s.line,
                    outcome=outcome(s.market, s.side, s.line, fx.status,
                                    fx.home_score, fx.away_score),
                )
                for s in fx.selections
            ],
        ))
    return out
