"""Today's 2-up candidates from OddsPapi — the only odds source.

Per refresh: for each chunk of the qualifying competitions
(`twoup_tournament_ids`, `oddspapi_tournaments_per_request` a request), ONE
`odds-by-tournaments` request for Pinnacle and ONE for bet365, through the
shared `OddsPapiClient` — so its daily budget, rate-limit spacing, quota
guard, failed-request hold and kickoff-tiered cache all apply unchanged. The
default competition list chunks into exactly the requests the coupon tracker
and the Paddy tool already make for Pinnacle, which are therefore cache hits;
bet365 is the only new spend (2 requests per refresh at the defaults).

Both books are resolved through `resolve_book` against the free `/account`,
since a plan can grant a book only under a variant slug ("pinnacle+30") and
asking for the plain one is refused and billed.

The 1X2 market is found in the `/markets` catalogue with the coupon grid's
own reader (`coupon_markets`, market "moneyline"). Pinnacle's three prices
are de-vigged in `model.fair_1x2`; a fixture missing any of them, or missing
bet365's price for a side, is not priced — counted, never approximated.

One log line per pass, never one per fixture.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..cards.oddspapi import (
    _as_list, _first, _parse_start, _price, coupon_markets, merge_books,
    participant_names, resolve_tournaments, tournament_names,
)
from ..config import settings
from ..ttl_cache import MISS, TTLCache
from . import model
from .model import Candidate, Params, RescueTable

logger = logging.getLogger("uvicorn.error")

BET365 = "bet365"
_ROLE_TO_INDEX = {1: 0, 0: 1, 2: 2}      # coupon_markets roles -> (home, draw, away)


def _entries() -> list[str]:
    return [x.strip() for x in (settings.twoup_tournament_ids or "").split(",") if x.strip()]


def moneyline_markets(catalogue) -> dict[str, dict[str, int]]:
    """{marketId: {outcomeId: 0|1|2}} for the full-time 1X2, index 0 = home,
    1 = draw, 2 = away."""
    out = {}
    for mid, (market, _line, sides) in coupon_markets(catalogue).items():
        if market == "moneyline":
            out[mid] = {oid: _ROLE_TO_INDEX[role] for oid, role in sides.items()}
    return out


def book_1x2(fx: dict, book: str, ml: dict[str, dict[str, int]]) -> tuple | None:
    """(home, draw, away) prices from one book on one fixture, or None
    unless all three are priced on one market."""
    odds = fx.get("bookmakerOdds") or {}
    entry = odds.get(book) or odds.get(book.split("+")[0]) or {}
    for mid, market in (entry.get("markets") or {}).items():
        sides = ml.get(str(mid))
        if sides is None or (isinstance(market, dict) and market.get("marketActive") is False):
            continue
        prices = [None, None, None]
        for oid, idx in sides.items():
            outs = market.get("outcomes") or {}
            prices[idx] = _price(outs.get(oid) or outs.get(str(oid)) or {})
        if all(prices):
            return tuple(prices)
    return None


class TwoUpFeed:
    """Priced 2-up candidates, cached `twoup_feed_ttl`. `last_pass` says what
    the last refresh saw; `status()` is what /api/ready reports."""

    def __init__(self, client, *, table: RescueTable | None = None,
                 params: Params | None = None):
        self._client = client
        self.table = table or model.load_rescue_table(settings.twoup_rescue_table_path or None)
        self.params = params or Params.from_settings()
        self._cache = TTLCache(ttl=settings.twoup_feed_ttl, maxsize=1)
        self.last_pass: dict = {}

    # --- public -------------------------------------------------------------

    def candidates(self, now: datetime | None = None, *, force: bool = False) -> list[Candidate]:
        """Every priced side (value or not) of every fixture in the horizon."""
        if not force:
            hit = self._cache.get("feed")
            if hit is not MISS:
                return hit
        rows = self._refresh(now or datetime.now(timezone.utc))
        self._cache.set("feed", rows)
        return rows

    def cached(self) -> list[Candidate] | None:
        """The last priced feed if it is still inside its TTL, else None.
        Never refreshes, so a caller that must not spend a request (the Bet
        Finder pass) reads only what the 2-Up pass already paid for."""
        hit = self._cache.get("feed")
        return None if hit is MISS else hit

    def value(self, now: datetime | None = None) -> list[Candidate]:
        """Candidates that pass the price band and the edge floor, best first."""
        rows = [c for c in self.candidates(now) if model.passes_filters(c, self.params) is None]
        return sorted(rows, key=lambda c: c.edge, reverse=True)

    def status(self) -> dict:
        lp = self.last_pass
        cannot = list(lp.get("cannot_price") or [])
        return {
            "provider": "oddspapi",
            "degraded": bool(cannot) or not self.table.loaded,
            "cannot_price": cannot + ([] if self.table.loaded
                                      else [f"rescue table unreadable: {self.table.path}"]),
            "last_pass": lp or None,
        }

    # --- the pass -------------------------------------------------------------

    def _ids(self, known: dict[int, str]) -> tuple[list[int], list[str]]:
        entries = _entries()
        if all(e.isdigit() for e in entries):
            return [int(e) for e in entries], []
        return resolve_tournaments(entries, known)

    def _refresh(self, now: datetime) -> list[Candidate]:
        lp: dict = {"at": now.isoformat(), "fixtures_listed": 0, "in_horizon": 0,
                    "no_pinnacle": 0, "no_bet365": 0, "unnamed": 0, "priced_fixtures": 0,
                    "candidates": 0, "value": 0, "errors": [], "cannot_price": []}
        try:
            ml = moneyline_markets(self._client.catalogue())
            names = participant_names(self._client.participants())
            known = tournament_names(self._client.tournaments())
        except Exception as exc:
            lp["errors"].append(f"reference data: {exc}")
            lp["cannot_price"].append("ALL — OddsPapi reference data failed")
            self._finish(lp)
            return []
        if not ml:
            lp["cannot_price"].append("ALL — the catalogue has no full-time 1X2 market")
            self._finish(lp)
            return []

        ids, unresolved = self._ids(known)
        lp["tournaments"] = len(ids)
        lp["unresolved_tournaments"] = unresolved
        pin = self._client.resolve_book((settings.oddspapi_fair_book or "pinnacle").strip())
        b365 = self._client.resolve_book(BET365)
        lp["books"] = {"fair": pin, "priced": b365}
        per = max(1, settings.oddspapi_tournaments_per_request)
        boards: dict[str, list] = {pin: [], b365: []}
        fetched = 0
        for i in range(0, len(ids), per):
            chunk = ids[i:i + per]
            for book in (pin, b365):
                try:
                    boards[book].extend(_as_list(self._client.odds_by_tournaments(chunk, bookmaker=book)))
                    fetched += 1
                except Exception as exc:
                    lp["errors"].append(f"{book} {chunk}: {exc}"[:300])
        if fetched == 0:
            lp["cannot_price"].append("ALL — every OddsPapi odds request failed")
            self._finish(lp)
            return []

        horizon = timedelta(hours=settings.twoup_horizon_hours)
        lead = timedelta(minutes=self.params.min_lead_minutes)
        rows: list[Candidate] = []
        for fx in merge_books(boards[pin], boards[b365]):
            lp["fixtures_listed"] += 1
            start = _parse_start(_first(fx, "startTime", "start_time", "commence_time"))
            if start is None or not (lead <= start - now <= horizon):
                continue
            lp["in_horizon"] += 1
            home = _first(fx, "participant1Name") or names.get(str(fx.get("participant1Id")))
            away = _first(fx, "participant2Name") or names.get(str(fx.get("participant2Id")))
            if not home or not away:
                lp["unnamed"] += 1
                continue
            p = book_1x2(fx, pin, ml)
            if p is None:
                lp["no_pinnacle"] += 1
                continue
            b = book_1x2(fx, b365, ml)
            if b is None:
                lp["no_bet365"] += 1
                continue
            tid = fx.get("tournamentId")
            priced = model.price_fixture(
                event_id=str(_first(fx, "fixtureId", "id")), home=str(home), away=str(away),
                kickoff=start, pinnacle=p, bet365=b, table=self.table,
                competition=known.get(tid, "") if tid is not None else "",
            )
            if priced:
                lp["priced_fixtures"] += 1
                rows.extend(priced)
        lp["candidates"] = len(rows)
        lp["value"] = sum(1 for c in rows if model.passes_filters(c, self.params) is None)
        # Fixtures on and NONE priced is a feed that cannot price, not a quiet
        # day — what /api/ready reports as degraded. No fixtures at all is.
        if lp["in_horizon"] and not lp["priced_fixtures"]:
            if lp["no_bet365"]:
                lp["cannot_price"].append(
                    f"bet365 1X2 missing on all {lp['no_bet365']} fixture(s) — "
                    f"is '{b365}' on the OddsPapi plan?")
            if lp["no_pinnacle"]:
                lp["cannot_price"].append(f"Pinnacle 1X2 missing on {lp['no_pinnacle']} fixture(s)")
        self._finish(lp)
        return rows

    def _finish(self, lp: dict) -> None:
        lp["requests_today"] = getattr(self._client, "requests_today", None)
        self.last_pass = lp
        log = logger.warning if (lp["errors"] or lp["cannot_price"]) else logger.info
        log("2up feed: %d fixture(s) in horizon, %d priced, %d candidate(s), %d value; "
            "no_pinnacle=%d no_bet365=%d unnamed=%d; %d error(s)%s",
            lp.get("in_horizon", 0), lp.get("priced_fixtures", 0), lp.get("candidates", 0),
            lp.get("value", 0), lp.get("no_pinnacle", 0), lp.get("no_bet365", 0),
            lp.get("unnamed", 0), len(lp["errors"]),
            (" — cannot price: " + "; ".join(lp["cannot_price"])) if lp["cannot_price"] else "")


def value_rows(feed: TwoUpFeed | None, floor: float, now: datetime):
    """The feed as the cross-tool `ValueSelection` shape (value/service.py).

    `fair` there is the 2-up-ADJUSTED fair price, 1 / (P(win) + P(rescue)),
    so `edge` keeps that shape's meaning — odds / fair - 1, the expected
    return per pound — rather than the probability-unit edge the decision
    ranks by."""
    from ..schemas import ValueSelection
    if feed is None:
        # No OddsPapi key on this deploy: no rows, like the other sources
        # without a client. /api/ready's twoup_detail names it as degraded.
        return []
    out = []
    for c in feed.candidates(now):
        if model.passes_filters(c, feed.params.but(min_edge=-1.0)) is not None:
            continue
        if c.ev < floor:
            continue
        out.append(ValueSelection(
            source="2up", bookmaker="bet365", event_id=c.event_id, event=c.event,
            home=c.home, away=c.away, kickoff=c.kickoff, sport="soccer",
            market="h2h", selection=c.selection, side=c.side,
            odds=c.price, fair=1.0 / c.payout_prob, edge=c.ev, basis="2up",
            seen_at=now,
        ))
    return out
