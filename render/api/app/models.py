import json

from sqlalchemy import (
    Boolean, Column, Integer, String, Float, DateTime, Date, ForeignKey, Text,
)
from sqlalchemy.orm import relationship
from .database import Base
from .time_utils import utcnow


class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=True)            # e.g. "Saturday Acca" (optional)
    type_code = Column(String, nullable=False)       # e.g. "TTS2", "WO25"
    # Coupons run on fixed windows (Mon–Wed / Wed–Fri / Sat / Sun), not single
    # days — see coupon_periods.py. match_day holds the window's FIRST fixture
    # date so existing ordering keeps working.
    period = Column(String, nullable=True)
    match_day = Column(Date, nullable=False)         # first fixture date of the period
    period_ends = Column(Date, nullable=True)        # last fixture date of the period
    # Payout ladder READ OFF THIS COUPON as JSON {"3": 5.5, ...}. Printed on
    # each issue and can change between them, so it is stored per coupon rather
    # than assumed from config.
    payout_ladder = Column(Text, nullable=True)
    # processing = OCR still running; confirmed = rows saved and priced. There
    # is no review state: OCR auto-confirms (SPEC §7).
    status = Column(String, nullable=False, default="processing")
    # How much of the printed GRID this scan actually recovered, as JSON — see
    # coupon_grid.GridAudit. A sheet is a rectangle of rows x market blocks, so
    # the cell count is knowable whether or not every cell could be read, and
    # this is where that arithmetic is kept. Null on coupons read before the
    # check existed, and on ladder coupons, which print no grid.
    scan_audit = Column(Text, nullable=True)
    image_path = Column(Text, nullable=True)         # OCR source image
    uploaded_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    legs = relationship(
        "Leg", back_populates="coupon",
        cascade="all, delete-orphan",
        order_by="Leg.rank",
    )

    @property
    def leg_count(self) -> int:
        return len(self.legs)

    @property
    def period_label(self) -> str | None:
        """Human label of this coupon's period, e.g. "Mon–Wed"."""
        from .coupon_periods import get as get_period
        if not self.period:
            return None
        p = get_period(self.period)
        return p.label if p else self.period

    @property
    def period_status(self) -> str | None:
        """Where today sits in this coupon's window: "active" (contains today),
        "upcoming" (not started), "past" (ended), or None when the window is
        unknown (a variable coupon not yet resolved from its fixtures).

        Windows OVERLAP by design — Mon–Wed and Wed–Fri both contain Wednesday —
        so more than one coupon of a type can be active at once.
        """
        from datetime import date
        if self.match_day is None:
            return None
        start = self.match_day
        end = self.period_ends or start
        today = date.today()
        if today < start:
            return "upcoming"
        if today > end:
            return "past"
        return "active"

    @property
    def ladder(self) -> dict[int, float]:
        """Payout ladder as {picks: decimal}. Empty when the coupon prices rows.

        Sanitised against the type's printed board on the way OUT as well as on
        the way in (upload.py). Reading it here too is what repairs a coupon
        already stored with a bad rung — the scan that produced it isn't going
        to run again on its own, and until it does, the combination figure on
        screen is the one thing the coupon is bet on.
        """
        if not self.payout_ladder:
            return {}
        from . import coupon_types  # local: coupon_types has no model imports
        try:
            raw = {int(k): float(v) for k, v in json.loads(self.payout_ladder).items()}
        except (ValueError, TypeError, AttributeError):
            return {}
        ctype = coupon_types.get(self.type_code)
        clean, _notes = coupon_types.sanitise_ladder(
            raw, ctype.default_ladder if ctype else {}
        )
        return clean


class Leg(Base):
    __tablename__ = "legs"

    id = Column(Integer, primary_key=True)
    coupon_id = Column(Integer, ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False)
    match_name = Column(String, nullable=False)
    pick = Column(String, nullable=False)            # selection e.g. "PSG HT/FT"
    # Nullable: GG Bonus and GG No Draw coupons print NO per-selection price —
    # any 3 selections pay a flat 5.5 — so those rows genuinely have no odds.
    odds = Column(Float, nullable=True)
    fair_odds = Column(Float, nullable=True)
    value = Column(Float, nullable=True)
    fair_odds_updated_at = Column(DateTime, nullable=True)
    external_event_id = Column(Integer, nullable=True)
    rank = Column(Integer, nullable=False, default=0)
    matched_fixture = Column(String, nullable=True)  # fixture the fuzzy match linked
    match_score = Column(Float, nullable=True)       # similarity behind that match
    kickoff = Column(DateTime, nullable=True)        # fixture start (UTC), drives KO countdown
    # The kickoff as PRINTED on the coupon. Kept apart from `kickoff`, which
    # pricing overwrites with the matched board fixture's time — so it cannot
    # be what checks the match, or a wrong match would vouch for itself.
    printed_kickoff = Column(DateTime, nullable=True)
    # Both sides of the market, so value on the opposite side stays visible.
    fair_odds_home = Column(Float, nullable=True)
    fair_odds_away = Column(Float, nullable=True)
    side = Column(String, nullable=True)             # 'home' | 'away' | None (unresolved)
    # BUNDLE coupons (Fred Bundles): which ready-made acca this selection is
    # part of, as printed, and that acca's single printed price. Null on every
    # other type. The bundle is the BET — value belongs to the group, not to
    # the row — so `odds` stays null on these legs and `bundle_odds` carries
    # the number the group is struck at (coupon_types.bundles_on).
    bundle = Column(String, nullable=True)
    bundle_odds = Column(Float, nullable=True)
    notified_tier = Column(String, nullable=True)    # last value tier sent to Discord (dedupe)
    # True once a human has corrected this row. A re-scan must never overwrite
    # an edited row with a fresh machine read.
    # server_default is REQUIRED, not cosmetic: the ensure_schema legs rebuild
    # recreates this table from the model and copies old rows with an INSERT
    # that omits `edited`. Without a SQL DEFAULT the NOT NULL column rejects
    # those rows (the Python-side default never reaches a raw INSERT ... SELECT).
    edited = Column(Boolean, nullable=False, default=False, server_default="0")

    coupon = relationship("Coupon", back_populates="legs")

    @property
    def period_label(self) -> str | None:
        """Human label of this selection's coupon period, e.g. "Mon–Wed"."""
        return self.coupon.period_label if self.coupon else None

    @property
    def period_status(self) -> str | None:
        """Where today sits in this selection's coupon window — see
        Coupon.period_status, which is the single definition of "active"."""
        return self.coupon.period_status if self.coupon else None


class GoalscorerAlert(Base):
    """A first-goalscorer value row found by the scheduler (bet365 > fair).

    Written by the pre-kickoff / in-play scan and read by GET /goalscorers/feed.
    Rows for an event are replaced on each scan, so the table always reflects
    the latest picture rather than accumulating history.
    """
    __tablename__ = "goalscorer_alerts"

    id = Column(Integer, primary_key=True)
    event_id = Column(String, nullable=False, index=True)
    match_name = Column(String, nullable=False)
    commence_time = Column(DateTime, nullable=True)   # fixture kickoff (UTC)
    player = Column(String, nullable=False)            # bet365's spelling
    matched_player = Column(String, nullable=True)     # Pinnacle's spelling paired to
    match_score = Column(Float, nullable=True)         # name-match confidence [0,1]
    bet365_price = Column(Float, nullable=False)
    pinnacle_price = Column(Float, nullable=False)
    fair_price = Column(Float, nullable=False)         # de-vigged Pinnacle
    edge = Column(Float, nullable=False)               # bet365/fair - 1
    inplay = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)


class Bet(Base):
    """A single settled bet's net result in the private P&L tracker.

    Owned by the user who logged it (the tracker is a per-user ledger, gated to
    the "pnl" tool). amount is the net change: positive = win, negative = loss.
    Cumulative total, month-to-date and the chart are all derived from these.

    A bet can be logged two ways: the flat net result on its own, or a
    stake/return pair when there's time to enter both. In the second case
    amount is still stored (= returned − stake) so every derived figure reads
    one field regardless of entry mode; stake/returned are extra detail and are
    NULL on flat entries. Anything needing them (turnover, ROI) must skip rows
    where they are NULL rather than treat a missing stake as zero.
    """
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True)
    user = Column(String, nullable=False, index=True)
    # Which ledger this bet belongs to: "dogs" (greyhounds) or "virtuals"
    # (virtual racing). Two independent P&L instances share one table, split by
    # this column — every query is scoped to (user, category). server_default
    # backfills rows written before the split (they land in the dogs ledger).
    category = Column(
        String, nullable=False, default="dogs", server_default="dogs", index=True
    )
    amount = Column(Float, nullable=False)          # net change, + win / − loss
    stake = Column(Float)                           # NULL on flat entries
    returned = Column(Float)                        # NULL on flat entries
    # Price taken vs the Betfair starting price, as decimal odds. Independent of
    # the money columns and of each other: you know the price you took at bet
    # time but the BFSP only after the off, so either can be recorded alone.
    # The price-vs-BFSP series uses only rows where BOTH are present.
    price_taken = Column(Float)
    bfsp = Column(Float)
    # Each-way: two bets on one line, so `stake` holds the TOTAL outlay (2 ×
    # unit) and `returned` the combined return. place_fraction is the place
    # terms the return was computed at (0.25 = 1/4 odds) and is NULL unless
    # each_way — without it a stored EW bet can't be reconstructed.
    each_way = Column(Boolean, nullable=False, default=False, server_default="0")
    place_fraction = Column(Float)
    # A staff tip (virtuals): real money out of the till, so it moves the
    # cumulative total like any loss, but it is NOT a bet — it never counts
    # toward the win rate, the bet count, turnover or EV. amount is negative and
    # stake/price columns stay NULL. server_default backfills existing rows as
    # ordinary bets (0), which is what every row before this column was.
    is_tip = Column(Boolean, nullable=False, default=False, server_default="0")
    # Expected profit in POUNDS at the moment the bet was struck, from the
    # virtuals boost model (frontend boost.js: the card's own price, overround
    # stripped, places by Harville) on the outlay actually staked. Virtuals has
    # no Betfair SP to measure against, so this is its only EV. NULL on every
    # bet without a boost entered — an unboosted bet is not measured, the same
    # way a dogs bet with no BFSP is not.
    ev = Column(Float)
    created_at = Column(DateTime, default=utcnow, index=True)


class ShopVisit(Base):
    """One logged stop on the field route, owned by the user who tapped it.

    Deliberately one row per visit rather than a counter column: the count is
    `COUNT(*)`, and keeping the timestamps means later questions ("how many
    yesterday?", "which weeks were slow?") need no migration, only a query. The
    row carries nothing else — a visit is the event, and the money figures on
    the page are all `count × a constant` (see routers/shop.py).
    """
    __tablename__ = "shop_visits"

    id = Column(Integer, primary_key=True)
    user = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # Tool access. "*" = every tool, present and future (admin). Otherwise the
    # granted tool names joined by "+" (e.g. "coupons+accabonus"). Seeing a tool
    # means full use of it — there is no view-only tier. Default "*" so any user
    # created without an explicit grant keeps the pre-split behaviour.
    permissions = Column(String, nullable=False, default="*")
    created_at = Column(DateTime, default=utcnow)


class ApiClient(Base):
    """A machine caller (a script, the Bet Placer) with its own key.

    The key itself is NEVER stored: only its sha256, so a copy of the database
    is not a copy of the keys. It is shown once, by `python -m app.apikeys
    create`, and cannot be recovered afterwards — lose it and create another.
    `permissions` uses the User grammar ("*" or tools joined by "+"), and is
    resolved through the same `security.user_permissions` seam, so every
    `require_tool` guard admits a client exactly as it would a user with that
    grant. Revoking sets `enabled` False; the row stays as the record.
    """
    __tablename__ = "api_clients"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    key_hash = Column(String, unique=True, nullable=False, index=True)
    permissions = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=utcnow)
    last_used_at = Column(DateTime, nullable=True)


class LadderShadow(Base):
    """One measured prediction error of the counts model (pricing/counts/shadow.py).

    A Pinnacle rung hidden from the fit and predicted from the rest — inside
    the ladder or from beyond its end — against Pinnacle's own de-vigged price.
    Collected by the Paddy sweep to MEASURE the beyond-ladder penalty instead
    of assuming it. Pruned to the last 30 days (cards/shadow_store.py).
    """
    __tablename__ = "ladder_shadow"

    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=utcnow, index=True)
    market = Column(String, nullable=False)          # corners | cards | goals
    fixture_id = Column(String, nullable=False)
    line = Column(Float, nullable=False)
    beyond = Column(Float, nullable=False)           # counts outside the fit's rungs
    p_pred = Column(Float, nullable=False)           # model p(over), rung hidden
    p_actual = Column(Float, nullable=False)         # Pinnacle's de-vigged p(over)


class AwSlip(Base):
    """One bet Callum placed at the Paddy terminal, on his AW 26 ledger (aw26/).

    Recorded by HubAI or corrected on the page; every field is editable
    afterwards. Slips recorded before AW 26's retirement (29 Oct 2026) may
    carry the photo they were read from (`image_path`). `user` records who recorded it.

    EV is fixed once EVERY leg has its odds and a fair price, and is not
    recomputed after (only an edit clears and recomputes it): it is what the
    bet was worth when it was struck. Until then it is NULL and `ev_note`
    says why.
    """
    __tablename__ = "aw_slips"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    # When the bet was struck: the terminal's own time if the record has one,
    # else its arrival. The EV-per-day target counts slips by this.
    placed_at = Column(DateTime, nullable=False, index=True)
    user = Column(String, nullable=False)
    image_path = Column(String, nullable=True)
    # The terminal's bet reference, as printed. Unique where present, so the
    # same slip sent twice is refused rather than counted twice.
    receipt = Column(String, nullable=True, unique=True)
    offer = Column(String, nullable=False, default="refund")   # refund | boost | other | free
    stake = Column(Float, nullable=True)            # NULL if the read missed it — fill in
    # Printed potential returns, kept for reference against our settlement.
    potential_returns = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="open")    # open | won | lost | refunded | void
    returns = Column(Float, nullable=True)                     # NULL while open
    settled_at = Column(DateTime, nullable=True)
    ev = Column(Float, nullable=True)                          # pounds
    ev_note = Column(String, nullable=True)                    # why EV is NULL / off-terms
    # Set once a person changes a field (PATCH /aw26/slips/{id}).
    edited = Column(Boolean, nullable=False, default=False)

    legs = relationship("AwLeg", back_populates="slip", cascade="all, delete-orphan",
                        order_by="AwLeg.position")

    @property
    def has_image(self) -> bool:
        return bool(self.image_path)


class AwLeg(Base):
    """One selection on an AW 26 slip, as printed, plus what it was worth."""
    __tablename__ = "aw_legs"

    id = Column(Integer, primary_key=True)
    slip_id = Column(Integer, ForeignKey("aw_slips.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    event = Column(String, nullable=False)          # "Arsenal v Chelsea", as printed
    market = Column(String, nullable=True)          # "Total Match Corners", as printed
    selection = Column(String, nullable=True)       # "Over 9.5", as printed
    odds = Column(Float, nullable=True)             # decimal, the price taken; NULL = unread
    kickoff = Column(DateTime, nullable=True)
    # corners | cards | goals | saves | other, the line, and over | under (None
    # outside totals): read off the printed words, on slips recorded before
    # the retirement (29 Oct 2026) and by HubAI on new ones, NULL where it
    # read none. The page prints a totals leg by them.
    kind = Column(String, nullable=True)
    line = Column(Float, nullable=True)
    side = Column(String, nullable=True)
    fair_odds = Column(Float, nullable=True)
    # The event's id: OddsPapi's on slips from before the retirement, the one
    # HubAI sends on new ones; NULL where there is none.
    fixture_id = Column(String, nullable=True)
    result = Column(String, nullable=False, default="pending")  # pending | won | lost | void
    # True = no fair was found, so the fair is taken as the odds (the leg adds
    # no value of its own). Added 9 Nov 2026 by ensure_schema: the last column,
    # and server_default backfills every earlier leg as not assumed.
    fair_assumed = Column(Boolean, nullable=False, default=False, server_default="0")

    slip = relationship("AwSlip", back_populates="legs")


class AwPhoto(Base):
    """A slip photo waiting for Qwen on the Hub (aw26/photos.py). Render stores
    it and tracks its state; it never reads one.

    waiting -> reading (taken, `tries` + 1) -> back to waiting on a failure
    while tries are left, else failed; duplicate when its receipt is already
    in; done once its slip is recorded, which then owns the file (`slip_id`,
    the slip's `image_path` is this one's)."""
    __tablename__ = "aw_photos"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow)
    user = Column(String, nullable=False)
    image_path = Column(String, nullable=False)
    source = Column(String, nullable=False)          # page | telegram
    # waiting | reading | failed | duplicate | done
    state = Column(String, nullable=False, default="waiting", index=True)
    taken_at = Column(DateTime, nullable=True)       # the last take
    tries = Column(Integer, nullable=False, default=0)
    reason = Column(String, nullable=True)           # why it last failed / is a duplicate
    slip_id = Column(Integer, ForeignKey("aw_slips.id", ondelete="SET NULL"), nullable=True)


# AW 26's other tables (aw_fairs, aw_leg_lines, value_snapshots, value_lines)
# are left in the database, unread: AW 26 is a page over the slips, and the
# work that filled those tables is retired. aw_leg_lines
# rows go with their leg (ON DELETE CASCADE; foreign keys are on, database.py).


# The scraper control plane (scraper_leagues, scraper_markets,
# scraper_timings, scraper_target_status, scraper_site_status) was removed on
# 5 Nov 2026: the Scraper reads targets.toml on the Hub. Its tables are left in
# the database untouched until someone drops them.


class HillsTarget(Base):
    """Hills' target match: the ONE fixture William Hill has boosted bet
    builders on, set by anyone on the team and seen by everyone. A single row
    (id 1); cleared, or simply ignored once it has kicked off."""
    __tablename__ = "hills_target"

    id = Column(Integer, primary_key=True)
    fixture_id = Column(String, nullable=False)          # OddsPapi fixture id
    home = Column(String, nullable=False)
    away = Column(String, nullable=False)
    kickoff = Column(DateTime, nullable=False)           # UTC, naive like the rest
    competition = Column(String, nullable=False)
    boost_pct = Column(Float, nullable=False)            # on winnings: 25 = +25%
    set_by = Column(String, nullable=False)
    set_at = Column(DateTime, nullable=False, default=utcnow)


class HillsBet(Base):
    """A boosted bet builder someone on the team has placed with William Hill
    on a target match: what it was and how it went. Nothing is priced or kept
    about fairs — it is a log of bets made."""
    __tablename__ = "hills_bets"

    id = Column(Integer, primary_key=True)
    fixture_id = Column(String, nullable=False, index=True)
    match = Column(String, nullable=False)                # "Arsenal vs Chelsea", as targeted
    kickoff = Column(DateTime, nullable=False)
    legs = Column(String, nullable=False)                 # the builder as read: leg labels + note
    # The legs picked off William Hill's lines, by OddsPapi id, each with its
    # result once settled: [{market_id, outcome_id, label, result}] as JSON.
    legs_json = Column(Text, nullable=False, default="[]")
    note = Column(String, nullable=True)                  # a leg not on the list (settled by hand)
    result = Column(String, nullable=False, default="open")   # open | won | lost
    settled_by = Column(String, nullable=True)            # "oddspapi" or the user who set it
    placed_by = Column(String, nullable=False)
    placed_at = Column(DateTime, nullable=False, default=utcnow)


class FinderRow(Base):
    """One row on Finder, Callum's tips board (docs/finder-spec.md,
    app/finder/rows.py): what HubAI posted from the Hub, and where it is in
    the status machine. Owner-only; kept 30 days from `created_at`.

    `odds` and `fair` are the prices the TIP quotes (nothing here looks one
    up); `edge` = odds / fair - 1, null with no fair. An `offered` row always
    has `kickoff`, `odds` and `fair`; an `asked` (or `rejected`) row may lack
    all three. Times are naive UTC
    like the rest of this file."""
    __tablename__ = "finder_rows"

    tip_id = Column(String, primary_key=True)          # minted once on the Hub (ids.py)
    origin = Column(String, nullable=False)            # the tipster or page
    sport = Column(String, nullable=False)             # football | horse
    bookmaker = Column(String, nullable=False)         # as the tip names it
    event = Column(String, nullable=False)             # "Home v Away" or "Course HH:MM"
    home = Column(String, nullable=True)
    away = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    kickoff = Column(DateTime, nullable=True)          # kick-off or the off; null on an asked row HubAI cannot place on a day
    market = Column(String, nullable=False)            # as the tip names it; win | each_way for a horse
    selection = Column(String, nullable=False)
    line = Column(Float, nullable=True)
    side = Column(String, nullable=True)
    odds = Column(Float, nullable=True)                # null on an asked row whose tip quotes none
    fair = Column(Float, nullable=True)
    edge = Column(Float, nullable=True)
    advised_stake = Column(Float, nullable=True)       # pounds; HubAI's advice, the Bet Placer decides
    reason = Column(String, nullable=True)             # one line
    status = Column(String, nullable=False, index=True)  # asked | offered | rejected | placed | gone | expired
    expires_at = Column(DateTime, nullable=False, index=True)  # earlier of kickoff and the tip's expiry; neither: seen_at + 48 h
    asked_at = Column(DateTime, nullable=True)         # first posted as asked
    answered_at = Column(DateTime, nullable=True)      # posted as offered or rejected
    seen_at = Column(DateTime, nullable=False)         # last posted by HubAI
    created_at = Column(DateTime, nullable=False, index=True)
    updated_at = Column(DateTime, nullable=False)


class PaddyLine(Base):
    """One price of one line on Paddy's board, as the sweep saw it
    (cards/history.py; Callum, 10 Nov 2026). Written when the line's price or
    fair moves, before kick-off only, kept 14 days after kick-off, so a slip
    read late is valued at the fair of the moment it was struck. A row holds
    until the next row for the same line."""
    __tablename__ = "paddy_lines"

    id = Column(Integer, primary_key=True)
    event_id = Column(String, nullable=False, index=True)
    home = Column(String, nullable=False)
    away = Column(String, nullable=False)
    commence_time = Column(String, nullable=False)    # as the board has it (ISO, UTC)
    kickoff = Column(DateTime, nullable=False, index=True)
    sport_key = Column(String, nullable=False, default="")
    market_key = Column(String, nullable=False)
    line = Column(Float, nullable=False)
    side = Column(String, nullable=False)
    odds = Column(Float, nullable=False)               # Paddy Power's price
    fair = Column(Float, nullable=False)               # the board's fair, to the penny
    basis = Column(String, nullable=False, default="ladder")
    seen_at = Column(DateTime, nullable=False, index=True)
