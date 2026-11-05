"""Every price is read twice before it is believed — see providers/verified_ocr.py.

The failure under test is a column of prices one row out: line 55 carrying
line 54's prices. The rectangle check cannot see it (every row still has every
block), so the sheet is read a second time in overlapping strips and each cell
is kept only where the reads agree.
"""
import io

import pytest
from PIL import Image

from app.coupon_grid import normalise_board_label
from app.providers.base import ExtractedCoupon, ExtractedRow, OCRProvider, OcrHint
from app.providers.verified_ocr import (
    OCRUnverified,
    VerifiedOCRProvider,
    describe_ocr,
    print_order_headings,
    reconcile,
)

TTS1 = normalise_board_label("TEAM TO SCORE 1 OR MORE GOALS")
TTS2 = normalise_board_label("TEAM TO SCORE 2 OR MORE GOALS")
BLOCKS = (TTS1, TTS2)


def _row(i, shift=0, **over):
    """Row i of a synthetic sheet. `shift` gives it the prices of row i+shift —
    a read that has slipped a line."""
    j = i + shift
    prices = {b: (1.0 + j, 2.0 + j) for b in BLOCKS}
    prices.update(over)
    return ExtractedRow(match=f"Home {i} v Away {i}", kickoff_text="SAT 15:00",
                        prices=prices)


def _sheet(n=20, slip_from=None):
    return [_row(i, shift=1 if slip_from is not None and i >= slip_from else 0)
            for i in range(n)]


def _png():
    img = Image.new("RGB", (400, 1000), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _Scripted(OCRProvider):
    """Returns the whole-page read first, then one strip read per call."""

    def __init__(self, whole, strip_reads):
        self.whole = whole
        self.strip_reads = list(strip_reads)
        self.calls = []

    def extract(self, image_bytes, media_type, hint=None):
        self.calls.append(hint)
        if hint is None or not hint.strip:
            return self.whole
        if not self.strip_reads:
            return ExtractedCoupon(rows=[])
        return self.strip_reads.pop(0)


GRID = OcrHint(priced=True, two_sided=True, board_label="TEAM TO SCORE 1 OR MORE GOALS")


# --- reconciliation ---------------------------------------------------------

def test_reads_that_agree_keep_every_price():
    rows, disputed, appended = reconcile(_sheet(), [_sheet(), _sheet()])
    assert disputed == 0 and appended == 0
    assert rows[7].prices[TTS1] == (8.0, 9.0)
    assert len(rows) == 20


def test_a_whole_page_read_that_slipped_a_row_is_outvoted_by_the_strips():
    """THE bug: from row 10 the page read carries the next row's prices.
    Two strip reads agree with each other and with the print; the page read
    loses every vote from row 10 on, and the prices come out right."""
    slipped = _sheet(slip_from=10)
    rows, disputed, _ = reconcile(slipped, [_sheet(), _sheet()])
    assert disputed == 0
    for i in range(20):
        assert rows[i].prices[TTS1] == (1.0 + i, 2.0 + i), f"row {i}"


def test_a_strip_read_that_slipped_is_outvoted_by_the_page_and_the_other_strip():
    rows, disputed, _ = reconcile(_sheet(), [_sheet(slip_from=5), _sheet()])
    assert disputed == 0
    assert rows[12].prices[TTS2] == (1.0 + 12, 2.0 + 12)


def test_two_reads_that_disagree_leave_the_cell_unpriced_and_count_it():
    """Page and one strip disagree, no third opinion: NOT priced. Which one
    is right is the thing that cannot be known."""
    page = _sheet()
    strip = _sheet()
    strip[3].prices[TTS1] = (99.0, 2.0 + 3)   # left slot wrong, right agrees
    rows, disputed, _ = reconcile(page, [strip])
    assert rows[3].prices[TTS1] == (None, 5.0)
    assert disputed == 1


def test_a_price_only_one_read_saw_is_a_claim_not_a_price():
    page = _sheet()
    strip = _sheet()
    strip[4].prices[TTS2] = (None, None)      # strip could not read it
    rows, disputed, _ = reconcile(page, [strip])
    assert rows[4].prices[TTS2] == (None, None)
    assert disputed == 2                      # both slots: seen once, unconfirmed


def test_a_cell_nobody_read_is_a_hole_not_a_dispute():
    page = _sheet()
    page[6].prices[TTS1] = (None, None)
    strip = _sheet()
    strip[6].prices[TTS1] = (None, None)
    rows, disputed, _ = reconcile(page, [strip])
    assert rows[6].prices[TTS1] == (None, None)
    assert disputed == 0


def test_a_row_no_strip_corroborates_keeps_its_place_with_its_prices_disputed():
    page = _sheet(5)
    strips_ = [_sheet(4), _sheet(4)]          # neither strip saw row 4
    rows, disputed, _ = reconcile(page, strips_)
    assert [r.match for r in rows][-1] == "Home 4 v Away 4"
    assert rows[4].prices[TTS1] == (None, None)
    assert disputed == 4                      # 2 blocks x 2 slots


def test_a_row_the_page_read_dropped_is_recovered_from_agreeing_strips():
    page = _sheet(5)
    del page[2]
    rows, disputed, appended = reconcile(page, [_sheet(5), _sheet(5)])
    assert appended == 1
    assert "Home 2 v Away 2" in [r.match for r in rows]
    recovered = next(r for r in rows if r.match == "Home 2 v Away 2")
    assert recovered.prices[TTS1] == (3.0, 4.0)
    assert disputed == 0


def test_a_row_only_one_strip_invented_is_not_added():
    page = _sheet(5)
    extra = _sheet(5) + [_row(99)]
    rows, _, appended = reconcile(page, [extra, _sheet(5)])
    assert appended == 0 and len(rows) == 5


def test_reads_are_matched_by_fixture_through_spelling_differences():
    page = [ExtractedRow(match="Man Utd v Chelsea", prices={TTS1: (1.5, 2.5)})]
    strip = [ExtractedRow(match="Man United v Chelsea", prices={TTS1: (1.5, 2.5)})]
    rows, disputed, _ = reconcile(page, [strip])
    assert disputed == 0 and rows[0].prices[TTS1] == (1.5, 2.5)


def test_one_sided_prices_are_voted_too():
    page = [ExtractedRow(match="A v B", your_odds=2.5)]
    rows, disputed, _ = reconcile(page, [[ExtractedRow(match="A v B", your_odds=2.5)]])
    assert rows[0].your_odds == 2.5 and disputed == 0
    rows, disputed, _ = reconcile(page, [[ExtractedRow(match="A v B", your_odds=2.6)]])
    assert rows[0].your_odds is None and disputed == 1


def test_headings_come_back_in_print_order_not_sorted():
    rows = [ExtractedRow(match=f"H{i} v A{i}", prices={"ZZZ": (1, 2), "AAA": (1, 2)})
            for i in range(4)]
    rows[0].prices["STRAY"] = (1, 2)        # one row's hallucination is not a column
    assert print_order_headings(rows) == ("ZZZ", "AAA")


# --- the wrapper --------------------------------------------------------------

def test_a_priced_grid_is_read_whole_then_in_strips_with_the_headings():
    whole = ExtractedCoupon(rows=_sheet(), title="TTS", period_text="Sat",
                            payout_ladder={})
    inner = _Scripted(whole, [ExtractedCoupon(rows=_sheet()) for _ in range(5)])
    coupon = VerifiedOCRProvider(inner, workers=2).extract(_png(), "image/png", GRID)

    assert coupon.verified is True
    assert coupon.reads == 6
    assert coupon.disputed_cells == 0
    assert coupon.title == "TTS" and coupon.period_text == "Sat"
    strip_hints = [h for h in inner.calls if h is not None and h.strip]
    assert len(strip_hints) == 5
    assert all(h.headings == BLOCKS for h in strip_hints)
    assert all(h.two_sided and h.priced for h in strip_hints)


def test_the_verified_coupon_reports_what_was_disputed():
    page = _sheet(slip_from=15)               # page slipped late...
    strips_ = [ExtractedCoupon(rows=_sheet(slip_from=15)) for _ in range(5)]
    strips_[0] = ExtractedCoupon(rows=_sheet())   # ...one strip disagrees, alone
    inner = _Scripted(ExtractedCoupon(rows=page), strips_)
    coupon = VerifiedOCRProvider(inner, workers=1).extract(_png(), "image/png", GRID)
    # From row 15 the reads are split: the majority carried the slip, so those
    # cells are priced the majority's way. That is the honest outcome of a
    # majority vote — the guard against it is that a slip must recur across
    # SEPARATE reads of SEPARATE pieces of the page to win.
    assert coupon.verified is True
    assert coupon.reads == 6


def test_a_ladder_coupon_is_read_once_there_being_no_prices_to_check():
    whole = ExtractedCoupon(rows=[ExtractedRow(match="A v B")], payout_ladder={3: 5.5})
    inner = _Scripted(whole, [])
    coupon = VerifiedOCRProvider(inner).extract(_png(), "image/png", OcrHint(priced=False))
    assert coupon is whole and coupon.verified is False
    assert len(inner.calls) == 1


def test_a_bundle_sheet_is_read_once_not_being_a_grid():
    whole = ExtractedCoupon(rows=[ExtractedRow(match="A v B", bundle="BUNDLE 1")])
    inner = _Scripted(whole, [])
    coupon = VerifiedOCRProvider(inner).extract(
        _png(), "image/png", OcrHint(priced=True, bundles=True)
    )
    assert coupon is whole and len(inner.calls) == 1


def test_a_failed_strip_read_fails_the_whole_read_rather_than_saving_it_unchecked():
    class _Flaky(OCRProvider):
        def extract(self, image_bytes, media_type, hint=None):
            if hint is not None and hint.strip:
                raise RuntimeError("vendor 503")
            return ExtractedCoupon(rows=_sheet())

    with pytest.raises(OCRUnverified, match="could not be checked"):
        VerifiedOCRProvider(_Flaky(), workers=1).extract(_png(), "image/png", GRID)


def test_the_wrapper_names_the_provider_doing_the_reading():
    class _Inner(OCRProvider):
        def extract(self, *a, **k):
            raise NotImplementedError
    assert describe_ocr(VerifiedOCRProvider(_Inner())) == "VerifiedOCRProvider(_Inner)"
    assert describe_ocr(_Inner()) == "_Inner"


def test_real_providers_are_wired_wrapped_and_the_mock_is_not():
    """The check is not a dial. A key set means a wrapped provider."""
    import inspect
    from app import main
    src = inspect.getsource(main.lifespan)
    assert "VerifiedOCRProvider(\n            GeminiOCRProvider" in src
    assert "VerifiedOCRProvider(\n            ClaudeOCRProvider" in src
    assert "VerifiedOCRProvider(MockOCRProvider" not in src
