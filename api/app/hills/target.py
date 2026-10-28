"""Hills' target match: which fixtures can be picked, and the team's one pick.

William Hill boosts bet builders on ONE match at a time (25% on winnings,
sometimes 50% on a cup final), so the team agrees a target and every phone
sees it. The picker offers only what the boost runs on (Callum, Oct 2026):

  - Premier League fixtures on a weekend round (Fri–Mon, London time);
  - the FA Cup final, the EFL Cup final and the Champions League final.

Fixtures come from the Hills sweep's own boards (OddsPapi, 7 days ahead), so
listing them costs no requests. OddsPapi's round field is unverified: a cup
fixture counts as a final when its round says so, or — when no round is sent
— when it is its competition's only fixture in the window.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..models import HillsTarget
from ..time_utils import utcnow

LONDON = ZoneInfo("Europe/London")

PREMIER_LEAGUE, FA_CUP, EFL_CUP, CHAMPIONS_LEAGUE = 17, 19, 21, 7
COMPETITIONS = {PREMIER_LEAGUE: "Premier League", FA_CUP: "FA Cup",
                EFL_CUP: "EFL Cup", CHAMPIONS_LEAGUE: "Champions League"}
FINALS_ONLY = (FA_CUP, EFL_CUP, CHAMPIONS_LEAGUE)
# Swept for Hills on top of the shared list, which has no domestic cups.
EXTRA_TOURNAMENTS = (FA_CUP, EFL_CUP)
WEEKEND = {4, 5, 6, 0}                  # Fri, Sat, Sun, Mon
BOOSTS = (25.0, 50.0)                   # the terms William Hill has run
_NOT_THE_FINAL = ("semi", "quarter", "1/2", "1/4", "1/8", "round of", "qualif", "play-off")


def is_final(round_name) -> bool | None:
    """True / False from the round's name, None when OddsPapi sent none."""
    if isinstance(round_name, dict):
        round_name = round_name.get("name") or round_name.get("roundName")
    if not isinstance(round_name, str) or not round_name.strip():
        return None
    r = round_name.lower()
    return "final" in r and not any(w in r for w in _NOT_THE_FINAL)


def eligible(fixtures: list[dict]) -> list[dict]:
    """The pickable fixtures, kick-off order, in the shape the page shows."""
    per_cup: dict[int, int] = {}
    for fx in fixtures:
        if fx["tournament_id"] in FINALS_ONLY:
            per_cup[fx["tournament_id"]] = per_cup.get(fx["tournament_id"], 0) + 1
    out = []
    for fx in fixtures:
        tid = fx["tournament_id"]
        if tid == PREMIER_LEAGUE:
            if fx["start"].astimezone(LONDON).weekday() not in WEEKEND:
                continue
        elif tid in FINALS_ONLY:
            final = is_final(fx.get("round"))
            if final is False or (final is None and per_cup[tid] != 1):
                continue
        else:
            continue
        out.append({
            "fixture_id": fx["id"], "home": fx["home"], "away": fx["away"],
            "kickoff": fx["start"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "competition": COMPETITIONS[tid] + (" final" if tid in FINALS_ONLY else ""),
        })
    return out


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def current(db: Session, now: datetime | None = None) -> HillsTarget | None:
    """The live target, or None — a target that has kicked off is over."""
    row = db.get(HillsTarget, 1)
    now = now or datetime.now(timezone.utc)
    if row is None or _aware(row.kickoff) <= now:
        return None
    return row


def as_dict(row: HillsTarget | None) -> dict | None:
    if row is None:
        return None
    return {"fixture_id": row.fixture_id, "home": row.home, "away": row.away,
            "match": f"{row.home} vs {row.away}",
            "kickoff": _aware(row.kickoff).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "competition": row.competition, "boost_pct": row.boost_pct,
            "set_by": row.set_by, "set_at": _aware(row.set_at).strftime("%Y-%m-%dT%H:%M:%SZ")}


def set_target(db: Session, fixture: dict, boost_pct: float, user: str) -> HillsTarget:
    """Make `fixture` (one of `eligible`'s rows) the team's target."""
    row = db.get(HillsTarget, 1) or HillsTarget(id=1)
    row.fixture_id, row.home, row.away = fixture["fixture_id"], fixture["home"], fixture["away"]
    row.kickoff = datetime.strptime(fixture["kickoff"], "%Y-%m-%dT%H:%M:%SZ")
    row.competition, row.boost_pct = fixture["competition"], float(boost_pct)
    row.set_by, row.set_at = user, utcnow()
    db.add(row)
    db.commit()
    return row


def clear(db: Session) -> None:
    row = db.get(HillsTarget, 1)
    if row is not None:
        db.delete(row)
        db.commit()
