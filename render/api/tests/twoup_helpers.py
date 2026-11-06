"""Shared fakes for the 2-Up tests: an in-memory DB, a stub OddsPapi
client (never live) and candidate builders."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cards.oddspapi import SOCCER
from app.twoup import model

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 10:00 UTC TOMORROW: inside the London window in GMT and BST alike, and in
# the future, so real-clock checks see these fixtures as live.
NOW = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=10, minute=0, second=0,
                                                               microsecond=0)

CATALOGUE = [
    {"marketId": 101, "marketType": "1x2", "sportId": SOCCER, "period": "fulltime",
     "handicap": 0, "outcomes": [{"outcomeId": 1, "outcomeName": "1"},
                                 {"outcomeId": 2, "outcomeName": "X"},
                                 {"outcomeId": 3, "outcomeName": "2"}]},
    {"marketId": 102, "marketType": "totals", "sportId": SOCCER, "period": "fulltime",
     "handicap": 2.5, "outcomes": [{"outcomeId": 4, "outcomeName": "Over"},
                                   {"outcomeId": 5, "outcomeName": "Under"}]},
]


def _p(price):
    return {"players": {"0": {"price": price, "active": True}}}


def fixture(fid, home, away, start, prices, book, tid=17):
    h, d, a = prices
    return {
        "fixtureId": fid, "tournamentId": tid, "participant1Name": home,
        "participant2Name": away, "startTime": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "bookmakerOdds": {book: {"markets": {"101": {"outcomes": {
            "1": _p(h), "2": _p(d), "3": _p(a)}}}}},
    }


class FakeOddsPapi:
    """The OddsPapiClient surface the feed uses. `boards[book]` is what
    odds_by_tournaments returns; every call is counted."""

    def __init__(self, boards: dict, grants=("pinnacle+30", "bet365")):
        self.boards = boards
        self.grants = grants
        self.calls: list[tuple] = []
        self.fetched: dict[tuple, datetime] = {}
        self.requests_today = 0

    def catalogue(self):
        return CATALOGUE

    def participants(self):
        return []

    def tournaments(self):
        return [{"tournamentId": 17, "tournamentName": "Premier League"}]

    def resolve_book(self, wanted):
        for g in self.grants:
            if g.split("+")[0] == wanted:
                return g
        return wanted

    def odds_by_tournaments(self, ids, bookmaker="paddypower"):
        self.calls.append((tuple(ids), bookmaker))
        self.requests_today += 1
        self.fetched[(tuple(ids), bookmaker)] = NOW
        return self.boards.get(bookmaker, [])

    def fetched_at(self, ids, bookmaker):
        return self.fetched.get((tuple(ids), bookmaker))


def standard_boards(now=NOW):
    """Two fixtures: one where bet365's home price is generous (value), one
    where it is not; plus one fixture bet365 does not price."""
    ko = now + timedelta(hours=5)
    pin = [
        fixture("f1", "Arsenal", "Chelsea", ko, (1.90, 3.70, 4.40), "pinnacle+30"),
        fixture("f2", "Leeds", "Hull", ko, (2.40, 3.30, 3.10), "pinnacle+30"),
        fixture("f3", "Wigan", "Bolton", ko, (2.50, 3.20, 3.00), "pinnacle+30"),
    ]
    b365 = [
        fixture("f1", "Arsenal", "Chelsea", ko, (2.05, 3.50, 4.00), "bet365"),
        fixture("f2", "Leeds", "Hull", ko, (2.30, 3.20, 3.00), "bet365"),
    ]
    return {"pinnacle+30": pin, "bet365": b365}


def cand(event_id="e1", selection="Arsenal", price=2.2, p_win=0.5, rescue=0.02,
         kickoff=None, side="home", now=NOW):
    return model.Candidate(
        event_id=event_id, event=f"{selection} v Other", home=selection, away="Other",
        kickoff=kickoff or now + timedelta(hours=6), selection=selection, side=side,
        price=price, p_win=p_win, rescue=rescue, edge=model.edge(p_win, rescue, price),
    )
