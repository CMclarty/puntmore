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

The served board: the scheduler's `twoup_refresh` job calls `refresh()` every
`twoup_feed_ttl`; the route and the `2up` value source read `served()`, which
never fetches. A pass in which every request failed (errors, nothing priced),
no configured tournament resolved, or the pass itself raised keeps the last
good board and marks it stale; any other pass replaces it. A chunk whose
request failed beside one that priced is named in `cannot_price` (degraded),
its rows simply absent: there is no chunk-level carry-forward.
"""
from __future__ import annotations

import logging
import threading
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
    """Priced 2-up candidates. `refresh()` (the scheduled job) prices a board
    and `served()` hands out the last good one; `candidates()` is the lazy,
    TTL-cached read the tests and `value()` use. `last_pass` says what the
    last pass saw; `status()` is what /api/ready and /diagnostics report."""

    def __init__(self, client, *, table: RescueTable | None = None,
                 params: Params | None = None):
        self._client = client
        self.table = table or model.load_rescue_table(settings.twoup_rescue_table_path or None)
        self.params = params or Params.from_settings()
        self._cache = TTLCache(ttl=settings.twoup_feed_ttl, maxsize=1)
        self.last_pass: dict = {}
        # The refresh job writes on a scheduler thread while requests read:
        # (generated_at, rows) is swapped whole under the lock, never mutated.
        self._lock = threading.Lock()
        self._served: tuple[datetime | None, list[Candidate]] = (None, [])
        self._stale = False

    # --- public -------------------------------------------------------------

    def candidates(self, now: datetime | None = None, *, force: bool = False) -> list[Candidate]:
        """Every priced side (value or not) of every fixture in the horizon."""
        if not force:
            hit = self._cache.get("feed")
            if hit is not MISS:
                return hit
        return self.refresh(now)

    def refresh(self, now: datetime | None = None) -> list[Candidate]:
        """One pass, always fetching (through the client's own cache): what
        the `twoup_refresh` job calls. Returns this pass's rows. A pass with
        errors and nothing priced (every request failed, reference data
        down, no tournament resolved, the pass itself raising) leaves the
        served board as it was and marks it stale; any other pass, an honest
        empty one included, replaces it."""
        now = now or datetime.now(timezone.utc)
        try:
            rows, lp = self._refresh(now)
        except Exception as exc:
            # A malformed market, a resolver bug: the pass is a failed pass,
            # never a silently ageing board with a healthy status.
            logger.exception("2up feed: the pricing pass raised; any served board is kept, stale")
            rows, lp = [], self._new_pass(now)
            lp["errors"].append(f"pass failed: {exc}"[:300])
            lp["cannot_price"].append(f"ALL — the pricing pass raised: {exc}"[:300])
            lp["requests_today"] = getattr(self._client, "requests_today", None)
        with self._lock:
            self.last_pass = lp
            if lp["errors"] and not lp["priced_fixtures"]:
                self._stale = self._served[0] is not None
            else:
                self._served = (now, list(rows))
                self._stale = False
            self._cache.set("feed", rows)
        return rows

    def served(self, now: datetime | None = None) -> tuple[datetime | None, list[Candidate]]:
        """(generated_at, rows) of the board being served; (None, []) before
        the first good pass. Never fetches. A row leaves at the minimum lead
        before kick-off even while its board is still served (a stale board
        through an outage, or the minutes between refreshes)."""
        with self._lock:
            at, rows = self._served
        cutoff = (now or datetime.now(timezone.utc)) + timedelta(minutes=self.params.min_lead_minutes)
        return at, [c for c in rows if c.kickoff >= cutoff]

    def value(self, now: datetime | None = None) -> list[Candidate]:
        """Candidates that pass the price band and the edge floor, best first."""
        rows = [c for c in self.candidates(now) if model.passes_filters(c, self.params) is None]
        return sorted(rows, key=lambda c: c.edge, reverse=True)

    def status(self) -> dict:
        """`stale`: the last pass failed and an older board is being served.
        `not_yet_refreshed`: no pass has run since boot. Never fetches."""
        with self._lock:
            lp, (at, _), stale = self.last_pass, self._served, self._stale
        cannot = list(lp.get("cannot_price") or [])
        return {
            "provider": "oddspapi",
            "degraded": bool(cannot) or not self.table.loaded,
            "cannot_price": cannot + ([] if self.table.loaded
                                      else [f"rescue table unreadable: {self.table.path}"]),
            "stale": stale,
            "served_at": at.isoformat() if at is not None else None,
            "not_yet_refreshed": not lp,
            "last_pass": lp or None,
        }

    # --- the pass -------------------------------------------------------------

    def _ids(self, known: dict[int, str]) -> tuple[list[int], list[str]]:
        entries = _entries()
        if all(e.isdigit() for e in entries):
            return [int(e) for e in entries], []
        return resolve_tournaments(entries, known)

    @staticmethod
    def _new_pass(now: datetime) -> dict:
        """An empty pass record: what `last_pass` holds."""
        return {"at": now.isoformat(), "fixtures_listed": 0, "in_horizon": 0,
                "no_pinnacle": 0, "no_bet365": 0, "unnamed": 0, "priced_fixtures": 0,
                "candidates": 0, "value": 0, "errors": [], "cannot_price": []}

    def _refresh(self, now: datetime) -> tuple[list[Candidate], dict]:
        """One pass: (rows, what it saw). Touches no shared state; `refresh`
        publishes the result."""
        lp = self._new_pass(now)
        try:
            ml = moneyline_markets(self._client.catalogue())
            names = participant_names(self._client.participants())
            known = tournament_names(self._client.tournaments())
        except Exception as exc:
            lp["errors"].append(f"reference data: {exc}")
            lp["cannot_price"].append("ALL — OddsPapi reference data failed")
            return self._finish(lp, [])
        if not ml:
            lp["cannot_price"].append("ALL — the catalogue has no full-time 1X2 market")
            return self._finish(lp, [])

        ids, unresolved = self._ids(known)
        lp["tournaments"] = len(ids)
        lp["unresolved_tournaments"] = unresolved
        if not ids:
            # Nothing to ask for is a failed pass (the last good board is
            # kept), not an empty board and not "every request failed".
            why = "no configured tournament resolved" + (
                f" ({', '.join(unresolved)})" if unresolved else "")
            lp["errors"].append(why[:300])
            lp["cannot_price"].append(f"ALL — {why}"[:300])
            return self._finish(lp, [])
        pin = self._client.resolve_book((settings.oddspapi_fair_book or "pinnacle").strip())
        b365 = self._client.resolve_book(BET365)
        lp["books"] = {"fair": pin, "priced": b365}
        per = max(1, settings.oddspapi_tournaments_per_request)
        boards: dict[str, list] = {pin: [], b365: []}
        fetched = 0
        failed: list[str] = []      # "<book> chunk [ids]: <error>", one per failed request
        # tournamentId -> when its chunk's prices were fetched: the EARLIER of
        # the two books', the stalest price behind a row.
        priced_at: dict[int, datetime] = {}
        for i in range(0, len(ids), per):
            chunk = ids[i:i + per]
            times = []
            for book in (pin, b365):
                try:
                    boards[book].extend(_as_list(self._client.odds_by_tournaments(chunk, bookmaker=book)))
                    fetched += 1
                except Exception as exc:
                    lp["errors"].append(f"{book} {chunk}: {exc}"[:300])
                    failed.append(f"{book} chunk {chunk}: {exc}"[:300])
                    continue
                # Read right after this book's own call, so another thread's
                # later fetch of the same chunk cannot make it look newer.
                times.append(self._book_fetched_at(chunk, book, now))
            at = min(times, default=now)
            priced_at.update({tid: at for tid in chunk})
        if fetched == 0:
            lp["cannot_price"].append("ALL — every OddsPapi odds request failed")
            return self._finish(lp, [])

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
                # A fixture whose tournament is not one asked for (or absent):
                # the stalest chunk, never a fresher time than any price had.
                priced_at=priced_at.get(tid) or min(priced_at.values(), default=now),
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
        # Some requests failed but others priced: the board is replaced, and
        # what the failed chunks would have carried is named — degraded, so a
        # missing competition never looks like a quiet one.
        if lp["priced_fixtures"]:
            lp["cannot_price"].extend(failed)
        # Requests failed and nothing priced: `refresh` keeps the last good
        # board, and the reason must say so even when no fixture was listed.
        if lp["errors"] and not lp["priced_fixtures"] and not lp["cannot_price"]:
            lp["cannot_price"].append(f"ALL — {len(lp['errors'])} OddsPapi odds request(s) "
                                      "failed and nothing was priced")
        return self._finish(lp, rows)

    def _book_fetched_at(self, chunk: list[int], book: str, now: datetime) -> datetime:
        """One book's recorded fetch time for `chunk` — a read, never a
        request. No recorded time (None, or a client that records none, e.g.
        an old fake) counts as `now`. A chunk's time is the earlier of its
        books'."""
        read = getattr(self._client, "fetched_at", None)
        return (read(chunk, book) if read is not None else None) or now

    def _finish(self, lp: dict, rows: list[Candidate]) -> tuple[list[Candidate], dict]:
        lp["requests_today"] = getattr(self._client, "requests_today", None)
        log = logger.warning if (lp["errors"] or lp["cannot_price"]) else logger.info
        log("2up feed: %d fixture(s) in horizon, %d priced, %d candidate(s), %d value; "
            "no_pinnacle=%d no_bet365=%d unnamed=%d; %d error(s)%s",
            lp.get("in_horizon", 0), lp.get("priced_fixtures", 0), lp.get("candidates", 0),
            lp.get("value", 0), lp.get("no_pinnacle", 0), lp.get("no_bet365", 0),
            lp.get("unnamed", 0), len(lp["errors"]),
            (" — cannot price: " + "; ".join(lp["cannot_price"])) if lp["cannot_price"] else "")
        return rows, lp


def value_rows(feed: TwoUpFeed | None, floor: float,
               now: datetime) -> tuple[list, str | None]:
    """The feed as the cross-tool `ValueSelection` shape (value/service.py),
    and a note for `ValueFeedOut.errors` when the source is not answering
    fresh: killed (no rows), stale (the last good board's rows, each with
    its own `seen_at`), not yet refreshed (no rows), cannot price (no board:
    every pass since boot failed or raised), or partly priced (a board with
    a named gap).

    `fair` there is the 2-up-ADJUSTED fair price, 1 / (P(win) + P(rescue)),
    so `edge` keeps that shape's meaning — odds / fair - 1, the expected
    return per pound — rather than the probability-unit edge the 2-Up feed
    ranks its own rows by. `seen_at` is the row's `priced_at`: a stale
    board's prices are as old as they are, never stamped with now."""
    from ..schemas import ValueSelection
    if feed is None:
        # No OddsPapi key on this deploy: no rows, like the other sources
        # without a client. /api/ready's twoup_detail names it as degraded.
        return [], None
    if settings.twoup_kill_switch:
        # The global kill switch empties the feed, and so this source too:
        # /api/value must not show rows /api/2up/feed withholds.
        return [], "killed: TWOUP_KILL_SWITCH is on"
    status = feed.status()
    if status["not_yet_refreshed"]:
        return [], "not yet refreshed"
    out = []
    at, served = feed.served(now)           # the scheduled board; never a fetch
    note = None
    cannot = status["cannot_price"]
    if status["stale"]:
        # The oldest price behind the board, not just when it was built.
        oldest = min((c.priced_at for c in served if c.priced_at), default=at)
        when = f"{oldest.astimezone(timezone.utc):%H:%M} UTC" if oldest else "an earlier pass"
        note = f"stale: prices from {when} — OddsPapi not answering"
    elif at is None or (status["degraded"] and not served):
        # Every pass since boot failed or raised, so there is no board to
        # serve: an empty list here is "could not price", not "no value".
        note = "cannot price: " + (cannot[0] if cannot else "no board since boot")
    elif status["degraded"]:
        # A board, but part of it could not be priced (a failed chunk, an
        # unreadable rescue table): its rows stand, the gap is named.
        note = "partly priced: " + "; ".join(cannot)
    for c in served:
        if model.passes_filters(c, feed.params.but(min_edge=-1.0)) is not None:
            continue
        if c.ev < floor:
            continue
        out.append(ValueSelection(
            source="2up", bookmaker="bet365", event_id=c.event_id, event=c.event,
            home=c.home, away=c.away, kickoff=c.kickoff, sport="soccer",
            market="h2h", selection=c.selection, side=c.side,
            odds=c.price, fair=1.0 / c.payout_prob, edge=c.ev, basis="2up",
            seen_at=c.priced_at or now,
        ))
    return out, note
