"""Pricing every line Paddy Power offers, using Pinnacle's whole ladder.

THE PROBLEM THIS SOLVES. `value.py` can only compare a line BOTH books quote:
it walks Pinnacle's lines and looks Paddy Power up at the same number. Books
rarely pick the same line, so most of Paddy's board is invisible — the tool sees
the minority of fixtures where the two happen to agree.

WHAT CHANGES. Pinnacle's rungs are fitted to a count distribution
(pricing/counts) and every line PADDY quotes is priced off it, whether or not
Pinnacle quotes that line. The comparison is still Pinnacle vs Paddy Power; the
model only fills the gap between Pinnacle's rungs.

ONLY WHAT PADDY OFFERS IS PRICED (Callum's call, Aug 2026). Not every line in
the bounds — only lines Paddy actually quotes, because a fair price for a line
nobody offers is a number you cannot bet. It also keeps the work proportional to
the board rather than to the grid.

THE MODEL CANNOT BEAT PINNACLE. Between their rungs it interpolates a sharp
book's own opinion, which is as good as this gets. Outside them it decays toward
the shape assumption, and it reproduces any error Pinnacle makes faithfully. So:

  * hard bounds (counts.config) refuse to quote outside each market's priceable
    range (e.g. 4.5-13.5 corners, 0.5-6.5 cards; goals and the team markets
    carry their own) at all;
  * `edge_vs(require_band=True)` demands a quote clear the whole uncertainty
    band, not the midpoint;
  * the extrapolation penalty raises the bar per count beyond Pinnacle's quoted
    range, because the biggest apparent edges appear exactly where the model is
    weakest and most of them are tail-shape error.

None of those three should be relaxed to make more rows appear. Fewer, better
rows is the entire design.
"""
import logging
import threading

from ..ttl_cache import MISS, TTLCache
from . import shadow_store

from ..pricing.counts.config import CARDS, CORNERS, GOALS, TEAM_CARDS, TEAM_CORNERS, ModelConfig
from .oddspapi import (CARDS_HANDICAP, CORNERS_HANDICAP, TEAM_CARDS_AWAY,
                       TEAM_CARDS_HOME, TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME)
from ..pricing.counts.errors import LadderError
from ..pricing.counts.fit import fit_ladder
from ..pricing.counts.ladder import Ladder, Rung
from ..pricing.counts.lines import INTEGER, is_half_line, line_kind
from ..pricing.counts.price import _band_dispersions, is_priceable, price_line
from ..pricing.counts.dist import CountDist
from ..pricing.counts.split import TeamSplit, estimate_share, price_team_line
from ..odds.utils import devigd
from .value import CardValueRow, _get_bookmaker, _get_market, _market_label

logger = logging.getLogger("uvicorn.error")

# Counts from the most recent call, for /cards/diagnostics.
LAST_RUN: dict = {}

# Fits and line prices, keyed by the exact Pinnacle ladder they came from.
# A fit is ~40 optimiser runs (the uncertainty band refits it several ways),
# ~1.3 s a fixture: 55 fixtures took ~76 s, every 2-minute sweep and every
# page load (live, 15 Oct — the page timed out blank). Pinnacle's ladder
# rarely moves between sweeps, so an unchanged ladder reuses its fit and only
# the edges (cheap) are recomputed. Bounded, like every cache here.
_FITS = TTLCache(ttl=6 * 3600, maxsize=2000)
_PRICES = TTLCache(ttl=6 * 3600, maxsize=10000)
_SPLITS = TTLCache(ttl=6 * 3600, maxsize=2000)    # team shares, per fixture and family
_REFUSED = object()
# One pricing pass at a time: a page load during a sweep waits, then reads the
# fits the sweep just cached, instead of computing the same ones beside it.
_PRICING_LOCK = threading.Lock()


def _excluded_lines() -> dict[str, set[float]]:
    """settings.paddy_excluded_lines ("corners:11.5,cards:6.5") as {market: {line}}."""
    from ..config import settings
    out: dict[str, set[float]] = {}
    for item in (settings.paddy_excluded_lines or "").split(","):
        market, _, line = item.strip().partition(":")
        try:
            out.setdefault(market.strip(), set()).add(float(line))
        except ValueError:
            continue
    return out


def _market_name(market_key: str) -> str:
    if market_key.startswith("team_"):
        return "team_corners" if "corners" in market_key else "team_cards"
    for word in ("corners", "cards", "goals"):
        if word in market_key:
            return word
    return market_key


def _signature(event: dict, market_key: str, sport_key: str, outcomes: list[dict]) -> tuple:
    return (event.get("id"), market_key, sport_key,
            tuple(sorted((o.get("name"), o.get("point"), o.get("price")) for o in outcomes)))

# Below this many two-sided Pinnacle rungs the fixture is skipped entirely.
# Two rungs pin a mean and let the prior hold the shape; one rung pins nothing.
MIN_RUNGS_TO_PRICE = 2

CORNERS_MARKET = "alternate_totals_corners"
CARDS_MARKET = "alternate_totals_cards"
GOALS_MARKET = "totals_goals"     # cards/oddspapi.py::GOALS

_CONFIGS: dict[str, ModelConfig] = {
    CORNERS_MARKET: CORNERS,
    CARDS_MARKET: CARDS,
    GOALS_MARKET: GOALS,
    # A single team's corners/cards — team-scaled bounds, same model.
    TEAM_CORNERS_HOME: TEAM_CORNERS,
    TEAM_CORNERS_AWAY: TEAM_CORNERS,
    TEAM_CARDS_HOME: TEAM_CARDS,
    TEAM_CARDS_AWAY: TEAM_CARDS,
}


def _lines(outcomes: list[dict]) -> dict[float, dict[str, float]]:
    """{line: {"Over": price, "Under": price}} from a market's outcomes."""
    out: dict[float, dict[str, float]] = {}
    for o in outcomes:
        try:
            line = float(o["point"])
            price = float(o["price"])
            side = str(o.get("name", "")).title()
        except (KeyError, TypeError, ValueError):
            continue
        if side in ("Over", "Under") and price > 1.0:
            out.setdefault(line, {})[side] = price
    return out


def ladder_from(outcomes: list[dict], fixture_id: str, market: str,
                league: str | None) -> Ladder:
    """Pinnacle's quoted lines as a Ladder the counts model can fit.

    Rungs quoted on one side only are kept: the fit reconstructs the missing
    side at half the ladder's median margin and down-weights the rung to a
    quarter, which is better than discarding a real observation.
    """
    rungs = [
        Rung(line, over=sides.get("Over"), under=sides.get("Under"))
        for line, sides in sorted(_lines(outcomes).items())
    ]
    return Ladder(fixture_id, market, rungs, league=league)


def _base_market(market_key: str) -> str | None:
    """The MATCH market whose fitted ladder lends a team market its shape."""
    if not market_key.startswith("team_"):
        return None
    return CORNERS_MARKET if "corners" in market_key else CARDS_MARKET


def _match_fit(event: dict, pin: dict, market_key: str, sport_key: str):
    """The fixture's fitted match ladder for a team market, or None.

    Shares the fit cache with the normal path, and needs only Pinnacle's side:
    Paddy may not quote the match market at all and the shape is still real.
    """
    base_key = _base_market(market_key)
    if base_key is None:
        return None
    outcomes = _get_market(pin, base_key)
    if not outcomes:
        return None
    ladder = ladder_from(outcomes, event["id"], base_key, sport_key)
    if len([r for r in ladder.rungs if r.over and r.under]) < MIN_RUNGS_TO_PRICE:
        return None
    sig = _signature(event, base_key, sport_key, outcomes)
    fit = _FITS.get(sig)
    if fit is MISS:
        try:
            fit = fit_ladder(ladder, _CONFIGS[base_key])
        except LadderError:
            fit = _REFUSED
        _FITS.set(sig, fit)
    return None if fit is _REFUSED else fit


_FAMILIES = {
    "corners": (TEAM_CORNERS_HOME, TEAM_CORNERS_AWAY, CORNERS_HANDICAP),
    "cards": (TEAM_CARDS_HOME, TEAM_CARDS_AWAY, CARDS_HANDICAP),
}


def _observable(line: float) -> bool:
    """A .5 or integer line is an observation for the split (the integer one
    push-aware, pricing/counts/split.py); a quarter line is not read."""
    try:
        return is_half_line(line) or line_kind(line) == INTEGER
    except LadderError:
        return False


def _rungs(outcomes: list[dict]) -> list[tuple[float, float]]:
    """EVERY two-sided .5 or integer rung of a team line as (line, devigged
    p_over). Information only — users never see an integer line."""
    out = []
    for line, sides in sorted(_lines(outcomes).items()):
        if _observable(line) and sides.get("Over") and sides.get("Under"):
            fair_over, _, _ = devigd(sides["Over"], sides["Under"])
            out.append((line, 1.0 / fair_over))
    return out


def _handicaps(outcomes: list[dict]) -> list[tuple[float, float]]:
    """EVERY .5 or integer handicap quoted both ways, nearest zero first, as
    (handicap, devigged P(home + handicap > away))."""
    by_h: dict[float, dict[str, float]] = {}
    for o in outcomes:
        try:
            by_h.setdefault(float(o["point"]), {})[str(o.get("name"))] = float(o["price"])
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    for h in sorted(by_h, key=abs):
        sides = by_h[h]
        if _observable(abs(h)) and sides.get("Home") and sides.get("Away"):
            fair_home, _, _ = devigd(sides["Home"], sides["Away"])
            out.append((h, 1.0 / fair_home))
    return out


def team_split(event: dict, pin: dict, market_key: str, sport_key: str):
    """The team SPLIT for a team market's family (corners or cards), or None:
    (cache key, point law, [match law + its dispersion band], share, quoted
    lines per side). pricing/counts/split.py explains the model; this reads
    its three observations off Pinnacle's side of the event — the home line,
    the away line and the handicap — and needs the fixture's match ladder to
    have fitted. Cached per fixture and family, so the two team markets and
    cards/most.py share one estimate.
    """
    base_key = _base_market(market_key)
    if base_key is None:
        return None
    family = "corners" if "corners" in market_key else "cards"
    home_key, away_key, hcap_key = _FAMILIES[family]
    home_out = _get_market(pin, home_key)
    away_out = _get_market(pin, away_key)
    hcap_out = _get_market(pin, hcap_key)
    base_out = _get_market(pin, base_key)
    key = (event.get("id"), family, _signature(event, base_key, sport_key, base_out),
           tuple(sorted((o.get("name"), o.get("point"), o.get("price"))
                        for o in home_out + away_out + hcap_out)))
    hit = _SPLITS.get(key)
    if hit is not MISS:
        return hit

    result = None
    base = _match_fit(event, pin, market_key, sport_key)
    if base is not None:
        home_rungs, away_rungs = _rungs(home_out), _rungs(away_out)
        share = estimate_share(
            base.dist, home_lines=home_rungs, away_lines=away_rungs,
            handicaps=_handicaps(hcap_out),
        )
        if share is not None:
            config = _CONFIGS[base_key]
            totals = [base.dist]
            for d in _band_dispersions(base)[1:]:
                try:
                    totals.append(CountDist(base.lam, d, config.grid_max, config.zero_excess))
                except LadderError:
                    continue

            def span(rungs):
                lines = [line for line, _ in rungs]
                return (min(lines), max(lines)) if lines else None
            quoted = {"home": span(home_rungs), "away": span(away_rungs)}
            result = (key, TeamSplit(base.dist, share.p), totals, share, quoted)
    _SPLITS.set(key, result)
    return result


def _price_paddy_lines(event: dict, market_key: str, sport_key: str, config: ModelConfig,
                       paddy_outcomes: list[dict], tally: dict, min_edge: float,
                       price_fn, rows: list[CardValueRow], basis: str) -> int:
    """Price every line Paddy quotes for one market with `price_fn(line) ->
    FairPrice | None`, appending the value rows. Returns the priceable count.

    ONE body for the fitted ladder and the team split, so the eligibility
    rules (named exclusions, .5 only, the market's bounds) and the edge rule
    (against the band, extrapolation-penalised) cannot drift apart.
    """
    excluded = _excluded_lines().get(_market_name(market_key), set())
    priceable = 0
    for line, sides in sorted(_lines(paddy_outcomes).items()):
        if line in excluded:
            continue
        # Pushes are never bet: only the .5 lines reach pricing, scoring or the
        # feed (is_half_line — shared with the saves and matched-line paths).
        if not is_half_line(line):
            tally["push_lines"] += 1
            continue
        tally["paddy_lines"] += 1
        if not is_priceable(line, config):
            tally["out_of_bounds"] += 1
            continue
        priceable += 1
        priced = price_fn(line)
        if priced is None:
            continue
        for side in ("Over", "Under"):
            quote = sides.get(side)
            if quote is None:
                continue
            # min_edge is the floor, not edge_vs: the acca offers ask for legs a
            # little under fair (see edge_vs.allow_negative).
            edge = priced.edge_vs(
                quote, side.lower(), require_band=True, config=config, allow_negative=True,
            )
            if edge is not None and (tally["best_edge"] is None or edge > tally["best_edge"]):
                tally["best_edge"] = round(edge, 4)
            if edge is None or edge < min_edge:
                continue
            tally["rows"] += 1
            # The row shows the fair the edge was measured against — the band's
            # least favourable end — so Odds ÷ Fair − 1 is the Value the page
            # prints. The point fair stays in the FairPrice for diagnostics.
            _, fair = priced.band_over if side == "Over" else priced.band_under
            if fair <= 0:
                fair = priced.fair_over if side == "Over" else priced.fair_under
            label = _market_label(market_key, side, line)
            if priced.extrapolated:
                # The row must say when its fair price came from beyond
                # Pinnacle's quoted range — same number, different kind of number.
                label += f" +{priced.beyond_ladder:g}"
            rows.append(CardValueRow(
                event_id=event["id"],
                home_team=event.get("home_team", ""),
                away_team=event.get("away_team", ""),
                commence_time=event.get("commence_time", ""),
                sport_key=sport_key,
                market_key=market_key,
                market_label=label,
                line=line,
                side=side,
                paddy_odds=round(quote, 3),
                # No raw Pinnacle price exists for a line they do not quote; the
                # fair price IS the model's answer, so the raw column carries it
                # rather than a fabricated quote.
                pinnacle_odds=round(fair, 3),
                fair_odds=round(fair, 3),
                pinnacle_margin=0.0,
                edge=round(edge, 4),
                basis=basis,
            ))
    return priceable


def compute_laddered_value(events: list[dict], min_edge: float) -> list[CardValueRow]:
    with _PRICING_LOCK:
        return _compute_laddered_value(events, min_edge)


def _compute_laddered_value(
    events: list[dict], min_edge: float,
) -> list[CardValueRow]:
    """Value rows for every line Paddy Power quotes, priced off Pinnacle's ladder.

    Returns the same CardValueRow the matched-line path returns, so the feed,
    the schema and the frontend need no changes — a row is a row whether its
    fair price came from a quoted Pinnacle line or from the fitted curve
    between two of them.
    """
    rows: list[CardValueRow] = []
    fitted = refused = 0
    both_books = priceable_lines = 0
    # Per market, so "no cards rows" says WHICH step lost them: Paddy not
    # pricing it, Pinnacle not posting it, a ladder too thin, or no value.
    by_market = {
        _market_name(mk): dict(paddy_only=0, pinnacle_only=0, both=0, thin=0, matched=0,
                               hybrid=0, split_disagreement=0,
                               # which of Pinnacle's quotes pinned the team shares
                               split_sources=dict(home_line=0, away_line=0, handicap=0),
                               unfittable=0, fitted=0, paddy_lines=0, push_lines=0,
                               out_of_bounds=0, rows=0, best_edge=None)
        for mk in _CONFIGS
    }

    for event in events:
        pin = _get_bookmaker(event, "pinnacle")
        paddy = _get_bookmaker(event, "paddypower")
        if pin is None or paddy is None:
            continue
        both_books += 1
        sport_key = event.get("sport_key", "")

        for market_key, config in _CONFIGS.items():
            pin_outcomes = _get_market(pin, market_key)
            paddy_outcomes = _get_market(paddy, market_key)
            tally = by_market[_market_name(market_key)]
            if not pin_outcomes or not paddy_outcomes:
                if paddy_outcomes:
                    tally["paddy_only"] += 1
                elif pin_outcomes:
                    tally["pinnacle_only"] += 1
                continue
            tally["both"] += 1

            ladder = ladder_from(pin_outcomes, event["id"], market_key, sport_key)

            # A ladder this thin can be FITTED — one rung, the missing side
            # reconstructed, dispersion taken from the prior — but it should not
            # produce a bet. The whole shape would come from a stale league
            # average, and the "fair" price would be an assumption wearing a
            # number. The fit's own sparse handling exists to keep a two-rung
            # ladder honest, not to make a one-rung one bettable.
            two_sided = [r for r in ladder.rungs if r.over and r.under]
            if len(two_sided) < MIN_RUNGS_TO_PRICE:
                tally["thin"] += 1
                # Pinnacle posts a team's corners/cards as ONE line. Too thin to
                # fit a shape on its own, so a team is priced as a binomial
                # SPLIT of the fixture's fitted match ladder — the team lines
                # and the handicap pin the share (pricing/counts/split.py).
                # Rows carry basis "hybrid". A share Pinnacle's own quotes
                # contradict is not priced at all, and counted.
                split = team_split(event, pin, market_key, sport_key)
                if split is not None:
                    skey, _, totals, share, quoted = split
                    if share.disagree:
                        tally["split_disagreement"] += 1
                        continue
                    tally["hybrid"] += 1
                    for kind in share.kinds:
                        tally["split_sources"][kind] += 1
                    home = market_key.endswith("_home")

                    def priced_off_split(line, skey=skey, totals=totals, share=share, home=home,
                                         q=quoted["home" if home else "away"],
                                         grace=config.extrapolation_grace):
                        ck = ("split", skey, home, line)
                        priced = _PRICES.get(ck)
                        if priced is MISS:
                            try:
                                priced = price_team_line(totals, share, line, home,
                                                         quoted_range=q, grace=grace)
                            except LadderError:
                                priced = _REFUSED
                            _PRICES.set(ck, priced)
                        return None if priced is _REFUSED else priced

                    priceable_lines += _price_paddy_lines(
                        event, market_key, sport_key, config, paddy_outcomes, tally,
                        min_edge, priced_off_split, rows, "hybrid",
                    )
                    continue
                else:
                    refused += 1
                    # No match ladder to borrow a shape from. A two-sided rung
                    # is still Pinnacle's own price at that line: where Paddy
                    # quotes the same line, de-vig it directly (the matched-line
                    # maths) — exact, no model; Paddy's other lines stay unpriced.
                    paddy_lines = _lines(paddy_outcomes)
                    for rung in two_sided:
                        if not is_half_line(rung.line) or rung.line not in paddy_lines:
                            continue
                        fair_over, fair_under, margin = devigd(rung.over, rung.under)
                        for side, pin_raw, fair in (("Over", rung.over, fair_over),
                                                    ("Under", rung.under, fair_under)):
                            quote = paddy_lines[rung.line].get(side)
                            if quote is None:
                                continue
                            edge = round(quote / fair - 1, 4)
                            if tally["best_edge"] is None or edge > tally["best_edge"]:
                                tally["best_edge"] = edge
                            if edge < min_edge:
                                continue
                            tally["matched"] += 1
                            tally["rows"] += 1
                            rows.append(CardValueRow(
                                event_id=event["id"],
                                home_team=event.get("home_team", ""),
                                away_team=event.get("away_team", ""),
                                commence_time=event.get("commence_time", ""),
                                sport_key=sport_key,
                                market_key=market_key,
                                market_label=_market_label(market_key, side, rung.line),
                                line=rung.line,
                                side=side,
                                paddy_odds=round(quote, 3),
                                pinnacle_odds=round(pin_raw, 3),
                                fair_odds=round(fair, 3),
                                pinnacle_margin=round(margin, 4),
                                edge=edge,
                                basis="matched",
                            ))
                    continue

            sig = _signature(event, market_key, sport_key, pin_outcomes)
            fit = _FITS.get(sig)
            if fit is MISS:
                try:
                    fit = fit_ladder(ladder, config)
                except LadderError as exc:
                    fit = _REFUSED
                    logger.info(
                        "cards: %s %s not fittable — %s", event.get("id"), market_key, exc
                    )
                _FITS.set(sig, fit)
                if fit is not _REFUSED:
                    # Measure the model against this ladder later, off the
                    # request path (cards/shadow_store.py). No vendor cost.
                    shadow_store.queue(ladder, market_key, config)
            if fit is _REFUSED:
                refused += 1
                tally["unfittable"] += 1
                continue
            fitted += 1
            tally["fitted"] += 1

            def priced_off_ladder(line, sig=sig, fit=fit, ladder=ladder):
                priced = _PRICES.get((sig, line))
                if priced is MISS:
                    try:
                        priced = price_line(fit, line, ladder)
                    except LadderError:
                        priced = _REFUSED
                    _PRICES.set((sig, line), priced)
                return None if priced is _REFUSED else priced

            priceable_lines += _price_paddy_lines(
                event, market_key, sport_key, config, paddy_outcomes, tally,
                min_edge, priced_off_ladder, rows, "ladder",
            )

    LAST_RUN.update({
        "events": len(events), "with_both_books": both_books,
        "ladders_fitted": fitted, "ladders_refused": refused,
        "priceable_paddy_lines": priceable_lines, "rows": len(rows),
        "by_market": by_market,
    })
    if fitted or refused:
        logger.info(
            "cards laddered pricing: %d ladder(s) fitted, %d refused, %d row(s)",
            fitted, refused, len(rows),
        )
    rows.sort(key=lambda r: r.edge, reverse=True)
    return rows
