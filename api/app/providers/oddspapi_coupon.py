"""OddsPapi implementation of the coupon GridCouponProvider.

One bulk `/odds-by-tournaments` request per CHUNK of tournaments returns
Pinnacle's 1X2 + goal totals + team goal totals for every fixture — all the
score grid is fitted from — so the whole coupon board is a handful of
budget-guarded requests, cached for `cache_ttl`. `_odds()` then reads the
cached board with no further call.

Coverage is the configured OddsPapi tournament list (shared with the Paddy
tool, `oddspapi_tournaments`): a fixture in a league not on that list won't be
on the board and its leg reads `no_match`. Widen coverage by adding ids there.
"""
import logging
import time

from ..config import settings
from ..cards.oddspapi import (
    OddsPapiClient, coupon_markets, participant_names, tournament_names,
    resolve_tournaments, _tournament_entries, _tournament_ids,
    _as_list, _first, _price,
)
from .grid_coupon import FULL_MATCH_PERIOD, GridCouponProvider

logger = logging.getLogger("uvicorn.error")


class OddsPapiCouponProvider(GridCouponProvider):
    def __init__(self, client: OddsPapiClient, cache_ttl: int | None = None):
        super().__init__(cache_ttl)
        self._client = client
        self._fixtures_cache: tuple[float, list[dict]] | None = None
        # event_id -> normalized odds rows, filled by _fixtures() from the bulk
        # board so _odds() needs no extra request.
        self._event_markets: dict[str, list[dict]] = {}

    def _tournament_ids(self) -> list[int]:
        entries = _tournament_entries()
        if all(e.isdigit() for e in entries):
            return _tournament_ids()
        try:
            ids, unresolved = resolve_tournaments(
                entries, tournament_names(self._client.tournaments())
            )
            if unresolved:
                logger.warning("oddspapi coupon: competition(s) not found: %s",
                               ", ".join(unresolved))
            return ids
        except Exception as exc:
            logger.error("oddspapi coupon: tournament list failed: %s", exc)
            return _tournament_ids()

    def _normalize(self, markets: dict, cm: dict) -> list[dict]:
        """Pinnacle markets on one fixture -> the grid's row format."""
        rows = []
        for mid, market in (markets or {}).items():
            meta = cm.get(str(mid))
            if meta is None:
                continue
            kind, line, sides = meta
            outs = market.get("outcomes") or {}
            row = {"period": FULL_MATCH_PERIOD, "market": kind, "line": line}
            priced = 0
            for oid, role in sides.items():
                p = _price(outs.get(oid) or outs.get(str(oid)) or {})
                if p:
                    row[f"odds{role}"] = p
                    priced += 1
            if priced >= 2:
                rows.append(row)
        return rows

    def _fixtures(self) -> list[dict]:
        now = time.time()
        if self._fixtures_cache and now - self._fixtures_cache[0] < self._cache_ttl:
            return self._fixtures_cache[1]
        try:
            cm = coupon_markets(self._client.catalogue())
            names = participant_names(self._client.participants())
            known = tournament_names(self._client.tournaments())
        except Exception as exc:
            logger.error("oddspapi coupon reference data failed: %s", exc)
            return self._fixtures_cache[1] if self._fixtures_cache else []

        ids = self._tournament_ids()
        book = self._client.resolve_book("pinnacle")
        per = max(1, settings.oddspapi_tournaments_per_request)
        fixtures: list[dict] = []
        event_markets: dict[str, list[dict]] = {}
        fetched_any = False
        for i in range(0, len(ids), per):
            try:
                board = self._client.odds_by_tournaments(ids[i:i + per], bookmaker=book)
            except Exception as exc:
                logger.error("oddspapi coupon board failed for %s: %s", ids[i:i + per], exc)
                continue
            fetched_any = True
            for fx in _as_list(board):
                eid = _first(fx, "fixtureId", "id")
                home = _first(fx, "participant1Name") or names.get(str(fx.get("participant1Id")))
                away = _first(fx, "participant2Name") or names.get(str(fx.get("participant2Id")))
                if eid is None or not home or not away:
                    continue
                eid = str(eid)
                markets = ((fx.get("bookmakerOdds") or {}).get(book) or {}).get("markets") or {}
                event_markets[eid] = self._normalize(markets, cm)
                tid = fx.get("tournamentId")
                fixtures.append({
                    "event_id": eid,
                    "runner_home": home,
                    "runner_away": away,
                    "league_name": known.get(tid, "") if tid is not None else "",
                    "starts": _first(fx, "startTime", "start_time", "commence_time"),
                })

        if not fetched_any:
            # Total wipe-out (budget/network) — keep the last good board rather
            # than blank every leg; the next refresh completes it.
            return self._fixtures_cache[1] if self._fixtures_cache else []
        self._event_markets = event_markets
        if not fixtures:
            logger.error(
                "oddspapi coupon board is EMPTY across %d tournament(s) — every "
                "leg will be unmatched. The requests succeeded and returned no "
                "fixtures: check the tournament list and the OddsPapi plan.",
                len(ids),
            )
        self._fixtures_cache = (now, fixtures)
        return fixtures

    def _odds(self, event_id) -> list[dict]:
        return self._event_markets.get(str(event_id), [])
