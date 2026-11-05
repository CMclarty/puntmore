from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MatchQuery:
    match_id: int
    match_name: str
    market: str
    # The kickoff PRINTED on the coupon (naive, GMT-as-UTC), never the board's.
    # It is the independent fact that tells a senior fixture from the same two
    # sides' youth game when the names alone cannot (`fixture_checks`).
    kickoff: datetime | None = None


@dataclass
class OddsResult:
    match_id: int
    fair_odds: float | None
    # The fixture name the provider fuzzy-matched the query to (None if no
    # match). Surfaced so a wrong match is visible instead of silently
    # producing a wrong fair price.
    matched_fixture: str | None = None
    # Similarity score behind that match (0–1). Feeds the low-confidence leg
    # flag, so a loose match is badged in the UI rather than only logged.
    match_score: float | None = None
    # Fixture kickoff (UTC). Drives the tracker's KO countdown, and comes from
    # the matched fixture rather than the coupon's match_day, which is only a
    # date and cannot order legs within a day.
    kickoff: datetime | None = None
    # BOTH sides of a team-specific market, always. Value can be on either, so
    # neither is assumed and both are carried through for comparison.
    # `side` is which one the pick names: True home, False away, None
    # unresolved (in which case fair_odds is None rather than a guess).
    fair_odds_home: float | None = None
    fair_odds_away: float | None = None
    side: bool | None = None


class OddsProvider(ABC):
    @abstractmethod
    def get_fair_odds(self, queries: list[MatchQuery]) -> list[OddsResult]:
        ...


@dataclass
class OcrHint:
    """What the coupon being photographed is, so OCR reads the right thing.

    A priced coupon prints odds beside each fixture; `board_label` names WHICH
    market's columns to read, because one sheet often prints several side by
    side (e.g. team-to-score 1+, 2+ and 3+). `two_sided` says that market has a
    separate Home and an Away price per fixture — both are read, one leg each.
    A ladder-only coupon prints no per-fixture price and needs no hint.
    """
    priced: bool = False
    two_sided: bool = False
    board_label: str | None = None
    # A BUNDLE sheet is a third shape, and not a rectangle at all: instead of
    # one fixture list under several market columns, it prints a handful of
    # ready-made accumulators, each a small group of selections with ONE price
    # for the whole group. The grid rules do not apply to it — there are no
    # blocks to key by heading, and the same fixture may legitimately appear in
    # more than one bundle.
    bundles: bool = False
    # A STRIP is a horizontal piece cut from a sheet that has already been read
    # whole (`providers/verified_ocr.py`). The column headings are usually not
    # in the picture, so they are supplied here in print order, left to right,
    # for the model to key each row's pairs by. Rows the cut leaves half-visible
    # are to be left out, not guessed at.
    strip: bool = False
    headings: tuple[str, ...] = ()


@dataclass
class ExtractedRow:
    match: str
    # None when the coupon prints no per-fixture selection. The market is known
    # from WHICH coupon was photographed, so OCR is not asked to guess it.
    market: str | None = None
    # None on list coupons (GG Bonus / GG No Draw), which print no price per
    # fixture and pay off a ladder instead.
    your_odds: float | None = None
    # Kickoff exactly as printed, e.g. "MON 17:00". Resolved to a date against
    # the coupon's window, since the coupon only prints a weekday.
    kickoff_text: str | None = None
    # Two-sided priced coupons print a price for the home team AND the away team
    # under one market heading — each becomes its own priced leg. None on any
    # coupon that prints a single price (your_odds) or none at all.
    #
    # POSITIONAL, on every coupon: home_odds is the LEFT price of the pair and
    # away_odds the RIGHT one, matching the fixture's own order (the team named
    # first is at home). OCR does NOT decide this — it reports the pair in
    # printed order and `claude_ocr._sides_from` assigns the sides, because a
    # model asked which team a price belonged to is a model that can answer
    # wrong. `_build_legs` pairs these with `normalise.split_sides`, which
    # splits the fixture left-to-right the same way.
    home_odds: float | None = None
    away_odds: float | None = None
    # Did the pair above arrive POSITIONALLY (a ["left", "right"] list, sides
    # assigned by us) or LABELLED by the model ("home_odds"/"away_odds")?
    #
    # It matters only on a two-sided coupon, where it decides whether the pair
    # is usable at all: a model asked which team a price belongs to is a model
    # that can answer wrong, and it did — that is how prices ended up crossed
    # onto the wrong team. Labelled sides are refused there and the leg goes
    # unpriced. True by default because a row built directly (a single-block
    # read, MockOCRProvider, a test) is stating the printed pair, not a
    # judgement about it; only the parser's labelled fallback sets it False.
    sides_positional: bool = True
    # EVERY market block printed on this row, keyed by the block's heading as
    # printed, each value a (left, right) pair. A sheet prints six look-alike
    # blocks side by side; transcribing all of them means the software picks the
    # one it wants by matching the heading, instead of the model being told to
    # find one block and ignore the rest. Empty when the coupon prints a single
    # price per fixture, or when the response carried only one pair.
    prices: dict[str, tuple[float | None, float | None]] = field(
        default_factory=dict
    )
    # Which ready-made accumulator this selection belongs to, as printed
    # ("Bundle 1", "Acca 3"), and that accumulator's single printed price.
    #
    # Both are repeated on EVERY row of the bundle rather than reported once
    # per group. Same reasoning as the grid's "every row gets every block": a
    # value carried on one row alone is lost outright if that row is the one
    # the read fumbles, and a repeated value can be checked for agreement.
    # `bundle` is the grouping key; `bundle_odds` is what the group is BET at,
    # so a coupon's value hangs on it the way a ladder coupon's hangs on a rung.
    bundle: str | None = None
    bundle_odds: float | None = None


@dataclass
class ExtractedCoupon:
    """A whole coupon: its fixtures plus the metadata printed on it.

    Coupons carry more than rows — the payout ladder and date range are printed
    and vary between issues, so they are read from the image rather than
    configured and assumed.
    """
    rows: list[ExtractedRow]
    title: str | None = None
    period_text: str | None = None
    # picks -> decimal return, e.g. {3: 5.5, 4: 10.0}. Empty when the coupon
    # prices each fixture individually instead.
    payout_ladder: dict[int, float] | None = None
    # Price pairs that came back in a shape that isn't a pair. Carried up rather
    # than dropped at the parser: a sheet's grid does not vary, so a malformed
    # cell is the READ going wrong, and the coupon's scan audit has to see it
    # (coupon_grid.py). Zero on a clean transcription.
    malformed_pairs: int = 0
    # Price cells the independent reads could not agree on, left unpriced.
    # Zero on a coupon that was read once (nothing to disagree with) — see
    # `verified` for whether that is the case.
    disputed_cells: int = 0
    # Did every price here survive a second, independent read? False for a
    # single-pass read: a ladder coupon (no prices to check), a bundle sheet
    # (not a grid), or a provider used bare. When True, every price present
    # was reported identically by at least two separate transcriptions.
    verified: bool = False
    reads: int = 1


class OCRProvider(ABC):
    @abstractmethod
    def extract(
        self, image_bytes: bytes, media_type: str, hint: OcrHint | None = None
    ) -> ExtractedCoupon:
        ...
