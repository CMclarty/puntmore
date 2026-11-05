import json

from sqlalchemy import (
    Boolean, Column, Integer, String, Float, DateTime, Date, ForeignKey, Text,
    UniqueConstraint,
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
    """A machine caller (a script, a future bet-placer) with its own key.

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


# AW 26 (aw_slips, aw_legs, aw_fairs, aw_leg_lines, value_snapshots,
# value_lines) was retired on 29 Oct 2026; its tables are left in the
# database untouched, and its code is on the archive/aw26 branch.


# The scraper control plane (scraper_leagues, scraper_markets,
# scraper_timings, scraper_target_status, scraper_site_status) was removed on
# 5 Nov 2026: the Worker reads targets.toml on the Hub. Its tables are left in
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


# --- the Hub link (hub/PROTOCOL.md, app/hub/) ----------------------------------
# Every table here is bounded by `hub.service.sweep` (retention per table in
# app/hub/service.py). Times are naive UTC like the rest of the schema.

class HubInboxSeen(Base):
    """An inbound msg_id from a Hub that Render processed — the idempotency
    record. A frame whose (client_id, msg_id) is here is acked `replay: true`
    and otherwise ignored. Swept after 7 days."""
    __tablename__ = "hub_inbox_seen"
    __table_args__ = (UniqueConstraint("client_id", "msg_id", name="uq_hub_inbox_seen"),)

    id = Column(Integer, primary_key=True)
    client_id = Column(String, nullable=False)
    msg_id = Column(String, nullable=False)
    type = Column(String, nullable=False)
    seen_at = Column(DateTime, nullable=False, default=utcnow, index=True)


class HubTip(Base):
    """A candidate tip from a Hub's Bet Finder sources, kept for the Bet Finder
    orchestrator to consume. `tip_id` is stable per source + event + market +
    selection + day, so a re-scraped tip is the same row (the first is kept).
    `body` is the tip as sent (JSON); the named columns are copies for listing."""
    __tablename__ = "hub_tips"

    id = Column(Integer, primary_key=True)
    tip_id = Column(String, unique=True, nullable=False)
    client_id = Column(String, nullable=False)
    source_id = Column(String, nullable=False)
    event = Column(String, nullable=False)
    market = Column(String, nullable=False)
    selection = Column(String, nullable=False)
    advised_price = Column(Float, nullable=True)
    tip_time = Column(String, nullable=False)            # ISO, as sent
    body = Column(Text, nullable=False)
    received_at = Column(DateTime, nullable=False, default=utcnow, index=True)


class HubEscalation(Base):
    """Something a Hub handed to its local AI, and (once decided) the outcome.
    One row per (client_id, escalation_id): `escalation` creates it,
    `escalation_outcome` fills `outcome` (creating the row if the escalation
    itself never arrived)."""
    __tablename__ = "hub_escalations"
    __table_args__ = (
        UniqueConstraint("client_id", "escalation_id", name="uq_hub_escalation"),
    )

    id = Column(Integer, primary_key=True)
    client_id = Column(String, nullable=False)
    escalation_id = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)
    ref = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    created = Column(String, nullable=True)              # ISO, as sent
    outcome = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    outcome_at = Column(DateTime, nullable=True)


class HubOutbox(Base):
    """A frame Render sends a Hub (bet_list, kill_switch, scrape_job), kept
    until the Hub acks it. Unacked frames are re-sent, same msg_id, when the
    client reconnects. A bet_list's bet_ids are marked sent in the engine only
    when its ack arrives."""
    __tablename__ = "hub_outbox"

    id = Column(Integer, primary_key=True)
    msg_id = Column(String, unique=True, nullable=False)
    client_id = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False)
    frame = Column(Text, nullable=False)                 # the whole envelope, JSON
    bet_ids = Column(Text, nullable=True)                # bet_list only: JSON list
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)
    sent_at = Column(DateTime, nullable=True)
    acked_at = Column(DateTime, nullable=True)


class HubSentBet(Base):
    """Every bet_id ever put in a frame to a Hub. A bet_id goes out in exactly
    ONE frame: that frame may be re-delivered (same msg_id), but the bet is
    never listed in a new one — PROTOCOL.md: a failed bet is never resent."""
    __tablename__ = "hub_sent_bets"
    __table_args__ = (UniqueConstraint("client_id", "bet_id", name="uq_hub_sent_bet"),)

    id = Column(Integer, primary_key=True)
    client_id = Column(String, nullable=False)
    bet_id = Column(String, nullable=False)
    msg_id = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow, index=True)

# --- 2-Up engine ledger (app/twoup/ledger.py) --------------------------------
# Every row is keyed client_id -> account_id (spec, "Tenancy model"): a client
# is a separate customer with its own pooled bankroll, an account one phone
# with one bet365 account on it. Nothing here places a bet; the Hub endpoint
# sends what is queued and reports come back through ledger.apply_report.

class TwoUpClient(Base):
    """A 2-Up client. Dry-run is ON for every new client: its bet lists are
    produced and recorded but never handed to the Hub endpoint."""
    __tablename__ = "twoup_clients"

    client_id = Column(String, primary_key=True)
    name = Column(String, nullable=True)
    # The pooled bankroll Kelly sizes on. 0 = the sum of the client's enabled
    # accounts' reported balances.
    bankroll = Column(Float, nullable=False, default=0.0)
    kill_switch = Column(Boolean, nullable=False, default=False)
    dry_run = Column(Boolean, nullable=False, default=True)
    # Only the owner client ever receives `owner`-scope (Bet Finder) bets.
    is_owner = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class TwoUpAccount(Base):
    """A sub-client: one phone, one bookmaker account."""
    __tablename__ = "twoup_accounts"
    __table_args__ = (UniqueConstraint("client_id", "account_id"),)

    id = Column(Integer, primary_key=True)
    client_id = Column(String, ForeignKey("twoup_clients.client_id"), nullable=False, index=True)
    account_id = Column(String, nullable=False)
    balance = Column(Float, nullable=False, default=0.0)
    balance_at = Column(DateTime, nullable=True)          # ts of the report it came from
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class TwoUpBet(Base):
    """One bet in the PROTOCOL.md Bet shape plus its lifecycle.

    status: queued -> sent -> placed -> settled, or price_changed / failed
    (terminal: a failed bet_id is never resent). Times are naive UTC."""
    __tablename__ = "twoup_bets"

    bet_id = Column(String, primary_key=True)
    client_id = Column(String, ForeignKey("twoup_clients.client_id"), nullable=False, index=True)
    account_id = Column(String, nullable=False, index=True)
    list_id = Column(String, nullable=False, index=True)  # the record_bet_list call it came in
    legs = Column(Text, nullable=False, default="[]")     # JSON, PROTOCOL.md bet_leg list
    stake = Column(Float, nullable=False)
    expected_price = Column(Float, nullable=False)
    bet_type = Column(String, nullable=False, default="single")
    source_scope = Column(String, nullable=False, default="shared")
    not_before = Column(DateTime, nullable=True)
    not_after = Column(DateTime, nullable=True)
    # The London day the bet belongs to, and the first leg's event/selection —
    # what the daily cap and the "never bet the same selection twice" check read.
    day = Column(Date, nullable=False, index=True)
    event_id = Column(String, nullable=True)
    selection = Column(String, nullable=True)
    edge = Column(Float, nullable=True)                   # model edge when decided
    status = Column(String, nullable=False, default="queued")
    dry_run = Column(Boolean, nullable=False, default=True)   # Render's dry-run: never sent
    hub_dry_run = Column(Boolean, nullable=True)          # the Hub's `placed.dry_run`
    actual_price = Column(Float, nullable=True)
    new_price = Column(Float, nullable=True)              # price_changed
    fail_reason = Column(String, nullable=True)
    outcome = Column(String, nullable=True)               # won | lost | void | partial
    return_amount = Column("return", Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    sent_at = Column(DateTime, nullable=True)
    placed_at = Column(DateTime, nullable=True)
    settled_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow)

    @property
    def legs_list(self) -> list:
        try:
            return json.loads(self.legs or "[]")
        except ValueError:
            return []


class TwoUpBalance(Base):
    """Every balance report, as history. The account row holds the latest."""
    __tablename__ = "twoup_balances"
    __table_args__ = (UniqueConstraint("client_id", "account_id", "ts"),)

    id = Column(Integer, primary_key=True)
    client_id = Column(String, nullable=False, index=True)
    account_id = Column(String, nullable=False)
    balance = Column(Float, nullable=False)
    ts = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class BetFinderSource(Base):
    """One Bet Finder source (app/betfinder/sources.py): config as data.

    Seeded from BETFINDER_SOURCES on startup (missing rows only, never an
    edit); a tip from a source not here creates it INACTIVE at trust 0.5.
    `trust_weight` (0..1) scales the stake and is set by hand, never by the
    code. `stake_mode`: auto (advised units when the tip carries them, else
    Kelly) | advised | kelly. `kind`: hub (tips over the Hub link) | imap (a
    documented seam, not implemented: Render never fetches)."""
    __tablename__ = "betfinder_sources"

    name = Column(String, primary_key=True)
    kind = Column(String, nullable=False, default="hub")
    trust_weight = Column(Float, nullable=False, default=0.5)
    active = Column(Boolean, nullable=False, default=False)
    stake_mode = Column(String, nullable=False, default="auto")
    note = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class BetFinderTip(Base):
    """One `hub_tips` row as the Bet Finder pipeline sees it (one per tip_id).

    Kept apart from `hub_tips` (swept at 30 days) so a source's record
    outlives the raw tip. The `*_raw` columns are the tip's text, truncated,
    UNTRUSTED: shown, never logged, never put in a prompt. The feed columns
    (event_id ... edge) are OddsPapi's, set when the tip is priced.

    status: new -> priced -> decided -> settled | not_placed, or quarantined
    (with a `quarantine` reason), duplicate (of `duplicate_of`), expired."""
    __tablename__ = "betfinder_tips"

    id = Column(Integer, primary_key=True)
    tip_id = Column(String, unique=True, nullable=False)
    hub_client_id = Column(String, nullable=False)
    source = Column(String, nullable=False, index=True)
    origin = Column(String, nullable=True)
    event_raw = Column(String, nullable=False)
    market_raw = Column(String, nullable=False)
    selection_raw = Column(String, nullable=False)
    market = Column(String, nullable=False)              # normalised ("match_result", ...)
    dedupe_key = Column(String, nullable=False, index=True)
    tip_time = Column(DateTime, nullable=True)           # naive UTC
    tip_day = Column(Date, nullable=True)
    expiry = Column(DateTime, nullable=True)
    advised_price = Column(Float, nullable=True)
    advised_stake_units = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="new", index=True)
    quarantine = Column(String, nullable=True)
    duplicate_of = Column(String, nullable=True)
    # From the 2-Up feed once matched and priced.
    event_id = Column(String, nullable=True, index=True)
    event = Column(String, nullable=True)
    home = Column(String, nullable=True)
    away = Column(String, nullable=True)
    selection = Column(String, nullable=True)
    side = Column(String, nullable=True)
    kickoff = Column(DateTime, nullable=True)
    competition = Column(String, nullable=True)
    match_score = Column(Float, nullable=True)
    price = Column(Float, nullable=True)                 # bet365
    p_win = Column(Float, nullable=True)                 # Pinnacle de-vigged
    fair_price = Column(Float, nullable=True)
    edge = Column(Float, nullable=True)                  # p_win - 1/price (rescue 0)
    priced_at = Column(DateTime, nullable=True)
    # From the 2-Up ledger once bet and settled.
    bet_ids = Column(Text, nullable=False, default="[]")
    outcome = Column(String, nullable=True)
    staked = Column(Float, nullable=True)
    returned = Column(Float, nullable=True)
    settled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow)

    @property
    def bet_id_list(self) -> list:
        try:
            v = json.loads(self.bet_ids or "[]")
        except ValueError:
            return []
        return v if isinstance(v, list) else []
