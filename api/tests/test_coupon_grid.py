"""The sheet as a rectangle — see app/coupon_grid.py.

A priced coupon is R fixtures x B market blocks. The count is a property of the
PRINT, so it holds whether or not every cell could be read, and these tests pin
the distinction that used to be lost: a cell nobody could read is a hole in a
solved grid; a block missing from a row is the read losing structure the sheet
has.
"""
from app.coupon_grid import (
    GridAudit,
    audit,
    consensus_blocks,
    drop_duplicate_fixtures,
    headings_match,
    normalise_board_label,
    priced_blocks,
    reconcile,
    resolve_heading,
)
from app.providers.base import ExtractedRow

TTS1 = normalise_board_label("TEAM TO SCORE 1 OR MORE GOALS")
TTS2 = normalise_board_label("TEAM TO SCORE 2 OR MORE GOALS")
WO15 = normalise_board_label("WIN & 2 OR MORE GOALS")


def _row(match, **prices):
    return ExtractedRow(match=match, prices=dict(prices))


def _sheet(n=10, blocks=(TTS1, TTS2, WO15)):
    return [
        _row(f"Home {i} v Away {i}", **{b: (1.5, 2.5) for b in blocks})
        for i in range(n)
    ]


# --- heading identity: the trap that makes plain fuzzy matching unusable -----

def test_a_mistranscribed_letter_still_matches_its_own_heading():
    assert headings_match(TTS1, normalise_board_label("TEAM TO SCORE 1 OR MOR GOALS"))
    assert headings_match(WO15, normalise_board_label("Win and 2 or more goals"))


def test_headings_differing_by_a_DIGIT_never_match():
    """The whole reason this isn't a similarity score.

    difflib rates these two at ~0.97 — above any threshold loose enough to
    survive an OCR typo. Matching on the digits exactly is what stops a block's
    prices being filed under its neighbour's market, which is the failure the
    entire positional pipeline exists to prevent.
    """
    import difflib
    assert difflib.SequenceMatcher(None, TTS1, TTS2).ratio() > 0.95
    assert not headings_match(TTS1, TTS2)
    assert not headings_match(
        normalise_board_label("WIN & 2 OR MORE GOALS"),
        normalise_board_label("WIN & 3 OR MORE GOALS"),
    )


def test_a_misread_digit_leaves_the_block_unmatched_rather_than_wrong():
    """Refusing beats guessing: an unread block costs a price, a mismatched one
    files the wrong market's odds against a team."""
    assert resolve_heading("TEAM TO SCORE 1 OR MORE GOALS", [TTS2, WO15]) is None


def test_resolve_heading_finds_the_column_through_a_typo():
    blocks = (normalise_board_label("TEAM TO SCORE 1 OR MOREE GOALS"), WO15)
    assert resolve_heading("TEAM TO SCORE 1 OR MORE GOALS", blocks) == blocks[0]


# --- the sheet's own column set ---------------------------------------------

def test_consensus_is_the_columns_most_rows_carry():
    rows = _sheet(10)
    rows[3].prices["STRAY HEADING FROM NOWHERE"] = (2.0, 3.0)
    assert set(consensus_blocks(rows)) == {TTS1, TTS2, WO15}


def test_a_stray_heading_is_folded_onto_the_column_it_misspells():
    rows = _sheet(10)
    typo = normalise_board_label("TEAM TO SCORE 1 OR MOR GOALS")
    del rows[4].prices[TTS1]
    rows[4].prices[typo] = (9.0, 9.5)

    blocks = consensus_blocks(rows)
    assert reconcile(rows, blocks) == 1
    assert rows[4].prices[TTS1] == (9.0, 9.5)
    assert typo not in rows[4].prices


def test_reconcile_never_overwrites_a_column_the_row_already_read():
    rows = _sheet(10)
    rows[2].prices[normalise_board_label("TEAM TO SCORE 1 OR MOR GOALS")] = (99.0, 99.0)
    reconcile(rows, consensus_blocks(rows))
    assert rows[2].prices[TTS1] == (1.5, 2.5)


# --- one line per match -----------------------------------------------------

def test_a_repeated_fixture_is_the_read_losing_its_place():
    rows = _sheet(4) + [_row("Home 1 v Away 1", **{TTS1: (7.0, 8.0)})]
    kept, dupes = drop_duplicate_fixtures(rows)
    assert len(kept) == 4
    assert dupes == ("Home 1 v Away 1",)
    # The FIRST occurrence survives — it is the one read off the row that exists.
    assert kept[1].prices[TTS1] == (1.5, 2.5)


# --- counting the rectangle -------------------------------------------------

def test_a_complete_sheet_accounts_for_every_cell():
    rows = _sheet(10)
    scan = audit(rows, consensus_blocks(rows))
    assert (scan.rows, len(scan.blocks), scan.cells) == (10, 3, 30)
    assert scan.read == 30
    assert (scan.holes, scan.absent, scan.malformed) == (0, 0, 0)
    assert scan.clean


def test_an_unreadable_cell_is_a_hole_and_the_grid_is_still_solved():
    rows = _sheet(10)
    rows[0].prices[TTS1] = (None, None)
    scan = audit(rows, consensus_blocks(rows))
    assert (scan.read, scan.holes, scan.absent) == (29, 1, 0)
    assert scan.read + scan.holes + scan.absent == scan.cells
    # One faint cell out of thirty is a photograph, not a failed read.
    assert scan.clean


def test_a_block_missing_from_a_row_is_counted_as_missing():
    rows = _sheet(10)
    del rows[0].prices[TTS1]
    del rows[1].prices[TTS2]
    scan = audit(rows, consensus_blocks(rows))
    assert scan.absent == 2
    assert not scan.clean
    assert "MISSING" in scan.summary()


def test_too_many_holes_fails_the_scan_even_though_none_are_missing():
    rows = _sheet(10)
    for r in rows[:5]:
        r.prices[TTS1] = (None, None)
        r.prices[TTS2] = (None, None)
    scan = audit(rows, consensus_blocks(rows))
    assert scan.absent == 0
    assert scan.coverage == 20 / 30
    assert not scan.clean


def test_malformed_pairs_and_duplicates_fail_the_scan():
    rows = _sheet(5)
    blocks = consensus_blocks(rows)
    assert not audit(rows, blocks, malformed=1).clean
    assert not audit(rows, blocks, duplicates=("A v B",)).clean


def test_a_block_no_row_could_read_is_not_a_market_the_sheet_offers():
    """It is ON the sheet — that's why it's in the audit — but a coupon built
    from it would be a list of fixtures with no odds."""
    rows = _sheet(10)
    for r in rows:
        r.prices[WO15] = (None, None)
    blocks = consensus_blocks(rows)
    assert WO15 in blocks
    assert priced_blocks(rows, blocks) == {TTS1, TTS2}


def test_an_empty_audit_is_clean_rather_than_dividing_by_zero():
    assert GridAudit().clean
    assert GridAudit().coverage == 1.0
    assert "no market grid" in GridAudit().summary()
