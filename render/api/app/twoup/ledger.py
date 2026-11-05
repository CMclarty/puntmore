"""The 2-Up ledger: clients, accounts, bets, balances.

The public functions below are the contract the Hub-endpoint code calls; keep
their names and signatures. Bets go in and out in the `hub/PROTOCOL.md` Bet
shape and reports come in as its Report body. Everything is scoped by
`client_id`: no function here reads or writes another client's rows, so a
report or a bet_id from one client can never touch another's ledger.

Rules this file holds:
- **Idempotent on bet_id.** Recording a bet that exists is a no-op; a report
  replayed is a no-op; a report for a bet in a terminal state does not move it.
- **A failed bet is never resent.** `failed` is terminal, `pending_bet_lists`
  only ever returns `queued` bets, and the decision never re-bets a selection
  that has a bet that day.
- **Dry-run sends nothing.** A client in dry-run gets its bet lists recorded
  (status `queued`, `dry_run=True`) and `pending_bet_lists` returns none of
  them. Every new client is in dry-run.
- **Kill switch** (global `twoup_kill_switch`, or the client's flag):
  `pending_bet_lists` returns nothing while it is on.
- **Open exposure** is the stake on bets that may still be live: sent and
  placed bets, and queued ones whose window has not closed.

All datetimes are stored naive UTC, like the rest of the app.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import TwoUpAccount, TwoUpBalance, TwoUpBet, TwoUpClient
from ..time_utils import utcnow

logger = logging.getLogger("uvicorn.error")

STATUSES = ("queued", "sent", "placed", "price_changed", "failed", "settled")
TERMINAL = ("price_changed", "failed", "settled")
OUTCOMES = ("won", "lost", "void", "partial")


# --- time helpers -------------------------------------------------------------

def _naive_utc(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


def local_day(now_utc: datetime | None = None) -> date:
    """The betting day (Europe/London by default) a UTC instant falls in."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(ZoneInfo(settings.twoup_timezone)).date()


# --- clients and accounts -----------------------------------------------------

def parse_clients(spec: str) -> list[tuple[str, bool, list[str]]]:
    """"owner:owner=acc1+acc2;dave=acc3" -> [("owner", True, ["acc1", "acc2"]),
    ("dave", False, ["acc3"])]."""
    out = []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        head, _, accs = part.partition("=")
        cid, _, flag = head.strip().partition(":")
        if not cid.strip():
            continue
        out.append((cid.strip(), flag.strip().lower() == "owner",
                    [a.strip() for a in accs.split("+") if a.strip()]))
    return out


def ensure_clients(db: Session, spec: str | None = None) -> int:
    """Create the clients and accounts named in `twoup_clients` that do not
    exist yet. Never modifies an existing row — bankroll, flags and balances
    belong to the ledger. Returns how many rows were created."""
    made = 0
    for cid, owner, accs in parse_clients(settings.twoup_clients if spec is None else spec):
        if db.get(TwoUpClient, cid) is None:
            db.add(TwoUpClient(client_id=cid, is_owner=owner, dry_run=True, kill_switch=False))
            db.flush()
            made += 1
        for acc in accs:
            if _account(db, cid, acc) is None:
                db.add(TwoUpAccount(client_id=cid, account_id=acc, enabled=True))
                made += 1
    db.commit()
    return made


def get_client(db: Session, client_id: str) -> TwoUpClient | None:
    return db.get(TwoUpClient, client_id)


def clients(db: Session) -> list[TwoUpClient]:
    return db.query(TwoUpClient).order_by(TwoUpClient.client_id).all()


def accounts(db: Session, client_id: str, *, enabled_only: bool = False) -> list[TwoUpAccount]:
    q = db.query(TwoUpAccount).filter(TwoUpAccount.client_id == client_id)
    if enabled_only:
        q = q.filter(TwoUpAccount.enabled.is_(True))
    return q.order_by(TwoUpAccount.account_id).all()


def _account(db: Session, client_id: str, account_id: str) -> TwoUpAccount | None:
    return (db.query(TwoUpAccount)
            .filter(TwoUpAccount.client_id == client_id,
                    TwoUpAccount.account_id == account_id).first())


def set_kill(db: Session, client_id: str, on: bool) -> TwoUpClient | None:
    c = get_client(db, client_id)
    if c is None:
        return None
    c.kill_switch = bool(on)
    c.updated_at = utcnow()
    db.commit()
    logger.warning("2up: client %s kill switch %s", client_id, "ON" if on else "off")
    return c


def pooled_bankroll(db: Session, client: TwoUpClient) -> float:
    """What Kelly sizes on: the client's configured bankroll, or (when 0) the
    sum of its enabled accounts' balances."""
    if client.bankroll and client.bankroll > 0:
        return float(client.bankroll)
    return float(sum(a.balance or 0.0 for a in accounts(db, client.client_id, enabled_only=True)))


# --- bets ---------------------------------------------------------------------

def to_protocol(bet: TwoUpBet) -> dict:
    """A ledger row as a PROTOCOL.md Bet."""
    return {
        "bet_id": bet.bet_id,
        "account_id": bet.account_id,
        "legs": bet.legs_list,
        "stake": bet.stake,
        "expected_price": bet.expected_price,
        "bet_type": bet.bet_type,
        "not_before": _iso(bet.not_before),
        "not_after": _iso(bet.not_after),
        "source_scope": bet.source_scope,
    }


def record_bet_list(db: Session, client_id: str, bets: list[dict]) -> None:
    """Record a bet list (PROTOCOL.md Bet dicts) as `queued` for this client.

    Idempotent on bet_id: a bet already in the ledger is left exactly as it
    is (so a failed one stays failed and is never re-queued). A bet for an
    account the client does not have, or an `owner` bet for a client that is
    not the owner, is refused and logged — once for the list, not per bet.
    Optional non-protocol keys `edge` and `day` are stored when present."""
    client = get_client(db, client_id)
    if client is None:
        raise ValueError(f"unknown 2up client {client_id!r}")
    list_id = uuid.uuid4().hex[:16]
    known = {a.account_id for a in accounts(db, client_id)}
    refused: list[str] = []
    added = 0
    now = utcnow()
    for b in bets:
        bid = str(b["bet_id"])
        if db.get(TwoUpBet, bid) is not None:
            continue
        scope = b.get("source_scope", "shared")
        if b["account_id"] not in known:
            refused.append(f"{bid}: unknown account {b['account_id']}")
            continue
        if scope == "owner" and not client.is_owner:
            refused.append(f"{bid}: owner-scope bet for non-owner client")
            continue
        legs = list(b.get("legs") or [])
        first = legs[0] if legs else {}
        nb, na = _naive_utc(b.get("not_before")), _naive_utc(b.get("not_after"))
        day = b.get("day")
        day = (date.fromisoformat(day) if isinstance(day, str) else day) or local_day(
            (nb or now).replace(tzinfo=timezone.utc))
        db.add(TwoUpBet(
            bet_id=bid, client_id=client_id, account_id=b["account_id"], list_id=list_id,
            legs=json.dumps(legs), stake=float(b["stake"]),
            expected_price=float(b["expected_price"]), bet_type=b.get("bet_type", "single"),
            source_scope=scope, not_before=nb, not_after=na, day=day,
            event_id=str(first.get("event_id")) if first.get("event_id") is not None else None,
            selection=first.get("selection"), edge=b.get("edge"),
            status="queued", dry_run=bool(client.dry_run), created_at=now, updated_at=now,
        ))
        added += 1
    try:
        db.commit()
    except IntegrityError:
        # Two writers recorded the same bet_id at once: the other one won,
        # which is exactly the idempotent outcome. Retry without the dupes.
        db.rollback()
        return record_bet_list(db, client_id, [b for b in bets
                                               if db.get(TwoUpBet, str(b["bet_id"])) is None])
    if refused:
        logger.warning("2up: client %s: %d bet(s) refused: %s", client_id, len(refused),
                       "; ".join(refused[:5]))
    if added:
        logger.info("2up: client %s: %d bet(s) queued%s", client_id, added,
                    " (dry-run, not sent)" if client.dry_run else "")


def pending_bet_lists(db: Session, client_id: str) -> list[dict]:
    """Bet lists queued but not yet sent, oldest first, each
    `{"list_id": ..., "bets": [Bet, ...]}` — the `bets` key is the PROTOCOL.md
    `bet_list` body. Empty while the global or the client's kill switch is
    on, while the client is in dry-run, and for an unknown client. A queued
    bet whose window has closed is not returned (the Hub would only fail it)."""
    client = get_client(db, client_id)
    if client is None or settings.twoup_kill_switch or client.kill_switch or client.dry_run:
        return []
    now = utcnow()
    rows = (db.query(TwoUpBet)
            .filter(TwoUpBet.client_id == client_id, TwoUpBet.status == "queued",
                    TwoUpBet.dry_run.is_(False))
            .order_by(TwoUpBet.created_at, TwoUpBet.bet_id).all())
    lists: dict[str, list[dict]] = {}
    for b in rows:
        if b.not_after is not None and b.not_after <= now:
            continue
        if b.source_scope == "owner" and not client.is_owner:
            continue                       # belt and braces: never leaves for a non-owner
        lists.setdefault(b.list_id, []).append(to_protocol(b))
    return [{"list_id": lid, "bets": bets} for lid, bets in lists.items()]


def mark_sent(db: Session, client_id: str, bet_ids: list[str]) -> None:
    """queued -> sent for these bets of this client. Any other status, or
    another client's bet_id, is left alone."""
    if not bet_ids:
        return
    now = utcnow()
    for b in (db.query(TwoUpBet)
              .filter(TwoUpBet.client_id == client_id, TwoUpBet.bet_id.in_(list(bet_ids)),
                      TwoUpBet.status == "queued").all()):
        b.status, b.sent_at, b.updated_at = "sent", now, now
    db.commit()


def open_exposure(db: Session, client_id: str, account_id: str) -> float:
    """Stake that may still be live on this account: sent and placed bets,
    plus queued ones whose placement window has not closed."""
    now = utcnow()
    total = 0.0
    for b in (db.query(TwoUpBet)
              .filter(TwoUpBet.client_id == client_id, TwoUpBet.account_id == account_id,
                      TwoUpBet.status.in_(("queued", "sent", "placed"))).all()):
        if b.status == "queued" and b.not_after is not None and b.not_after <= now:
            continue
        total += b.stake or 0.0
    return round(total, 2)


def bets_on_day(db: Session, client_id: str, day: date) -> list[TwoUpBet]:
    return (db.query(TwoUpBet)
            .filter(TwoUpBet.client_id == client_id, TwoUpBet.day == day)
            .order_by(TwoUpBet.created_at).all())


def trailing_stakes(db: Session, client_id: str, account_id: str, n: int) -> list[float]:
    """The account's last `n` stakes, oldest first: bets that were placed or
    settled, plus dry-run bets (which stand in for placed ones while the
    engine is validated). Failed and re-priced bets were never on the account."""
    rows = (db.query(TwoUpBet)
            .filter(TwoUpBet.client_id == client_id, TwoUpBet.account_id == account_id,
                    TwoUpBet.status.notin_(("failed", "price_changed")))
            .order_by(TwoUpBet.created_at.desc()).limit(n).all())
    return [b.stake for b in reversed(rows)
            if b.status in ("placed", "settled") or b.dry_run]


def recent_bets(db: Session, client_id: str, limit: int = 200) -> list[TwoUpBet]:
    return (db.query(TwoUpBet).filter(TwoUpBet.client_id == client_id)
            .order_by(TwoUpBet.created_at.desc()).limit(limit).all())


def recent_balances(db: Session, client_id: str, limit: int = 100) -> list[TwoUpBalance]:
    return (db.query(TwoUpBalance).filter(TwoUpBalance.client_id == client_id)
            .order_by(TwoUpBalance.ts.desc()).limit(limit).all())


# --- reports ------------------------------------------------------------------

def apply_report(db: Session, client_id: str, report: dict) -> None:
    """Apply one PROTOCOL.md Report body for this client. Idempotent:

    - placed / price_changed / failed move a `queued` or `sent` bet only. A
      bet gets at most one of the three; a second is ignored.
    - settled settles any bet not already settled, failed or re-priced (a
      `settled` may arrive before a lost `placed`).
    - balance updates the account when the report is newer than what it
      holds, and is stored once per (account, ts). An account the client has
      not registered is created DISABLED, so a balance is never lost but the
      account receives no bets until enabled.

    A bet_id that is unknown, or belongs to another client, is ignored."""
    kind = report.get("kind")
    ts = _naive_utc(report.get("ts")) or utcnow()
    if kind == "balance":
        _apply_balance(db, client_id, str(report["account_id"]), float(report["balance"]), ts)
        return
    if kind not in ("placed", "price_changed", "failed", "settled"):
        logger.warning("2up: client %s: unknown report kind %r ignored", client_id, kind)
        return
    bid = str(report.get("bet_id"))
    bet = db.get(TwoUpBet, bid)
    if bet is None or bet.client_id != client_id:
        logger.warning("2up: client %s: %s report for unknown bet %s ignored", client_id, kind, bid)
        return
    now = utcnow()
    if kind == "settled":
        if bet.status in ("settled", "failed", "price_changed"):
            return
        outcome = report.get("outcome")
        if outcome not in OUTCOMES:
            logger.warning("2up: bet %s: bad outcome %r ignored", bid, outcome)
            return
        bet.status, bet.outcome = "settled", outcome
        bet.return_amount = float(report.get("return") or 0.0)
        bet.settled_at = ts
    else:
        if bet.status not in ("queued", "sent"):
            return                           # replay, or a second lifecycle report
        if kind == "placed":
            bet.status = "placed"
            bet.actual_price = float(report["actual_price"])
            bet.hub_dry_run = bool(report.get("dry_run"))
            bet.placed_at = ts
        elif kind == "price_changed":
            bet.status = "price_changed"
            bet.new_price = float(report["new_price"])
        else:
            bet.status = "failed"
            bet.fail_reason = str(report.get("reason") or "")[:500]
            # Escalation is the Hub's (it drops and escalates on its side);
            # Render records it and never resends this bet_id.
            logger.warning("2up: client %s: bet %s failed: %s", client_id, bid, bet.fail_reason)
    bet.updated_at = now
    db.commit()


def _apply_balance(db: Session, client_id: str, account_id: str, balance: float,
                   ts: datetime) -> None:
    if get_client(db, client_id) is None:
        logger.warning("2up: balance for unknown client %s ignored", client_id)
        return
    acc = _account(db, client_id, account_id)
    if acc is None:
        acc = TwoUpAccount(client_id=client_id, account_id=account_id, enabled=False)
        db.add(acc)
        logger.warning("2up: client %s: balance for unregistered account %s — "
                       "created DISABLED", client_id, account_id)
    exists = (db.query(TwoUpBalance)
              .filter(TwoUpBalance.client_id == client_id,
                      TwoUpBalance.account_id == account_id, TwoUpBalance.ts == ts).first())
    if exists is None:
        db.add(TwoUpBalance(client_id=client_id, account_id=account_id, balance=balance, ts=ts))
    if acc.balance_at is None or ts >= acc.balance_at:
        acc.balance, acc.balance_at = balance, ts
    db.commit()
