"""The sheet as a RECTANGLE — a coupon is a solved puzzle, not a bag of rows.

A priced coupon is printed as a grid: R fixtures down the page, B market blocks
across it, the same B on every row. That is R x B cells, and the shape is a
property of the SHEET, knowable independently of whether every cell can be
read. A price too faint to transcribe is a hole in a known grid — not a missing
column, and not a shorter coupon.

Everything here exists because the pipeline used to lose that distinction. A
malformed pair, an illegible pair and a block the sheet never printed all
arrived downstream as the same thing — nothing, silently — so a bad read looked
exactly like a coupon that simply had less on it. The read is now measured
against the rectangle it claims to be, and what doesn't fit is COUNTED.

Nothing here changes what gets priced: an unreadable cell still produces no leg,
because an unpriced leg beats a wrongly priced one. What changes is that the
coupon now knows how much of itself it failed to read, and can say so.

Deliberately a whole-SHEET verdict, never a per-row one. The per-row anomaly
checks and their badges were removed in July 2026 (the rows get checked by hand
anyway) and are not coming back; "this scan lost 40 cells, re-scan it" is a
different statement from "this row looks odd", and only the first is actionable.
"""
from dataclasses import dataclass, field
import difflib
import re

# A heading is a column of the sheet if it appears on more than this share of
# the rows. Set at half: the six blocks print on every row, so a genuine column
# clears this by miles, while a heading the model hallucinated on a handful of
# rows — or misspelled on a few — falls below it and is reconciled or dropped.
CONSENSUS_FLOOR = 0.5

# Similarity required between the ALPHABETIC parts of two headings before they
# are treated as the same column. See headings_match for why the digits are
# compared separately and exactly.
HEADING_SIMILARITY = 0.85

# Share of the grid's cells that must carry at least one readable price for the
# scan to be called clean. A sheet always has some illegible corners; losing
# more than a tenth of it means the photo, not the print.
CLEAN_CELL_FLOOR = 0.9


def normalise_board_label(label: str | None) -> str:
    """A block heading reduced to what identifies it.

    The heading is transcribed from a photo, so case, spacing, punctuation and
    the "&"/"AND" spelling all vary between reads of the same sheet. Everything
    that isn't a letter or a digit goes, so "WIN & 2 OR MORE GOALS",
    "Win and 2 or more goals" and "WIN &  2 OR MORE  GOALS" are one key.
    """
    if not label:
        return ""
    text = str(label).upper().replace("&", " AND ")
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text).split())


def _digits(key: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+", key))


def _alpha(key: str) -> str:
    return re.sub(r"\d+", "", key)


def headings_match(a: str, b: str) -> bool:
    """Are these two transcriptions of the SAME column heading?

    NOT a plain similarity score, and this is the important part. The six blocks
    on a Hill grid differ from each other by a single digit:

        TEAM TO SCORE 1 OR MORE GOALS
        TEAM TO SCORE 2 OR MORE GOALS

    difflib scores that pair at 0.97 — higher than most thresholds anyone would
    pick, and higher than the margin between a right answer and a wrong one. A
    fuzzy match tuned loose enough to survive an OCR typo is therefore also
    loose enough to file a block's prices under its neighbour's heading, which
    is the exact failure this whole path exists to prevent.

    So the two halves are judged differently. The DIGITS are the discriminator
    and must match exactly; the letters are what OCR fumbles, and are compared
    fuzzily. "TEAM TO SCORE 1 OR MOR GOALS" matches its own heading and no other
    — and a digit misread by the camera fails to match anything at all, which
    leaves the block unread rather than crossed onto the wrong market.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if _digits(a) != _digits(b):
        return False
    return difflib.SequenceMatcher(None, _alpha(a), _alpha(b)).ratio() >= HEADING_SIMILARITY


def resolve_heading(label: str | None, blocks) -> str | None:
    """The consensus heading a coupon type's board_label refers to, if any.

    Returns None when the sheet carries no such column — which is the honest
    answer for a photo of a different sheet, and stops a type being priced off
    a block that isn't its own.
    """
    key = normalise_board_label(label)
    if not key:
        return None
    for b in blocks:
        if headings_match(key, b):
            return b
    return None


@dataclass(frozen=True)
class GridAudit:
    """How much of the rectangle survived the read.

    `cells` is what the sheet PRINTED (rows x blocks). The other three account
    for every one of them, so read + holes + absent == cells always:

      read    at least one of the pair transcribed — the cell did its job
      holes   the block was reported for this row, both prices unreadable
      absent  the row didn't carry the block at all, though the sheet does

    `holes` is an honest coupon and a poor photo. `absent` is the read losing
    structure the sheet has — the number that says re-scan.

    `disputed` is a subset of the holes with a different meaning: a PRICE was
    read there, but the independent reads of the same cell did not agree, so
    it was left unpriced rather than trusted (`providers/verified_ocr.py`).
    A hole is a cell nobody could read; a disputed cell is one somebody read
    WRONG — and which read it was is unknowable, which is why neither is kept.
    """
    rows: int = 0
    blocks: tuple[str, ...] = ()
    read: int = 0
    holes: int = 0
    absent: int = 0
    malformed: int = 0
    duplicates: tuple[str, ...] = ()
    disputed: int = 0

    @property
    def cells(self) -> int:
        return self.rows * len(self.blocks)

    @property
    def coverage(self) -> float:
        """Share of the grid that carries a price. 1.0 when nothing was lost."""
        return self.read / self.cells if self.cells else 1.0

    @property
    def clean(self) -> bool:
        """A read worth trusting whole: the rectangle is intact and no fixture
        was transcribed twice. Illegible cells alone do not fail it — the sheet
        is a photograph, and a hole in a known grid is still a solved grid."""
        return (
            not self.duplicates
            and not self.malformed
            and self.absent == 0
            and self.disputed == 0
            and self.coverage >= CLEAN_CELL_FLOOR
        )

    def summary(self) -> str:
        """One line, written for whoever has to decide whether to re-scan."""
        if not self.blocks:
            return f"{self.rows} row(s), no market grid"
        parts = [f"{self.rows} rows x {len(self.blocks)} blocks",
                 f"{self.read}/{self.cells} priced"]
        if self.holes:
            parts.append(f"{self.holes} unreadable")
        if self.absent:
            parts.append(f"{self.absent} MISSING")
        if self.disputed:
            parts.append(f"{self.disputed} DISPUTED between reads (left unpriced)")
        if self.malformed:
            parts.append(f"{self.malformed} malformed")
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} duplicate fixture(s)")
        return ", ".join(parts)

    def as_dict(self) -> dict:
        return {
            "rows": self.rows,
            "blocks": list(self.blocks),
            "cells": self.cells,
            "read": self.read,
            "holes": self.holes,
            "absent": self.absent,
            "malformed": self.malformed,
            "disputed": self.disputed,
            "duplicates": list(self.duplicates),
            "clean": self.clean,
            "summary": self.summary(),
        }


def consensus_blocks(rows) -> tuple[str, ...]:
    """The sheet's own column set, derived from the read rather than assumed.

    A heading on most rows is a column; a heading on three rows out of seventy
    is a misspelling of one (reconciled below) or noise. Deriving it beats
    taking it from config, because it is the PHOTOGRAPHED sheet that has to be
    accounted for — including a sheet whose layout changed this week.
    """
    if not rows:
        return ()
    counts: dict[str, int] = {}
    for row in rows:
        for key in row.prices:
            counts[key] = counts.get(key, 0) + 1
    floor = max(1, int(len(rows) * CONSENSUS_FLOOR))
    winners = [k for k, n in counts.items() if n >= floor]
    # Most-printed first, then alphabetical, so the order is stable across reads
    # of the same sheet and the stored audit is comparable between issues.
    winners.sort(key=lambda k: (-counts[k], k))
    return tuple(winners)


def reconcile(rows, blocks: tuple[str, ...]) -> int:
    """Fold near-miss headings onto the consensus column they belong to.

    A single mistranscribed letter used to cost that row its whole block: the
    key missed, `_pair_for` found nothing, and the row silently priced one
    market short. Returns the number of headings repaired.

    Mutates the rows in place — they are freshly parsed per upload and belong to
    nobody else yet.
    """
    if not blocks:
        return 0
    repaired = 0
    known = set(blocks)
    for row in rows:
        for key in list(row.prices):
            if key in known:
                continue
            target = next((b for b in blocks if headings_match(key, b)), None)
            # Only fills a gap. If the row already has the real heading, the
            # stray is a second reading of a column already accounted for, and
            # overwriting a good pair with a doubtful one is a bad trade.
            if target and target not in row.prices:
                row.prices[target] = row.prices.pop(key)
                repaired += 1
    return repaired


def drop_duplicate_fixtures(rows) -> tuple[list, tuple[str, ...]]:
    """One line per match. Returns (kept rows, names seen more than once).

    A long grid transcribed in one pass can repeat a fixture, and a repeat is
    never a coupon printing the same match twice — it is the read losing its
    place. The first occurrence is kept: it is the one whose prices were read
    off the row that actually exists.
    """
    seen: set[str] = set()
    kept, dupes = [], []
    for row in rows:
        key = normalise_board_label(row.match)
        if key and key in seen:
            dupes.append(row.match)
            continue
        seen.add(key)
        kept.append(row)
    return kept, tuple(dupes)


def audit(rows, blocks: tuple[str, ...], *, malformed: int = 0,
          duplicates: tuple[str, ...] = (), disputed: int = 0) -> GridAudit:
    """Measure the read against the rectangle it claims to be."""
    read = holes = absent = 0
    for row in rows:
        for b in blocks:
            if b not in row.prices:
                absent += 1
            elif row.prices[b] == (None, None):
                holes += 1
            else:
                read += 1
    return GridAudit(
        rows=len(rows), blocks=blocks, read=read, holes=holes, absent=absent,
        malformed=malformed, duplicates=duplicates, disputed=disputed,
    )


def priced_blocks(rows, blocks: tuple[str, ...]) -> set[str]:
    """Consensus columns with at least one readable price on the sheet.

    A block every row reported and none could read is on the sheet but carries
    nothing — creating a coupon from it would produce a list of fixtures with no
    odds, which reads as a coupon that priced nothing rather than a photo that
    failed. It stays in the audit and out of the coupon list.
    """
    out = set()
    for row in rows:
        for b, pair in row.prices.items():
            if b in blocks and pair != (None, None):
                out.add(b)
    return out
