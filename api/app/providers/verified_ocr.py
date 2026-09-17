"""Every price is read twice before it is believed.

The failure this exists for is a whole column of prices ONE ROW OUT: the
fixture on line 55 carrying the prices printed beside line 54. Nothing
downstream can see it. The rectangle is intact (every row still has every
block), the prices are real prices, the fixtures are real fixtures — only the
association between them has slipped, and a slipped price is a confident wrong
price on a real bet. It reached the tracker more than once, and the fixes that
made the model transcribe rather than interpret did not touch it, because it is
not a matter of interpretation: it is a model losing its place across
seventy-eight dense rows, or a photo in which the columns genuinely do not
line up (see `images.py` for the geometry).

So a priced grid is now read TWICE, by separate calls, over separate pieces of
the page:

  1. The whole sheet, once — the read every consumer already expects. It sets
     the fixture list and its order, the column headings, the title, the
     period and the ladder.
  2. The sheet again as overlapping horizontal STRIPS (`images.strips`), each
     a third of the page. A model transcribing a quarter of a page cannot
     drift a row between the fixture column and the price columns the way it
     can over the full height; and because the strips overlap, most rows are
     in two of them, read by two separate calls.

Then the reads are RECONCILED, cell by cell. A price is kept only where at
least `READS_TO_AGREE` reads report the same value in the same slot. A price
one read saw and no other corroborates, or one two reads disagree on, is left
UNPRICED and counted as disputed — because which read is right is exactly the
thing that cannot be known. A slipped read is outvoted; a slipped read that
happens to agree with nothing is discarded; and a price that appears in the
coupon has, by construction, been read identically at least twice.

An unpriced leg beats a wrongly priced one. This is that rule, applied to the
one failure the rectangle check could not see.

What is NOT verified, and why:
  - Ladder coupons: they print no per-fixture prices; there is nothing to
    cross-check, and the ladder itself is sanitised against the type's board
    (`coupon_types.sanitise_ladder`).
  - Bundle sheets: not a grid, and the same fixture legitimately repeats, so
    a row-keyed reconciliation would be reconciling the wrong thing.
  - The fixture NAMES: the whole-page read owns the list and its order. A
    fixture the strips read that the page did not is appended (it is a row
    the page read dropped); one the page read that no strip corroborates keeps
    its place with its prices disputed.

Cost: one full read plus five strip reads, each about a third the size — the
equivalent of roughly two and a half full reads. Against ~150 uploads a month
that is a few pounds. Latency: strips are read concurrently.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import logging

from .base import ExtractedCoupon, ExtractedRow, OCRProvider, OcrHint
from ..coupon_grid import normalise_board_label
from ..images import strips
from ..normalise import normalise_match

logger = logging.getLogger("uvicorn.error")

# How many separate reads must report the same value for a price to be kept.
# Two: with the full read plus overlapping strips, every row has at least two
# reads and most have three, so two agreeing is "corroborated" and one alone
# is "a claim".
READS_TO_AGREE = 2

# Strip reads in flight at once. Each is a vision call of a minute or so;
# five in series would double the upload time, five at once is fine.
STRIP_WORKERS = 5


class OCRUnverified(ValueError):
    """The second read could not be completed, so nothing was verified.

    A ValueError subclass so the upload's existing handling fails the coupon
    (status "failed", nothing saved) rather than saving the single-pass read
    as though it had been checked. A re-scan is cheap; a coupon that looks
    verified and is not is the exact thing this module exists to prevent.
    """


def describe_ocr(provider) -> str:
    """The provider's name for logs and diagnostics, naming the inner one too
    when it is wrapped — "VerifiedOCRProvider(ClaudeOCRProvider)" says which
    model is doing the reading, which the wrapper's name alone does not."""
    inner = getattr(provider, "inner", None)
    if inner is not None:
        return f"{type(provider).__name__}({type(inner).__name__})"
    return type(provider).__name__


class VerifiedOCRProvider(OCRProvider):
    """Wraps any OCRProvider; priced grids come back cross-checked."""

    def __init__(self, inner: OCRProvider, *, workers: int = STRIP_WORKERS):
        self.inner = inner
        self._workers = max(1, workers)

    def extract(
        self, image_bytes: bytes, media_type: str, hint: OcrHint | None = None
    ) -> ExtractedCoupon:
        whole = self.inner.extract(image_bytes, media_type, hint)
        if hint is None or not hint.priced or hint.bundles or hint.strip:
            return whole

        headings = print_order_headings(whole.rows)
        strip_hint = replace(hint, strip=True, headings=headings)
        pieces = strips(image_bytes, media_type)
        try:
            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                reads = list(pool.map(
                    lambda piece: self.inner.extract(piece[0], piece[1], strip_hint),
                    pieces,
                ))
        except Exception as exc:
            raise OCRUnverified(
                f"strip read failed ({type(exc).__name__}: {exc}) — the coupon "
                f"was read once and could not be checked, so it has not been saved"
            ) from exc

        rows, disputed, appended = reconcile(whole.rows, [r.rows for r in reads])
        log = logger.warning if disputed else logger.info
        log(
            "OCR verification: %d row(s) over %d reads, %d disputed cell(s) left "
            "unpriced, %d row(s) the whole-page read missed",
            len(rows), 1 + len(reads), disputed, appended,
        )
        return replace(
            whole, rows=rows, disputed_cells=disputed, verified=True,
            reads=1 + len(reads),
        )


def print_order_headings(rows) -> tuple[str, ...]:
    """The sheet's column headings, LEFT TO RIGHT as printed.

    `coupon_grid.consensus_blocks` gives the same set but sorted for stable
    audits; a strip needs them in print order, because that is how it is told
    which pair goes under which heading. Print order is the key order of the
    transcribed rows (JSON objects keep it), taken from the rows' consensus so
    one row's stray heading does not become a column.
    """
    if not rows:
        return ()
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for row in rows:
        for position, key in enumerate(row.prices):
            counts[key] = counts.get(key, 0) + 1
            first_seen.setdefault(key, position)
    floor = max(1, len(rows) // 2)
    keys = [k for k, n in counts.items() if n >= floor]
    keys.sort(key=lambda k: first_seen[k])
    return tuple(keys)


def _key(match: str) -> str:
    """How two reads of the same fixture are recognised as the same row.

    Team-normalised (`normalise_match`) so a read that spells "Man Utd" and
    one that spells "Man United" meet, with the grid's plainer key as the
    fallback for a match name the splitter does not recognise as "A v B".
    """
    return normalise_match(match or "") or normalise_board_label(match)


def _index(rows) -> dict[str, ExtractedRow]:
    """Rows by key, first occurrence winning — the same rule as
    `coupon_grid.drop_duplicate_fixtures`: a repeat is the read losing its
    place, and the first is the one read off the row that exists."""
    out: dict[str, ExtractedRow] = {}
    for row in rows:
        out.setdefault(_key(row.match), row)
    return out


def _vote(values, need: int) -> tuple[float | None, bool]:
    """The value at least `need` reads agree on, or None.

    Returns (value, disputed). Disputed is True when SOMETHING was read here
    but not corroborated — one read alone, or reads that disagree with no
    majority. All-null is a plain hole (nobody read anything) and not disputed.
    """
    seen = [v for v in values if v is not None]
    if not seen:
        return None, False
    tally: dict[float, int] = {}
    for v in seen:
        tally[v] = tally.get(v, 0) + 1
    top = max(tally.values())
    winners = [v for v, n in tally.items() if n == top]
    if top >= need and len(winners) == 1:
        return winners[0], False
    return None, True


def reconcile(
    reference, strip_reads, *, need: int = READS_TO_AGREE
) -> tuple[list[ExtractedRow], int, int]:
    """Cross-check the whole-page rows against the strip reads, cell by cell.

    Returns (rows, disputed_cells, appended_rows). The rows come back in the
    reference order, each with every price slot replaced by the value the
    reads agree on — or None, counted as disputed, where they do not. A
    fixture the strips read that the reference missed is appended at the end,
    if at least `need` strip reads carry it; its prices are voted the same way.
    """
    indexes = [_index(reference)] + [_index(rows) for rows in strip_reads]
    order = list(indexes[0])
    appended = []
    for ix in indexes[1:]:
        for key in ix:
            if key in indexes[0] or key in appended:
                continue
            if sum(key in other for other in indexes[1:]) >= need:
                appended.append(key)

    out: list[ExtractedRow] = []
    disputed = 0
    for key in order + appended:
        present = [ix[key] for ix in indexes if key in ix]
        base = present[0]

        blocks: list[str] = []
        for row in present:
            for b in row.prices:
                if b not in blocks:
                    blocks.append(b)
        prices = {}
        for b in blocks:
            pairs = [row.prices[b] for row in present if b in row.prices]
            left, dl = _vote([p[0] for p in pairs], need)
            right, dr = _vote([p[1] for p in pairs], need)
            disputed += int(dl) + int(dr)
            prices[b] = (left, right)

        home, dh = _vote([r.home_odds for r in present], need)
        away, da = _vote([r.away_odds for r in present], need)
        yours, dy = _vote([r.your_odds for r in present], need)
        disputed += int(dh) + int(da) + int(dy)

        out.append(replace(
            base, prices=prices, home_odds=home, away_odds=away, your_odds=yours,
        ))
    return out, disputed, len(appended)
