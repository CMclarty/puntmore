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
from .oddspapi import (TEAM_CARDS_AWAY, TEAM_CARDS_HOME,
                       TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME)
from ..pricing.counts.errors import LadderError
from ..pricing.counts.fit import fit_ladder
from ..pricing.counts.ladder import Ladder, Rung
from ..pricing.counts.lines import is_half_line
from ..pricing.counts.price import is_priceable, price_line
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


def team_dist(event: dict, pin: dict, market_key: str, sport_key: str):
    """(CountDist, basis) for one team's corners/cards off Pinnacle's side, or
    None. A 2+ rung team ladder fits on its own ("ladder"); one rung borrows the
    match ladder's shape ("hybrid") — the same rule, and the same fit cache, as
    the pricing loop, so cards/most.py never fits twice.
    """
    outcomes = _get_market(pin, market_key)
    config = _CONFIGS.get(market_key)
    if not outcomes or config is None:
        return None
    ladder = ladder_from(outcomes, event["id"], market_key, sport_key)
    two_sided = [r for r in ladder.rungs if r.over and r.under]
    if not two_sided:
        return None
    d_override, basis = None, "ladder"
    if len(two_sided) < MIN_RUNGS_TO_PRICE:
        base = _match_fit(event, pin, market_key, sport_key)
        if base is None:
            return None
        d_override, basis = base.dispersion, "hybrid"
    sig = _signature(event, market_key, sport_key, outcomes)
    if d_override is not None:
        sig = sig + ("hybrid", round(d_override, 4))
    fit = _FITS.get(sig)
    if fit is MISS:
        try:
            fit = fit_ladder(ladder, config, d_override=d_override)
        except LadderError:
            fit = _REFUSED
        _FITS.set(sig, fit)
    return None if fit is _REFUSED else (fit.dist, basis)


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
                               hybrid=0, unfittable=0, fitted=0, paddy_lines=0, push_lines=0,
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
            hybrid_d = None
            if len(two_sided) < MIN_RUNGS_TO_PRICE:
                tally["thin"] += 1
                # Pinnacle posts a team's corners/cards as ONE line. Too thin to
                # fit a SHAPE on its own — but the fixture's MATCH ladder (4-5
                # rungs) gives a fixture-specific dispersion, and the team rung
                # pins the team's mean: a hybrid fit, both parameters from live
                # Pinnacle money on this game. It carries the wide sparse band,
                # the extrapolation penalty and a "hybrid" basis on every row.
                base = _match_fit(event, pin, market_key, sport_key) if two_sided else None
                if base is not None:
                    hybrid_d = base.dispersion
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
            if hybrid_d is not None:
                sig = sig + ("hybrid", round(hybrid_d, 4))
            fit = _FITS.get(sig)
            if fit is MISS:
                try:
                    fit = fit_ladder(ladder, config, d_override=hybrid_d)
                    if hybrid_d is not None:
                        fit.flags.append("hybrid_dispersion")
                except LadderError as exc:
                    fit = _REFUSED
                    logger.info(
                        "cards: %s %s not fittable — %s", event.get("id"), market_key, exc
                    )
                _FITS.set(sig, fit)
                if fit is not _REFUSED and hybrid_d is None:
                    # Measure the model against this ladder later, off the
                    # request path (cards/shadow_store.py). No vendor cost. A
                    # one-rung hybrid has nothing to measure itself against.
                    shadow_store.queue(ladder, market_key, config)
            if fit is _REFUSED:
                refused += 1
                tally["unfittable"] += 1
                continue
            if hybrid_d is not None:
                tally["hybrid"] += 1
            else:
                fitted += 1
                tally["fitted"] += 1

            excluded = _excluded_lines().get(_market_name(market_key), set())
            for line, sides in sorted(_lines(paddy_outcomes).items()):
                # Only lines Paddy actually offers, only .5 lines, and only
                # inside the range the model is allowed to quote.
                if line in excluded:
                    continue
                # Pushes are never bet: only the .5 lines reach pricing, scoring
                # or the feed (is_half_line — shared with the saves and
                # matched-line paths, so no path can leak one).
                if not is_half_line(line):
                    tally["push_lines"] += 1
                    continue
                tally["paddy_lines"] += 1
                if not is_priceable(line, config):
                    tally["out_of_bounds"] += 1
                    continue
                priceable_lines += 1
                priced = _PRICES.get((sig, line))
                if priced is MISS:
                    try:
                        priced = price_line(fit, line, ladder)
                    except LadderError:
                        priced = _REFUSED
                    _PRICES.set((sig, line), priced)
                if priced is _REFUSED:
                    continue

                for side in ("Over", "Under"):
                    quote = sides.get(side)
                    if quote is None:
                        continue
                    # min_edge is the floor, not edge_vs: the acca offers ask for
                    # legs a little under fair (see edge_vs.allow_negative).
                    edge = priced.edge_vs(
                        quote, side.lower(), require_band=True, config=config,
                        allow_negative=True,
                    )
                    if edge is not None and (tally["best_edge"] is None or edge > tally["best_edge"]):
                        tally["best_edge"] = round(edge, 4)
                    if edge is None or edge < min_edge:
                        continue
                    tally["rows"] += 1

                    fair = priced.fair_over if side == "Over" else priced.fair_under
                    label = _market_label(market_key, side, line)
                    if priced.extrapolated:
                        # The row must say when its fair price came from beyond
                        # Pinnacle's quoted range — same number, different kind
                        # of number.
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
                        # No raw Pinnacle price exists for a line they do not
                        # quote; the fair price IS the model's answer, so the
                        # raw column carries it rather than a fabricated quote.
                        pinnacle_odds=round(fair, 3),
                        fair_odds=round(fair, 3),
                        pinnacle_margin=0.0,
                        edge=round(edge, 4),
                        basis="hybrid" if hybrid_d is not None else "ladder",
                    ))

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
