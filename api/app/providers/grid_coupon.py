"""Provider-agnostic coupon fair odds — the score grid + de-vig + derived
markets + fixture matching, over a Pinnacle board ANY vendor can supply.

`GridCouponProvider` is the coupon tracker's odds engine (SPEC §5/§7/§8). It is
vendor-neutral: a concrete provider implements just two data methods —
`_fixtures()` and `_odds(event_id)` — and everything else (in-house de-vig,
the fitted score grid, GG / GG-No-Draw / TTS / Win-and-Over / Win-to-Nil
derivation, fuzzy fixture matching, the `/api/ready` tally) is shared here.
OddsPapiCouponProvider (oddspapi_coupon.py) is the live implementation.

THE ROW CONTRACT a provider must satisfy:

  _fixtures() -> [{event_id, league_name, runner_home, runner_away, starts, ...}]
      every fixture on the board, each with a stable event_id.
  _odds(event_id) -> [{period, market, line, odds0, odds1, odds2}]
        market  ∈ moneyline | totals | home_totals | away_totals
        period  0 = full match (only period used)
        line    the handicap/total (null for moneyline)
        odds0/1/2   RAW prices (in-house de-vig runs here, so the provider
                    stays swappable — never a vendor's devigged field)
      Index convention: 0 = draw, 1 = home / over, 2 = away / under.
"""
import difflib
from datetime import datetime
import logging
import re

from .base import OddsProvider, MatchQuery, OddsResult
from ..normalise import normalise_match, normalise_sides, split_sides, team_similarity
from ..fixture_checks import excluded_reason, kickoff_distance_min, query_is_senior
from ..picks import (
    OVER_RE, UNDER_RE, WIN_RE, WIN_TO_NIL_RE, TTS_RE, GG_NO_DRAW_RE, GG_RE,
    SIDE_THRESHOLD, SIDE_MARGIN, team_fragment, side_of, is_team_specific,
)
from ..odds.utils import devigd_multi
from ..ttl_cache import MISS, TTLCache
from ..pricing.grid import (
    fit_grid, score_grid, p_win_and_over, p_btts, p_gg_no_draw, p_win_to_nil,
)

logger = logging.getLogger("uvicorn.error")

# A full-match total (period 0) is what the score grid is fitted from; halves
# (period 1) are ignored. A concrete provider's _odds() rows carry this.
FULL_MATCH_PERIOD = 0

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

# Ceilings on the two per-fixture caches. Both are keyed by event id and were
# plain dicts that never dropped anything, so they grew with every fixture the
# app had ever priced — ~110 KB of odds rows and ~14 KB of fitted grid each,
# held until the process died. That is what ran the instance out of memory.
#
# A live sheet is 50-80 fixtures and only ones still to kick off get priced, so
# these sit far above the working set: they are a ceiling, not a policy. Worst
# case is now ~28 MB and ~3.5 MB rather than unbounded.
ODDS_CACHE_MAX = 256
GRID_CACHE_MAX = 256

# Reading a pick's market and team is shared with the fallback provider
# (app/picks.py) — the two used to answer those questions differently, so the
# same coupon priced correctly or crossed onto the wrong team depending on
# which provider a deploy happened to be running. Aliased to the module-private
# names this file has always used.
_OVER_RE = OVER_RE
_UNDER_RE = UNDER_RE
_WIN_RE = WIN_RE
_WIN_TO_NIL_RE = WIN_TO_NIL_RE
_TTS_RE = TTS_RE
_GG_NO_DRAW_RE = GG_NO_DRAW_RE
_GG_RE = GG_RE
_team_fragment = team_fragment
_side_of = side_of


def _parse_starts(raw) -> datetime | None:
    """Fixture kickoff from an ISO string (naive == UTC), or None."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        return None




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


class GridCouponProvider(OddsProvider):
    """Coupon fair odds over a Pinnacle board. Subclass and implement the two
    data methods — `_fixtures()` and `_odds()` — to the row contract in the
    module docstring; the grid, de-vig, derived markets and fixture matching
    are all here."""

    def __init__(self, cache_ttl: int | None = None):
        self._cache_ttl = cache_ttl or 600
        # BOUNDED, and that is not a detail. These are keyed by fixture id and
        # fed by the 10-minute refresh loop, so as plain dicts they accumulated
        # every fixture the app had ever priced — measured at ~110 KB of odds
        # and ~14 KB of fitted grid per fixture, never freed. That is what
        # exhausted the instance's memory. See ttl_cache.py.
        #
        # The ceilings are set well above the working set (a live sheet is
        # 50-80 fixtures, and only fixtures still to kick off get priced) so
        # they never bite in normal use — they exist to make the worst case a
        # bounded number rather than "however long the process has been up".
        self._odds_cache = TTLCache(self._cache_ttl, ODDS_CACHE_MAX)
        self._grid_cache = TTLCache(self._cache_ttl, GRID_CACHE_MAX)
        # The last pricing pass's tally, for /api/ready. A live deploy has no
        # shell, so without this the only record of WHY a coupon is blank is a
        # log line that may already have scrolled away.
        self.last_pass: dict | None = None

    # --- board: a concrete provider supplies these two (see module docstring) ---

    def _fixtures(self) -> list[dict]:
        """Every fixture on the board: dicts with event_id / league_name /
        runner_home / runner_away / starts."""
        raise NotImplementedError

    def _odds(self, event_id) -> list[dict]:
        """Normalized Pinnacle rows for one event: {period, market, line,
        odds0, odds1, odds2}, market in moneyline/totals/home_totals/away_totals."""
        raise NotImplementedError

    # --- public accessors ---
    #
    # The cards/corners tool needs the same board and the same odds rows this
    # provider already fetches and caches, for the DERIVED fixtures the coupon
    # path deliberately filters out ("X (Corners) vs Y (Corners)"). Exposed as
    # methods rather than copied into a second client so there is one transport,
    # one cache and one quota-warning path — the same reason pricing/legs.py is
    # one implementation.

    def board(self) -> list[dict]:
        """Every fixture on the board, INCLUDING the derived-market ones."""
        return self._fixtures()

    def odds_for(self, event_id: int) -> list[dict]:
        """Cached odds rows for one event."""
        return self._odds(event_id)

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
        cached = self._grid_cache.get(event_id)
        if cached is not MISS:
            return cached
        cons = self._grid_constraints(rows)
        # 3 parameters. Demand headroom so the fit is over-determined and the
        # residual actually means something.
        if len(cons) < 5:
            logger.warning(
                "event %s: only %d grid constraints — not fitting", event_id, len(cons)
            )
            self._grid_cache.set(event_id, None)
            return None
        try:
            lh, la, rho, rmse = fit_grid(cons)
            grid = score_grid(lh, la, rho)
        except Exception as exc:
            logger.error("grid fit failed for event %s: %s", event_id, exc)
            self._grid_cache.set(event_id, None)
            return None
        if rmse > MAX_GRID_RMSE:
            logger.warning(
                "event %s: grid rmse %.4f exceeds %.4f — refusing to derive",
                event_id, rmse, MAX_GRID_RMSE,
            )
            self._grid_cache.set(event_id, None)
            return None
        logger.info(
            "event %s grid: lh=%.2f la=%.2f rho=%+.3f rmse=%.4f (%d constraints)",
            event_id, lh, la, rho, rmse, len(cons),
        )
        self._grid_cache.set(event_id, grid)
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
        tts_m = _TTS_RE.search(key)

        def total(side: int, points: float) -> float | None:
            return _fair(self._market_prices(rows, "totals", points), side)

        def derived(fn) -> float | None:
            """Read a probability off the fitted grid, or refuse to price."""
            if event_id is None:
                return None
            grid = self._grid_for(event_id, rows)
            if grid is None:
                return None
            p = fn(grid)
            return round(1.0 / p, 4) if p > 0 else None

        # GG No Draw before GG — the former contains the latter's keyword.
        if _GG_NO_DRAW_RE.search(key):
            return derived(p_gg_no_draw)
        if _GG_RE.search(key):
            return derived(p_btts)

        # "Team to Score N" = that team's total over N-0.5, DIRECT off the
        # vendor's native home_totals / away_totals (no model error).
        #
        # Deliberately NOT derived off the grid when the line is absent. Pinnacle
        # posts the lines it chooses to, and a line it has not priced is simply
        # not priced — that is the market, not a gap to fill with a model.
        if tts_m:
            line = float(tts_m.group(1)) - 0.5
            market = "home_totals" if home else "away_totals"
            return _fair(self._market_prices(rows, market, line), 1)

        # Before the plain-win branch: "Win to Nil" contains "win" and would
        # otherwise take the moneyline price. DERIVED off the grid — the result
        # and the opponent's goals are correlated, so moneyline × clean sheet
        # overstates it (SPEC §8.5, same trap as Win-and-Over).
        if _WIN_TO_NIL_RE.search(key):
            return derived(lambda g: p_win_to_nil(g, home=home))

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

    # Shared with the fallback provider (app/picks.py) — kept as a method
    # because callers and tests reach it through the provider.
    _is_team_specific = staticmethod(is_team_specific)

    @staticmethod
    def _pair_score(q_sides, c_sides) -> float:
        """Similarity of two fixtures compared SIDE BY SIDE.

        Scored as the WEAKER of the two sides (home vs home, away vs away).
        Taking the weaker one is what makes this safer than comparing the whole
        string, not looser: both teams must independently look right, so a
        coincidental overlap on one name cannot carry an unrelated fixture over
        the line. It is also what a single-word difference does not destroy —
        whole-string matching averages a perfect side into a mismatched one and
        loses real fixtures, e.g. "Red Star Belgrade v Larne" against
        "Crvena Zvezda vs Larne" (0.59, below the bar) even though Larne is
        exact and the alias table canonicalises the other side.
        """
        if q_sides is None or c_sides is None:
            return 0.0
        # team_similarity, not a raw character ratio: the two sources disagree
        # by shortening ("Paide" / "Paide Linnameeskond") and word order
        # ("ETO Gyor" / "Gyori ETO"), which characters alone punish.
        home = team_similarity(q_sides[0], c_sides[0])
        away = team_similarity(q_sides[1], c_sides[1])
        return min(home, away)

    @staticmethod
    def _best_match(query: str, candidates: list[str]) -> tuple[str, float] | None:
        q_norm = normalise_match(query)
        q_sides = split_sides(query)
        norm_to_orig: dict[str, str] = {}
        for c in candidates:
            norm_to_orig.setdefault(normalise_match(c), c)

        scored = []
        for c_norm, orig in norm_to_orig.items():
            c_sides = split_sides(orig)
            if q_sides and c_sides:
                # BOTH sides must stand up on their own. The whole-string score
                # is deliberately NOT considered here: when one team's name is
                # long and shared, it carries a fixture over the line despite
                # the other team being someone else entirely — which is how
                # "Horsens v Nordsjaelland" priced off "Brondby vs
                # Nordsjaelland", and "Brommapojkarna v Hammarby" off
                # "Brommapojkarna vs Malmo". Both were confident wrong prices.
                score = GridCouponProvider._pair_score(q_sides, c_sides)
            else:
                # Not a fixture pair (shouldn't happen for a coupon leg, but a
                # malformed name must still be comparable) — fall back to the
                # whole string.
                score = difflib.SequenceMatcher(None, q_norm, c_norm).ratio()
            scored.append((score, orig))

        scored.sort(reverse=True)
        if scored and scored[0][0] >= MATCH_THRESHOLD:
            return scored[0][1], scored[0][0]
        return None

    @classmethod
    def _match_fixture(cls, q: MatchQuery, by_name: dict[str, list[dict]]):
        """The board row this leg is, or why none was accepted.

        Returns ((name, score) | None, row | None, refused_reason | None).

        Candidates the fixture checks refuse — a youth competition for a senior
        row, a kickoff on another day — are removed BEFORE the name match, so a
        refused fixture leaves the leg unmatched rather than passing it to the
        next-best name. Among rows that share the winning name, the one nearest
        the printed kickoff wins: the only way to tell two "England vs Spain"s
        apart when the league check cannot. Never an exclusion on its own, so a
        delayed or time-shifted fixture is still found when it is the only one.
        """
        senior = query_is_senior(q.match_name)
        allowed: dict[str, list[dict]] = {}
        refusals: dict[str, int] = {}
        for name, rows in by_name.items():
            keep = []
            for r in rows:
                why = excluded_reason(
                    senior, q.kickoff, r.get("league_name"), r.get("_starts_dt")
                )
                if why is None:
                    keep.append(r)
                else:
                    refusals[name] = why
            if keep:
                allowed[name] = keep

        match = cls._best_match(q.match_name, list(allowed))
        if match is None:
            # Only when something was refused: would the name have matched it?
            # That separates "the checks turned the fixture away" from "the
            # names never met", which are different fixes. Costs a second name
            # pass for unmatched legs only, over the refused names only.
            refused_names = [n for n in refusals if n not in allowed]
            if refused_names:
                hit = cls._best_match(q.match_name, refused_names)
                if hit:
                    return None, None, {"youth_league": "youth",
                                        "wrong_day": "day"}[refusals[hit[0]]]
            return None, None, None

        rows = allowed[match[0]]
        if len(rows) > 1:
            def distance(r):
                d = kickoff_distance_min(q.kickoff, r.get("_starts_dt"))
                return d if d is not None else 10**9
            rows = sorted(rows, key=distance)
        return match, rows[0], None

    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        board = self._fixtures()
        # EVERY row per fixture name, not the first. Two fixtures can print
        # identically — a senior international and the same nations' U19 game
        # both read "England vs Spain" when the board marks the age group only
        # in the league name. Keeping the first row (the board is fetched day
        # by day from today) handed the leg whichever of them kicked off
        # FIRST, which is how U19 fixtures were priced as senior ones.
        by_name: dict[str, list[dict]] = {}
        for f in board:
            if EXCLUDED_LEAGUE_RE.search(f.get("league_name", "")):
                continue
            name = f"{f.get('runner_home', '')} vs {f.get('runner_away', '')}"
            f.setdefault("_starts_dt", _parse_starts(f.get("starts")))
            by_name.setdefault(name, []).append(f)

        # WHY a leg came back blank, counted. Every one of these used to be the
        # same thing from outside — a selection with no fair odds — so "the feed
        # isn't working" had no answer short of reading the code. They are
        # different problems with different fixes:
        #
        #   no_board   the board is empty          -> config / vendor (above)
        #   no_match   fixture not on the board    -> name, or the wrong week
        #   no_odds    matched, odds fetch empty   -> vendor error for that event
        #   no_market  matched, market not priced  -> Pinnacle does not post it
        #   no_side    market priced, side unclear -> the pick's team name
        #
        # A TTS coupon that is 100% no_market is the vendor declining to post
        # team totals; 100% no_match with a full board is a naming problem;
        # 100% no_board is not a pricing problem at all.
        tally = {
            "legs": len(queries), "board": len(by_name), "priced": 0,
            "no_board": 0, "no_match": 0, "no_odds": 0,
            "no_market": 0, "no_side": 0,
            # Subsets of no_match: the name DID match a fixture, and the
            # fixture checks refused it (fixture_checks.py). Counted apart so
            # "the U19 game was turned away" never reads as "the names failed".
            "refused_youth": 0, "refused_day": 0,
        }

        results = []
        for q in queries:
            fair = None
            fixture = None
            score = None
            kickoff = None
            fair_home = fair_away = None
            side = None
            odds_rows: list[dict] = []
            match, row, refused = self._match_fixture(q, by_name)
            if match is None and refused:
                tally["refused_" + refused] += 1
            if match:
                fixture, score = match
                if score < CONFIDENT_THRESHOLD:
                    logger.warning(
                        "Borderline fixture match (%.2f): %r -> %r — verify in UI",
                        score, q.match_name, fixture,
                    )
                event_id = row.get("event_id")
                kickoff = row.get("_starts_dt")
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
                        # The team is named in the PICK ("Lyon Win and Over
                        # 2.5"), not in match_name ("PSG vs Lyon") — that names
                        # both sides and resolves to neither.
                        side = _side_of(
                            q.market, row.get("runner_home"), row.get("runner_away")
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

            # Classify the blank. Most specific cause first: "the market is not
            # priced" is a better answer than "the side is unclear" when both
            # are true, because the side never mattered.
            if fair is not None:
                tally["priced"] += 1
            elif not by_name:
                tally["no_board"] += 1
            elif match is None:
                tally["no_match"] += 1
            elif not odds_rows:
                tally["no_odds"] += 1
            elif self._is_team_specific(q.market) and (
                fair_home is None and fair_away is None
            ):
                tally["no_market"] += 1
            elif self._is_team_specific(q.market) and side is None:
                tally["no_side"] += 1
            else:
                tally["no_market"] += 1

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

        # ONE line per pass — never one per leg, which on a 154-selection sheet
        # is 154 lines nobody reads. This is the line that answers "why is
        # nothing pricing", so it is INFO when everything priced and WARNING the
        # moment anything did not.
        self.last_pass = tally
        blanks = tally["legs"] - tally["priced"]
        log = logger.info if blanks == 0 else logger.warning
        log(
            "coupon pricing pass: %d/%d priced off a %d-fixture board "
            "(no_board=%d no_match=%d [refused: youth=%d day=%d] no_odds=%d "
            "no_market=%d no_side=%d)",
            tally["priced"], tally["legs"], tally["board"],
            tally["no_board"], tally["no_match"], tally["refused_youth"],
            tally["refused_day"], tally["no_odds"],
            tally["no_market"], tally["no_side"],
        )
        return results
