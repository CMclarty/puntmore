"""Today's 2-up candidates: bet365's price from Bookie Bashing's Early Payout
Tracker or from OddsPapi (`twoup_bet365_source`), Pinnacle's 1X2 from OddsPapi.

THE `bb` SOURCE (the default since 18 Nov 2026; the register, "2-Up and the
Bet Placer"). bet365's price is the tracker's: the Hub's Scraper reads the
tracker and the Hub posts the whole table to `POST /api/2up/bb`, held in
memory (`tracker.py`). The feed makes NO OddsPapi request for bet365. Two
modes run over the tracker's rows, each row saying which flagged it
(`model.flagged_by`):
- `bb`: BB's rating taken as given, value at `twoup_bb_min_rating` or above;
- `pinnacle`: the rescue model, unchanged, against Pinnacle's 1X2 de-vigged,
  with bet365's price from the tracker row; the row is joined to an OddsPapi
  fixture by team names and kick-off (`tracker.match_fixture`), and a row
  that does not join, or joins ambiguously, has no `pinnacle` verdict.
The refresh job fetches Pinnacle's half (one request per chunk, the same
requests as before, so the same cache tiers and no new spend) and composes
the board with the snapshot held; a tracker post composes it again from the
Pinnacle half already held. Neither fetches anything on a request. Pinnacle
failing leaves the `bb` mode serving (a "pinnacle mode: ..." gap, never ALL);
no snapshot, or one older than `twoup_bb_max_age_s`, serves nothing (an
"ALL ..." gap).

HONEST AGES (both sources). The plan grants Pinnacle as `pinnacle+30`, 30
minutes behind: the delay is read off the resolved slug (`slug_delay_s`) and
subtracted from the fetch time, so `fair_priced_at` says when Pinnacle's price
was true. Every row carries `bet365_priced_at`, `fair_priced_at` and
`priced_at`, the older of the two. A Pinnacle price older than
`twoup_pinnacle_max_age_s` gives no `pinnacle` verdict and is counted.

THE `oddspapi` SOURCE (before 18 Nov 2026; OddsPapi refuses bet365 on the
current plan). Per refresh: for each chunk of the qualifying competitions
(`twoup_tournament_ids`, `oddspapi_tournaments_per_request` a request), ONE
`odds-by-tournaments` request for Pinnacle and ONE for bet365, through the
shared `OddsPapiClient`, so its daily budget, rate-limit spacing, quota
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
bet365's price for a side, is not priced: counted, never approximated.

One log line per pass, never one per fixture.

The served board: the scheduler's `twoup_refresh` job calls `refresh()` every
`twoup_feed_ttl`; the route and the `2up` value source read `served()`, which
never fetches. In the `oddspapi` source a pass in which every request failed
(errors, nothing priced), no configured tournament resolved, or the pass
itself raised keeps the last good board and marks it stale; any other pass
replaces it. A chunk whose request failed beside one that priced is named in
`cannot_price` (degraded), its rows simply absent: there is no chunk-level
carry-forward.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ..cards.oddspapi import (
    _as_list, _first, _parse_start, _price, coupon_markets, merge_books,
    participant_names, resolve_tournaments, tournament_names,
)
from ..config import settings
from ..ttl_cache import MISS, TTLCache
from . import model
from .model import Candidate, Params, RescueTable
from .tracker import Fixture, Snapshot, TrackerRow, TrackerStore, match_fixture

logger = logging.getLogger("uvicorn.error")

BET365 = "bet365"
_DELAY_RE = re.compile(r"\+(\d+)$")
_ROLE_TO_INDEX = {1: 0, 0: 1, 2: 2}      # coupon_markets roles -> (home, draw, away)


def slug_delay_s(slug: str | None) -> int:
    """The delay a plan's bookmaker slug names, in seconds: OddsPapi grants a
    delayed feed as `<book>+<minutes>` (`pinnacle+30`: 30 minutes behind).
    A plain slug is live: 0."""
    m = _DELAY_RE.search(slug or "")
    return int(m.group(1)) * 60 if m else 0


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
                 params: Params | None = None, tracker: TrackerStore | None = None):
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
        # `bb` source: the tracker snapshot the Hub posts, and the Pinnacle
        # half the refresh job last fetched, (fixtures, what it saw). A board
        # is composed from the two by the job and by each tracker post, one
        # at a time under the compose lock, each reading the data and the
        # clock inside it, so the board that lands last is the newest.
        self.tracker = tracker or TrackerStore()
        self._pin: tuple[list[Fixture], dict] | None = None
        self._compose_lock = threading.Lock()
        # The resolved Pinnacle slug's delay (`pinnacle+30`: 1800 s), learnt
        # on each pass; a Pinnacle price was true this long before its fetch.
        self.pinnacle_delay_s = 0

    @staticmethod
    def source() -> str:
        """Where bet365's price comes from: `bb` (the tracker) or `oddspapi`."""
        return settings.twoup_bet365_source

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
        explicit = now
        now = now or datetime.now(timezone.utc)
        if self.source() == "bb":
            return self._refresh_bb(now, compose_at=explicit)
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
        now = now or datetime.now(timezone.utc)
        with self._lock:
            at, rows = self._served
        if self.source() == "bb" and self._tracker_gap(now) is not None:
            # No tracker snapshot, or one too old: every row's bet365 price
            # came from it, so neither mode has a row to serve.
            return at, []
        cutoff = now + timedelta(minutes=self.params.min_lead_minutes)
        return at, [c for c in rows if c.kickoff >= cutoff]

    def value(self, now: datetime | None = None) -> list[Candidate]:
        """Candidates that pass the price band and the edge floor, best first."""
        rows = [c for c in self.candidates(now) if model.passes_filters(c, self.params) is None]
        return sorted(rows, key=lambda c: c.edge, reverse=True)

    def status(self, now: datetime | None = None) -> dict:
        """`stale`: the last pass failed and an older board is being served.
        `not_yet_refreshed`: no pass has run since boot, or (`bb` source) no
        tracker snapshot has been posted since boot. Never fetches.

        `bb` source: no snapshot, or one older than `twoup_bb_max_age_s`, is
        an "ALL — ..." entry first in `cannot_price` (nothing is served);
        what the Pinnacle half could not do is a "pinnacle mode: ..." entry,
        never ALL, since the `bb` mode serves its rows without it."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            lp, (at, _), stale = self.last_pass, self._served, self._stale
        cannot = list(lp.get("cannot_price") or [])
        bb = self.source() == "bb"
        not_yet = not lp
        pinnacle = self._pinnacle_status(now)
        if bb:
            gap = self._tracker_gap(now)
            if gap is not None:
                cannot.insert(0, gap)
            not_yet = not_yet or self.tracker.snapshot() is None
            # Read now, never from the last composition: a tracker post
            # composing a new board must not hide that Pinnacle has stalled.
            if pinnacle.get("stalled"):
                cannot.append(pinnacle["stalled"])
        return {
            "provider": "bookiebashing+oddspapi" if bb else "oddspapi",
            "bet365_source": self.source(),
            "degraded": bool(cannot) or not self.table.loaded,
            "cannot_price": cannot + ([] if self.table.loaded
                                      else [f"rescue table unreadable: {self.table.path}"]),
            "stale": stale,
            "served_at": at.isoformat() if at is not None else None,
            "not_yet_refreshed": not_yet,
            "pinnacle": {k: v for k, v in pinnacle.items() if k != "stalled"},
            "last_pass": lp or None,
        }

    def _pinnacle_status(self, now: datetime) -> dict:
        """The Pinnacle side's delay and ages, read now. In the `bb` source
        from the Pinnacle data held (`stalled`: a "pinnacle mode: ..." gap
        when even its freshest price is older than the limit); in the
        `oddspapi` source from the board served."""
        limit = settings.twoup_pinnacle_max_age_s
        if self.source() == "bb":
            held = self._pin                 # swapped whole, so one read is consistent
            fixtures, info = held if held is not None else ([], None)
            times = [f.fair_priced_at for f in fixtures if f.fair_priced_at is not None]
            book = (info or {}).get("book")
        else:
            with self._lock:
                rows = self._served[1]
            times = [c.fair_priced_at for c in rows if c.fair_priced_at is not None]
            book = (self.last_pass.get("books") or {}).get("fair")
        out = {"book": book, "delay_s": self.pinnacle_delay_s, "max_age_s": limit,
               "oldest_fair_priced_at": min(times).isoformat() if times else None,
               "oldest_age_s": round((now - min(times)).total_seconds()) if times else None,
               "newest_age_s": round((now - max(times)).total_seconds()) if times else None}
        if times and (now - max(times)).total_seconds() > limit:
            out["stalled"] = (f"pinnacle mode: the freshest Pinnacle price held is "
                              f"{out['newest_age_s']}s old (limit {limit}s): its refresh has stalled")
        return out

    def tracker_status(self, now: datetime | None = None) -> dict:
        """The held tracker snapshot and what the last composed board made of
        it: for /api/2up/diagnostics. Never fetches."""
        now = now or datetime.now(timezone.utc)
        snap = self.tracker.snapshot()
        with self._lock:
            lp = self.last_pass
        out = {
            "received_at": snap.received_at.isoformat() if snap else None,
            "newest_seen_at": (snap.newest_seen_at.isoformat()
                               if snap and snap.newest_seen_at else None),
            "age_s": round(snap.age_s(now), 1) if snap else None,
            "fresh": snap is not None and snap.age_s(now) <= settings.twoup_bb_max_age_s,
            "rows_received": snap.received if snap else 0,
            "bet365_rows": len(snap.rows) if snap else 0,
            "not_kept": dict(snap.dropped) if snap else {},
        }
        if lp.get("bet365_source") == "bb":
            out.update({k: lp.get(k, 0) for k in (
                "matched", "unmatched", "ambiguous", "no_pinnacle_board", "no_pinnacle",
                "pinnacle_too_old", "pinnacle_priced", "modes_disagree")})
            out["dropped"] = dict(lp.get("dropped") or {})
        return out

    # --- the tracker (`bb` source) --------------------------------------------

    def take_tracker(self, rows: list[TrackerRow], received: int, dropped: dict,
                     now: datetime | None = None) -> tuple[Snapshot | None, str | None]:
        """A tracker post: replace the snapshot and, in `bb` source, compose
        the board again from it and the Pinnacle half already held. Never
        fetches. (snapshot, None), or (None, why) when the post was refused.
        `now` is for tests; the route leaves it out, so the composition takes
        its time inside the compose lock."""
        snap, why = self.tracker.put(rows, received, dropped, now or datetime.now(timezone.utc))
        if snap is not None and self.source() == "bb":
            self.recompose(now)
        return snap, why

    def _tracker_gap(self, now: datetime) -> str | None:
        """The "ALL — ..." reason the tracker cannot be used, or None. Its age
        is the older of since it was received and since its newest row was
        read, so a post delivered late is as old as what it carries."""
        snap = self.tracker.snapshot()
        if snap is None:
            return "ALL — no Bookie Bashing tracker snapshot since boot (POST /api/2up/bb)"
        age = snap.age_s(now)
        if age > settings.twoup_bb_max_age_s:
            return (f"ALL — the Bookie Bashing tracker snapshot is {int(age)}s old "
                    f"(limit {settings.twoup_bb_max_age_s}s)")
        return None

    def _refresh_bb(self, now: datetime, compose_at: datetime | None = None) -> list[Candidate]:
        """The job's pass in `bb` source: Pinnacle's half from OddsPapi (no
        bet365 request), then the board composed with the tracker."""
        try:
            half = self._pinnacle_half(now)
        except Exception as exc:
            logger.exception("2up feed: the Pinnacle pass raised; the bb mode carries on without it")
            info = self._new_half(now)
            info.pop("cannot_price")
            info["errors"].append(f"pass failed: {exc}"[:300])
            info["gaps"].append(f"the Pinnacle pass raised: {exc}"[:300])
            half = ([], info)
        with self._compose_lock:
            self._pin = half
        return self.recompose(compose_at)

    @staticmethod
    def _new_half(now: datetime) -> dict:
        return {"at": now.isoformat(), "fixtures_listed": 0, "fixtures": 0, "no_pinnacle": 0,
                "unnamed": 0, "errors": [], "cannot_price": [], "gaps": []}

    def _pinnacle_half(self, now: datetime) -> tuple[list[Fixture], dict]:
        """Pinnacle's 1X2 board for the configured competitions, through the
        same requests (and so the same cache tiers) as the oddspapi source's
        Pinnacle half. (fixtures for the join, what it saw)."""
        info = self._new_half(now)
        ref = self._reference(info)
        if ref is None:
            info["gaps"] = [c.replace("ALL — ", "", 1) for c in info.pop("cannot_price")]
            return [], info
        info.pop("cannot_price", None)
        ml, names, known, ids = ref
        pin = self._client.resolve_book((settings.oddspapi_fair_book or "pinnacle").strip())
        self.pinnacle_delay_s = slug_delay_s(pin)
        delay = timedelta(seconds=self.pinnacle_delay_s)
        info["book"], info["delay_s"] = pin, self.pinnacle_delay_s
        boards, times, fetched, failed = self._fetch_chunks(info, ids, (pin,), now)
        if fetched == 0:
            info["gaps"].append("every OddsPapi odds request for Pinnacle failed")
            return [], info
        info["gaps"].extend(failed)
        fixtures = []
        for fx in boards[pin]:
            info["fixtures_listed"] += 1
            start = _parse_start(_first(fx, "startTime", "start_time", "commence_time"))
            if start is None:
                continue
            home = _first(fx, "participant1Name") or names.get(str(fx.get("participant1Id")))
            away = _first(fx, "participant2Name") or names.get(str(fx.get("participant2Id")))
            if not home or not away:
                info["unnamed"] += 1
                continue
            p = book_1x2(fx, pin, ml)
            if p is None:
                info["no_pinnacle"] += 1
            tid = fx.get("tournamentId")
            fixtures.append(Fixture(
                fixture_id=str(_first(fx, "fixtureId", "id")), home=str(home), away=str(away),
                start=start, pinnacle=p,
                competition=known.get(tid, "") if tid is not None else "",
                fair_priced_at=_time_for(times[pin], tid, now) - delay,
            ))
        info["fixtures"] = len(fixtures)
        return fixtures, info

    def recompose(self, now: datetime | None = None) -> list[Candidate]:
        """Compose the `bb` source's board from the held tracker snapshot and
        Pinnacle half, and serve it. The snapshot, the Pinnacle half and the
        time (unless a test passes one) are all taken inside the compose lock,
        so of two compositions the one that lands last is built from the
        newest data and carries the later time. A compose that raises keeps
        the board being served and marks it stale."""
        with self._compose_lock:
            now = now or datetime.now(timezone.utc)
            try:
                rows, lp = self._compose(now)
            except Exception as exc:
                logger.exception("2up feed: composing the board raised; any served board is kept, stale")
                lp = {"at": now.isoformat(), "bet365_source": "bb", "priced_fixtures": 0,
                      "candidates": 0, "value": 0, "errors": [f"pass failed: {exc}"[:300]],
                      "cannot_price": [f"ALL — composing the board raised: {exc}"[:300]]}
                with self._lock:
                    self.last_pass = lp
                    self._stale = self._served[0] is not None
                return []
            with self._lock:
                self.last_pass = lp
                self._served = (now, list(rows))
                self._stale = False
                self._cache.set("feed", rows)
            return rows

    def _compose(self, now: datetime) -> tuple[list[Candidate], dict]:
        fixtures, half = self._pin if self._pin is not None else ([], None)
        snap = self.tracker.snapshot()
        lp = {"at": now.isoformat(), "bet365_source": "bb",
              "tracker_received_at": snap.received_at.isoformat() if snap else None,
              "tracker_rows": len(snap.rows) if snap else 0,
              "dropped": {"unknown_age": 0, "row_too_old": 0, "out_of_horizon": 0,
                          "duplicate": 0},
              "matched": 0, "unmatched": 0, "ambiguous": 0, "no_pinnacle_board": 0,
              "no_pinnacle": 0, "pinnacle_too_old": 0, "pinnacle_priced": 0,
              "modes_disagree": 0, "priced_fixtures": 0,
              "candidates": 0, "value": 0, "errors": [], "cannot_price": [],
              "pinnacle": {k: v for k, v in (half or {}).items() if k != "gaps"} or None}
        if half is None:
            lp["cannot_price"].append("pinnacle mode: no Pinnacle pass yet")
        else:
            lp["errors"].extend(half.get("errors") or [])
            lp["cannot_price"].extend(f"pinnacle mode: {g}"[:300] for g in half.get("gaps") or [])
        rows: list[Candidate] = []
        if snap is not None:
            horizon = timedelta(hours=settings.twoup_horizon_hours)
            lead = timedelta(minutes=self.params.min_lead_minutes)
            max_age = settings.twoup_bb_row_max_age_s
            kept: dict[tuple[str, str], TrackerRow] = {}
            usable = 0                  # rows of a known, fresh enough price
            for r in snap.rows:
                at = r.bet365_priced_at
                if at is None:
                    lp["dropped"]["unknown_age"] += 1
                    continue
                if (now - at).total_seconds() > max_age:
                    lp["dropped"]["row_too_old"] += 1
                    continue
                usable += 1
                if not (lead <= r.kickoff - now <= horizon):
                    lp["dropped"]["out_of_horizon"] += 1
                    continue
                key = (r.event_id, r.side)
                if key in kept:
                    # The same selection listed twice: the fresher price stands.
                    lp["dropped"]["duplicate"] += 1
                    if at > kept[key].bet365_priced_at:
                        kept[key] = r
                    continue
                kept[key] = r
            for r in kept.values():
                rows.append(self._tracker_row(r, fixtures, lp, now))
            # A post with nothing usable is not a quiet board. Only rows that
            # were usable and merely outside the horizon make an honest empty one.
            if not usable:
                lp["cannot_price"].insert(0, self._unusable(snap, lp["dropped"]))
        lp["candidates"] = len(rows)
        lp["priced_fixtures"] = len({c.event_id for c in rows})
        lp["value"] = sum(1 for c in rows if model.passes_filters(c, self.params) is None)
        # The modes disagreeing strongly: the Pinnacle model flags a row Bookie
        # Bashing rates below break-even. Counted for Callum; changes nothing.
        lp["modes_disagree"] = sum(
            1 for c in rows if c.bb_rating is not None and c.bb_rating < 100
            and "pinnacle" in model.flagged_by(c, self.params))
        if rows and fixtures and not lp["pinnacle_priced"] and half and not half.get("gaps"):
            lp["cannot_price"].append(
                f"pinnacle mode: no tracker row priced against Pinnacle ({lp['unmatched']} "
                f"unmatched, {lp['ambiguous']} ambiguous, {lp['no_pinnacle']} without "
                f"Pinnacle's 1X2, {lp['pinnacle_too_old']} too old)")
        log = logger.warning if (lp["errors"] or lp["cannot_price"]) else logger.info
        log("2up feed (bb): %d tracker row(s), %d candidate(s), %d value; matched=%d "
            "unmatched=%d ambiguous=%d pinnacle_priced=%d pinnacle_too_old=%d "
            "modes_disagree=%d dropped=%s; %d error(s)%s",
            lp["tracker_rows"], lp["candidates"], lp["value"], lp["matched"], lp["unmatched"],
            lp["ambiguous"], lp["pinnacle_priced"], lp["pinnacle_too_old"],
            lp["modes_disagree"], lp["dropped"], len(lp["errors"]),
            (" — cannot price: " + "; ".join(lp["cannot_price"])) if lp["cannot_price"] else "")
        lp["requests_today"] = getattr(self._client, "requests_today", None)
        return rows, lp

    @staticmethod
    def _unusable(snap: Snapshot, dropped: dict) -> str:
        """The ALL gap for a snapshot with no usable bet365 row, and why."""
        why = {**{k: v for k, v in snap.dropped.items() if v},
               **{k: dropped[k] for k in ("unknown_age", "row_too_old") if dropped.get(k)}}
        detail = ", ".join(f"{k} {v}" for k, v in why.items()) or "none"
        return (f"ALL — the Bookie Bashing tracker snapshot has no usable bet365 row "
                f"({snap.received} received; not used: {detail})")

    def _tracker_row(self, r: TrackerRow, fixtures: list[Fixture], lp: dict,
                     now: datetime) -> Candidate:
        """One tracker row as a candidate: BB's figures always, the Pinnacle
        model's beside them when the row joins a fixture with Pinnacle's 1X2
        no older than `twoup_pinnacle_max_age_s`."""
        if fixtures:
            fx, how = match_fixture(r, fixtures)
            lp[how] += 1
        else:
            fx = None
            lp["no_pinnacle_board"] += 1
        bb = model.bb_only(
            event_id=r.event_id, home=r.home, away=r.away, kickoff=r.kickoff,
            selection=r.selection, side=r.side, price=r.book_odds, rating=r.rating,
            fair=r.n_odds, competition=fx.competition if fx else "",
            bet365_priced_at=r.bet365_priced_at)
        if fx is None:
            return bb
        if fx.pinnacle is None:
            lp["no_pinnacle"] += 1
            return bb
        if (fx.fair_priced_at is None or (now - fx.fair_priced_at).total_seconds()
                > settings.twoup_pinnacle_max_age_s):
            lp["pinnacle_too_old"] += 1
            return bb
        book = (r.book_odds, None, None) if r.side == "home" else (None, None, r.book_odds)
        priced = model.price_fixture(
            event_id=r.event_id, home=r.home, away=r.away, kickoff=r.kickoff,
            pinnacle=fx.pinnacle, bet365=book, table=self.table,
            bet365_priced_at=r.bet365_priced_at, fair_priced_at=fx.fair_priced_at)
        if not priced:
            lp["no_pinnacle"] += 1
            return bb
        lp["pinnacle_priced"] += 1
        return replace(priced[0], selection=r.selection, competition=fx.competition,
                       bb_rating=r.rating, bb_fair=r.n_odds)

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
                "no_pinnacle": 0, "no_bet365": 0, "unnamed": 0, "pinnacle_too_old": 0,
                "priced_fixtures": 0,
                "candidates": 0, "value": 0, "errors": [], "cannot_price": []}

    def _reference(self, lp: dict):
        """(1X2 markets, participant names, tournament names, tournament ids)
        from the client's cached reference data, or None once `lp` says why
        nothing can be asked for (an "ALL — ..." entry)."""
        try:
            ml = moneyline_markets(self._client.catalogue())
            names = participant_names(self._client.participants())
            known = tournament_names(self._client.tournaments())
        except Exception as exc:
            lp["errors"].append(f"reference data: {exc}")
            lp["cannot_price"].append("ALL — OddsPapi reference data failed")
            return None
        if not ml:
            lp["cannot_price"].append("ALL — the catalogue has no full-time 1X2 market")
            return None

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
            return None
        return ml, names, known, ids

    def _fetch_chunks(self, lp: dict, ids: list[int], books: tuple[str, ...], now: datetime):
        """One `odds-by-tournaments` request per book per chunk, through the
        shared client. (boards by book, book -> tournamentId -> when that
        book's chunk was fetched, requests that answered, the failed requests
        named)."""
        per = max(1, settings.oddspapi_tournaments_per_request)
        boards: dict[str, list] = {book: [] for book in books}
        fetched = 0
        failed: list[str] = []      # "<book> chunk [ids]: <error>", one per failed request
        times: dict[str, dict[int, datetime]] = {book: {} for book in books}
        for i in range(0, len(ids), per):
            chunk = ids[i:i + per]
            for book in books:
                try:
                    boards[book].extend(_as_list(self._client.odds_by_tournaments(chunk, bookmaker=book)))
                    fetched += 1
                except Exception as exc:
                    lp["errors"].append(f"{book} {chunk}: {exc}"[:300])
                    failed.append(f"{book} chunk {chunk}: {exc}"[:300])
                    continue
                # Read right after this book's own call, so another thread's
                # later fetch of the same chunk cannot make it look newer.
                at = self._book_fetched_at(chunk, book, now)
                times[book].update({tid: at for tid in chunk})
        return boards, times, fetched, failed

    def _refresh(self, now: datetime) -> tuple[list[Candidate], dict]:
        """One pass: (rows, what it saw). Touches no shared state; `refresh`
        publishes the result."""
        lp = self._new_pass(now)
        ref = self._reference(lp)
        if ref is None:
            return self._finish(lp, [])
        ml, names, known, ids = ref
        pin = self._client.resolve_book((settings.oddspapi_fair_book or "pinnacle").strip())
        b365 = self._client.resolve_book(BET365)
        lp["books"] = {"fair": pin, "priced": b365}
        self.pinnacle_delay_s = lp["pinnacle_delay_s"] = slug_delay_s(pin)
        delay = timedelta(seconds=self.pinnacle_delay_s)
        boards, times, fetched, failed = self._fetch_chunks(lp, ids, (pin, b365), now)
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
            # Pinnacle's price was true the plan's delay before it was fetched.
            fair_at = _time_for(times[pin], tid, now) - delay
            if (now - fair_at).total_seconds() > settings.twoup_pinnacle_max_age_s:
                lp["pinnacle_too_old"] += 1
                continue
            priced = model.price_fixture(
                event_id=str(_first(fx, "fixtureId", "id")), home=str(home), away=str(away),
                kickoff=start, pinnacle=p, bet365=b, table=self.table,
                competition=known.get(tid, "") if tid is not None else "",
                bet365_priced_at=_time_for(times[b365], tid, now), fair_priced_at=fair_at,
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
            if lp["pinnacle_too_old"]:
                lp["cannot_price"].append(
                    f"Pinnacle 1X2 older than {settings.twoup_pinnacle_max_age_s}s on "
                    f"{lp['pinnacle_too_old']} fixture(s)")
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


def _time_for(times: dict[int, datetime], tid, now: datetime) -> datetime:
    """When a book's chunk holding tournament `tid` was fetched. A fixture
    whose tournament is not one asked for (or absent): the stalest chunk,
    never a fresher time than any price had."""
    return times.get(tid) or min(times.values(), default=now)


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
    status = feed.status(now)
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
        note = f"stale: prices from {when} — " + (
            "OddsPapi not answering" if feed.source() == "oddspapi" else "the board could not be composed")
    elif at is None or (status["degraded"] and not served):
        # Every pass since boot failed or raised, so there is no board to
        # serve: an empty list here is "could not price", not "no value".
        note = "cannot price: " + (cannot[0] if cannot else "no board since boot")
    elif status["degraded"]:
        # A board, but part of it could not be priced (a failed chunk, an
        # unreadable rescue table): its rows stand, the gap is named.
        note = "partly priced: " + "; ".join(cannot)
    for c in served:
        if model.passes_filters(c, feed.params.but(min_edge=-1.0, min_bb_rating=0.0)) is not None:
            continue
        # The better of the two modes' EV where both priced the row; its fair
        # is the 2-up-adjusted one that EV implies, so edge = odds/fair - 1.
        ev, payout = c.ev, c.payout_prob
        if c.bb_ev is not None and c.bb_ev > ev:
            ev, payout = c.bb_ev, c.bb_rating / 100.0 / c.price
        if ev < floor or payout <= 0:
            continue
        out.append(ValueSelection(
            source="2up", bookmaker="bet365", event_id=c.event_id, event=c.event,
            home=c.home, away=c.away, kickoff=c.kickoff, sport="soccer",
            market="h2h", selection=c.selection, side=c.side,
            odds=c.price, fair=1.0 / payout, edge=ev, basis="2up",
            seen_at=c.priced_at or now,
        ))
    return out, note
