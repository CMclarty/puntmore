"""BettingIsCool OddsProvider — Pinnacle-referenced coupon fair odds.

The coupon tracker's odds source (SPEC §5). Pinnacle-native: team totals are a
standard market and RAW prices are exposed on every snapshot, which is what
in-house de-vig (§7) and the derived score-grid model (§8) need.

Vendor API (https://api.bettingiscool.com, `X-API-Key` header, OpenAPI at
/openapi.json). Verified against the live API, July 2026:

  GET /api/fixtures?sport_id=29&starts_from=&limit=
      → [{event_id, league_name, runner_home, runner_away, starts, ...}]
  GET /api/odds?event_id=
      → one row per (period, market, line). `main_lines_only=1` collapses this
        to the single main line and is deliberately NOT used — see _odds().
        market  ∈ moneyline | totals | home_totals | away_totals | spread
        period  0 = full match, 1 = first half
        line    the handicap/total (null for moneyline)
        odds0/1/2   RAW prices   ← the pricing path uses these
        todds0/1/2  vendor de-vigged  ← cross-check tests ONLY (§7.1)

  Index convention (confirmed empirically — index 0 is null on every
  two-outcome market, and is the draw price on moneyline):
        0 = draw          1 = home / over        2 = away / under

  Billing: 1 token per request + 1 per row. Quota is reported per UTC DAY via
  X-Quota-Limit / X-Quota-Remaining (500,000/day on the current plan), so the
  free-tier arithmetic that constrains TheOddsApi does not apply here.

De-vig cross-check (July 2026): our `devigd_multi` reproduces the vendor's
`todds` to within 0.03% on all five markets including the 3-way moneyline —
independent confirmation that the power method is the right one. The pricing
path still de-vigs in-house so the provider stays swappable (§7.1).
"""
import difflib
from datetime import datetime
import logging
import re
import time

import httpx

from .base import OddsProvider, MatchQuery, OddsResult
from ..normalise import normalise_match
from ..odds.utils import devigd_multi
from ..pricing.grid import fit_grid, score_grid, p_win_and_over

logger = logging.getLogger("uvicorn.error")

BASE_URL = "https://api.bettingiscool.com"
SOCCER_SPORT_ID = 29
FULL_MATCH_PERIOD = 0
FIXTURE_LIMIT = 500

# Fuzzy-match thresholds. HIGHER than the TheOddsApi provider's 0.6 on purpose:
# that one scores against ~10 fixtures per league, this one against the whole
# soccer board (500+). A bigger candidate pool means more chances for an
# unrelated name to clear a loose threshold — observed live, "Nonexistent FC vs
# Fake United" scored 0.63 against "Newmarket vs North Lakes United". A missed
# match leaves a leg unpriced and visible; a wrong match produces a confident
# wrong price, so err toward missing.
MATCH_THRESHOLD = 0.72
CONFIDENT_THRESHOLD = 0.82
# The board carries derived-market fixtures ("X (Corners) vs Y (Corners)") whose
# names are near-identical to the real fixture. Never price a coupon leg off one.
EXCLUDED_LEAGUE_RE = re.compile(r"\b(corners|bookings|cards)\b", re.IGNORECASE)
# Above this fit residual the parametric form is not describing the market;
# derive nothing rather than emit a confident wrong price. Measured RMSE on a
# real fixture with 12 constraints was 0.0117.
MAX_GRID_RMSE = 0.05

# Resolving which side a pick names. The pick is a fragment ("Lyon Win and
# Over 2.5") scored against a full runner name, so the bar is lower than
# fixture matching — but the two sides must be clearly separated, hence the
# margin. Fail to None; an unresolved side is unpriced, never assumed.
SIDE_THRESHOLD = 0.25
SIDE_MARGIN = 0.10

_OVER_RE = re.compile(r"over\s+([\d.]+)", re.IGNORECASE)
_UNDER_RE = re.compile(r"under\s+([\d.]+)", re.IGNORECASE)
_WIN_RE = re.compile(r"\bwin\b", re.IGNORECASE)


class BettingIsCoolProviderNotConfigured(RuntimeError):
    """Raised when the provider is constructed without an API key."""


def _parse_starts(raw) -> datetime | None:
    """Fixture kickoff. The vendor sends naive ISO strings that are UTC."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        return None


def _side_of(pick: str, runner_home, runner_away) -> bool | None:
    """Which side a pick names: True home, False away, None unresolved.

    Compares the pick against both runner names on the SAME normalise + fuzzy
    path used for fixture matching, and requires a clear winner — a pick that
    scores alike against both ("United v United") is ambiguous, and an ambiguous
    side must go unpriced rather than default to one.
    """
    if not isinstance(runner_home, str) or not isinstance(runner_away, str):
        return None
    p = normalise_match(pick)
    h = difflib.SequenceMatcher(None, p, normalise_match(runner_home)).ratio()
    a = difflib.SequenceMatcher(None, p, normalise_match(runner_away)).ratio()
    if max(h, a) < SIDE_THRESHOLD or abs(h - a) < SIDE_MARGIN:
        return None
    return h > a


def _fair(prices: dict[int, float], want: int) -> float | None:
    """De-vig a market in-house and return the fair price for outcome `want`.

    `prices` maps outcome index → raw price. The FULL outcome set is required:
    the margin is a property of the market, not of one selection. Returns None
    rather than a vigged price if the market is partial or `want` is absent.
    """
    if want not in prices or len(prices) < 2:
        return None
    idx = sorted(prices)
    fair_prices, _margin = devigd_multi([prices[i] for i in idx])
    return round(fair_prices[idx.index(want)], 4)


class BettingIsCoolProvider(OddsProvider):
    def __init__(
        self,
        api_key: str,
        cache_ttl: int | None = None,
        sport_id: int = SOCCER_SPORT_ID,
    ):
        if not api_key:
            raise BettingIsCoolProviderNotConfigured(
                "BETTINGISCOOL_API_KEY is not set"
            )
        self._api_key = api_key
        self._cache_ttl = cache_ttl or 600
        self._sport_id = sport_id
        self._fixtures_cache: tuple[float, list[dict]] | None = None
        self._odds_cache: dict[int, tuple[float, list[dict]]] = {}
        self._grid_cache: dict[int, tuple[float, dict | None]] = {}

    # --- transport ---

    def _get(self, path: str, params: dict) -> list[dict]:
        resp = httpx.get(
            f"{BASE_URL}{path}",
            params=params,
            headers={"X-API-Key": self._api_key},
            timeout=20.0,
        )
        resp.raise_for_status()
        remaining = resp.headers.get("x-quota-remaining")
        if remaining is not None and int(remaining) < 10000:
            logger.warning("bettingiscool quota low: %s left today", remaining)
        data = resp.json()
        return data if isinstance(data, list) else []

    def _fixtures(self) -> list[dict]:
        now = time.time()
        if self._fixtures_cache and now - self._fixtures_cache[0] < self._cache_ttl:
            return self._fixtures_cache[1]
        try:
            rows = self._get(
                "/api/fixtures",
                {
                    "sport_id": self._sport_id,
                    "starts_from": time.strftime("%Y-%m-%d", time.gmtime(now)),
                    "limit": FIXTURE_LIMIT,
                },
            )
        except Exception as exc:
            logger.error("bettingiscool fixtures fetch failed: %s", exc)
            return self._fixtures_cache[1] if self._fixtures_cache else []
        self._fixtures_cache = (now, rows)
        return rows

    def _odds(self, event_id: int) -> list[dict]:
        now = time.time()
        cached = self._odds_cache.get(event_id)
        if cached and now - cached[0] < self._cache_ttl:
            return cached[1]
        try:
            # NOT main_lines_only: coupons name specific lines ("Over 2.5") and
            # the main line is often something else (2.75 on the fixture this
            # was verified against). The full ladder costs ~22 tokens instead of
            # ~11 and is also what the derived score grid needs.
            rows = self._get("/api/odds", {"event_id": event_id})
        except Exception as exc:
            logger.error("bettingiscool odds fetch failed (%s): %s", event_id, exc)
            return cached[1] if cached else []
        self._odds_cache[event_id] = (now, rows)
        return rows

    # --- pricing ---

    @staticmethod
    def _market_prices(rows: list[dict], market: str, line: float | None) -> dict:
        """Raw prices for one market at one line, keyed by outcome index."""
        for r in rows:
            if r.get("period") != FULL_MATCH_PERIOD or r.get("market") != market:
                continue
            if line is not None:
                try:
                    if abs(float(r.get("line")) - line) >= 0.05:
                        continue
                except (TypeError, ValueError):
                    continue
            return {
                i: float(r[f"odds{i}"])
                for i in (0, 1, 2)
                if isinstance(r.get(f"odds{i}"), (int, float))
                and r[f"odds{i}"] > 1.0
            }
        return {}

    @staticmethod
    def _is_half_line(line) -> bool:
        """True for clean .5 lines — the only ones used as fit constraints.

        Asian quarter (x.25/x.75) and integer (x.0) lines are ignored: quarter
        lines are split stakes and integer lines push, so both need special
        handling for no measurable benefit here. Measured on a real fixture,
        dropping them moved RMSE 0.01173 -> 0.01194 and the derived
        Win-and-Over price by 0.9%, while leaving 7 constraints against 3
        parameters — still over-determined.
        """
        try:
            return round(float(line) * 2) % 2 == 1
        except (TypeError, ValueError):
            return False

    @classmethod
    def _grid_constraints(cls, rows: list[dict]) -> list[tuple[str, float | None, float]]:
        """De-vigged market probabilities as score-grid fit constraints."""
        cons: list[tuple[str, float | None, float]] = []
        for r in rows:
            if r.get("period") != FULL_MATCH_PERIOD:
                continue
            prices = {
                i: float(r[f"odds{i}"])
                for i in (0, 1, 2)
                if isinstance(r.get(f"odds{i}"), (int, float)) and r[f"odds{i}"] > 1.0
            }
            if len(prices) < 2:
                continue
            idx = sorted(prices)
            fair_prices, _ = devigd_multi([prices[i] for i in idx])
            prob = {i: 1.0 / p for i, p in zip(idx, fair_prices)}
            market, line = r.get("market"), r.get("line")
            if market == "moneyline" and set(prob) == {0, 1, 2}:
                cons += [
                    ("home_win", None, prob[1]),
                    ("draw", None, prob[0]),
                    ("away_win", None, prob[2]),
                ]
            elif not cls._is_half_line(line):
                continue
            elif market == "totals":
                cons.append(("over", float(line), prob[1]))
            elif market == "home_totals":
                cons.append(("home_over", float(line), prob[1]))
            elif market == "away_totals":
                cons.append(("away_over", float(line), prob[1]))
        return cons

    def _grid_for(self, event_id: int, rows: list[dict]):
        """Fitted grid for an event, cached — several legs share one fixture."""
        now = time.time()
        cached = self._grid_cache.get(event_id)
        if cached and now - cached[0] < self._cache_ttl:
            return cached[1]
        cons = self._grid_constraints(rows)
        # 3 parameters. Demand headroom so the fit is over-determined and the
        # residual actually means something.
        if len(cons) < 5:
            logger.warning(
                "event %s: only %d grid constraints — not fitting", event_id, len(cons)
            )
            self._grid_cache[event_id] = (now, None)
            return None
        try:
            lh, la, rho, rmse = fit_grid(cons)
            grid = score_grid(lh, la, rho)
        except Exception as exc:
            logger.error("grid fit failed for event %s: %s", event_id, exc)
            self._grid_cache[event_id] = (now, None)
            return None
        if rmse > MAX_GRID_RMSE:
            logger.warning(
                "event %s: grid rmse %.4f exceeds %.4f — refusing to derive",
                event_id, rmse, MAX_GRID_RMSE,
            )
            self._grid_cache[event_id] = (now, None)
            return None
        logger.info(
            "event %s grid: lh=%.2f la=%.2f rho=%+.3f rmse=%.4f (%d constraints)",
            event_id, lh, la, rho, rmse, len(cons),
        )
        self._grid_cache[event_id] = (now, grid)
        return grid

    def _fair_odds_for(
        self,
        rows: list[dict],
        market_str: str,
        event_id: int | None = None,
        home: bool = True,
    ) -> float | None:
        """Fair odds for one side of a market. `home` selects which side.

        Team-specific markets are priced for BOTH sides by the caller — value
        can sit on either, and which side a pick names must never be assumed.
        """
        key = market_str.strip().lower()
        has_win = bool(_WIN_RE.search(key))
        over_m = _OVER_RE.search(key)
        under_m = _UNDER_RE.search(key)

        def total(side: int, points: float) -> float | None:
            return _fair(self._market_prices(rows, "totals", points), side)

        if has_win and over_m:
            # DERIVED off the fitted score grid (SPEC §8). Never moneyline ×
            # over: the result and the total are correlated through the shared
            # grid, so the product overstates fair odds. If the grid can't be
            # fitted the leg goes UNPRICED — falling back to the product would
            # reintroduce exactly the bias this replaces.
            if event_id is None:
                return None
            grid = self._grid_for(event_id, rows)
            if grid is None:
                return None
            p = p_win_and_over(grid, float(over_m.group(1)), home=home)
            return round(1.0 / p, 4) if p > 0 else None

        if has_win:
            # Index convention: 1 = home, 2 = away (see module docstring).
            return _fair(self._market_prices(rows, "moneyline", None), 1 if home else 2)
        if over_m:
            return total(1, float(over_m.group(1)))
        if under_m:
            return total(2, float(under_m.group(1)))
        return None

    @staticmethod
    def _is_team_specific(market_str: str) -> bool:
        """True when the market has a home and an away version.

        Match totals do not — "Over 2.5" is a property of the game, so both
        sides would return the same price and the distinction is meaningless.
        """
        return bool(_WIN_RE.search(market_str.strip().lower()))

    @staticmethod
    def _best_match(query: str, candidates: list[str]) -> tuple[str, float] | None:
        q_norm = normalise_match(query)
        norm_to_orig: dict[str, str] = {}
        for c in candidates:
            norm_to_orig.setdefault(normalise_match(c), c)
        scored = [
            (difflib.SequenceMatcher(None, q_norm, c_norm).ratio(), orig)
            for c_norm, orig in norm_to_orig.items()
        ]
        scored.sort(reverse=True)
        if scored and scored[0][0] >= MATCH_THRESHOLD:
            return scored[0][1], scored[0][0]
        return None

    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        by_name: dict[str, dict] = {}
        for f in self._fixtures():
            if EXCLUDED_LEAGUE_RE.search(f.get("league_name", "")):
                continue
            name = f"{f.get('runner_home', '')} vs {f.get('runner_away', '')}"
            by_name.setdefault(name, f)

        results = []
        for q in queries:
            fair = None
            fixture = None
            score = None
            kickoff = None
            fair_home = fair_away = None
            side = None
            match = self._best_match(q.match_name, list(by_name))
            if match:
                fixture, score = match
                if score < CONFIDENT_THRESHOLD:
                    logger.warning(
                        "Borderline fixture match (%.2f): %r -> %r — verify in UI",
                        score, q.match_name, fixture,
                    )
                row = by_name[fixture]
                event_id = row.get("event_id")
                kickoff = _parse_starts(row.get("starts"))
                if event_id is not None:
                    odds_rows = self._odds(event_id)
                    if self._is_team_specific(q.market):
                        # Price BOTH sides. Value can be on either, and the
                        # side a pick names is never assumed — the home side
                        # used to be hardcoded, which priced every away
                        # selection as its opponent.
                        fair_home = self._fair_odds_for(
                            odds_rows, q.market, event_id=event_id, home=True
                        )
                        fair_away = self._fair_odds_for(
                            odds_rows, q.market, event_id=event_id, home=False
                        )
                        side = _side_of(
                            q.match_name, row.get("runner_home"), row.get("runner_away")
                        )
                        # Unresolved side stays UNPRICED rather than guessing;
                        # both sides still ride along so the leg can show where
                        # the value actually is.
                        fair = (
                            fair_home if side is True
                            else fair_away if side is False
                            else None
                        )
                    else:
                        fair = self._fair_odds_for(
                            odds_rows, q.market, event_id=event_id
                        )
            results.append(
                OddsResult(
                    match_id=q.match_id,
                    fair_odds=fair,
                    matched_fixture=fixture,
                    match_score=score,
                    kickoff=kickoff,
                    fair_odds_home=fair_home,
                    fair_odds_away=fair_away,
                    side=side,
                )
            )
        return results
