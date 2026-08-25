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

  * hard bounds (counts.config) refuse to quote outside 4.5-13.5 corners /
    0.5-6.5 cards at all;
  * `edge_vs(require_band=True)` demands a quote clear the whole uncertainty
    band, not the midpoint;
  * the extrapolation penalty raises the bar per count beyond Pinnacle's quoted
    range, because the biggest apparent edges appear exactly where the model is
    weakest and most of them are tail-shape error.

None of those three should be relaxed to make more rows appear. Fewer, better
rows is the entire design.
"""
import logging

from ..pricing.counts.config import CARDS, CORNERS, ModelConfig
from ..pricing.counts.errors import LadderError
from ..pricing.counts.fit import fit_ladder
from ..pricing.counts.ladder import Ladder, Rung
from ..pricing.counts.price import is_priceable, price_line
from .value import CardValueRow, _get_bookmaker, _get_market, _market_label

logger = logging.getLogger("uvicorn.error")

# Counts from the most recent call, for /cards/diagnostics.
LAST_RUN: dict = {}

# Below this many two-sided Pinnacle rungs the fixture is skipped entirely.
# Two rungs pin a mean and let the prior hold the shape; one rung pins nothing.
MIN_RUNGS_TO_PRICE = 2

CORNERS_MARKET = "alternate_totals_corners"
CARDS_MARKET = "alternate_totals_cards"

_CONFIGS: dict[str, ModelConfig] = {
    CORNERS_MARKET: CORNERS,
    CARDS_MARKET: CARDS,
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


def compute_laddered_value(
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
            if not pin_outcomes or not paddy_outcomes:
                continue

            ladder = ladder_from(pin_outcomes, event["id"], market_key, sport_key)

            # A ladder this thin can be FITTED — one rung, the missing side
            # reconstructed, dispersion taken from the prior — but it should not
            # produce a bet. The whole shape would come from a stale league
            # average, and the "fair" price would be an assumption wearing a
            # number. The fit's own sparse handling exists to keep a two-rung
            # ladder honest, not to make a one-rung one bettable.
            two_sided = [r for r in ladder.rungs if r.over and r.under]
            if len(two_sided) < MIN_RUNGS_TO_PRICE:
                refused += 1
                continue

            try:
                fit = fit_ladder(ladder, config)
            except LadderError as exc:
                refused += 1
                logger.info(
                    "cards: %s %s not fittable — %s", event.get("id"), market_key, exc
                )
                continue
            fitted += 1

            for line, sides in sorted(_lines(paddy_outcomes).items()):
                # Only lines Paddy actually offers, and only inside the range
                # the model is allowed to quote.
                if not is_priceable(line, config):
                    continue
                priceable_lines += 1
                try:
                    priced = price_line(fit, line, ladder)
                except LadderError:
                    continue

                for side in ("Over", "Under"):
                    quote = sides.get(side)
                    if quote is None:
                        continue
                    edge = priced.edge_vs(
                        quote, side.lower(), require_band=True, config=config
                    )
                    if edge is None or edge < min_edge:
                        continue

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
                    ))

    LAST_RUN.update({
        "events": len(events), "with_both_books": both_books,
        "ladders_fitted": fitted, "ladders_refused": refused,
        "priceable_paddy_lines": priceable_lines, "rows": len(rows),
    })
    if fitted or refused:
        logger.info(
            "cards laddered pricing: %d ladder(s) fitted, %d refused, %d row(s)",
            fitted, refused, len(rows),
        )
    rows.sort(key=lambda r: r.edge, reverse=True)
    return rows
