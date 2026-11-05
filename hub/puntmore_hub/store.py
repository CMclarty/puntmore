"""SQLite state for the Hub.

One file, plain tables, so any agent can inspect it with ``sqlite3 data/hub.sqlite``.
Tables:
    tips          candidate bets from the Scraper's sources, deduped, with settled outcome for ROI
    bets          bet lists received from Render and their placement status
    outbox        messages queued for Render (survive restarts, sent at least once)
    inbox_seen    msg_ids already processed (idempotency)
    escalations   things handed to the local AI and what it decided
    alerts        sent alerts, for de-dup
    balances      balance snapshots per account
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ids import tip_id_for  # noqa: F401  (the one tip id; re-exported)
from .schemas import new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS tips(
    tip_id TEXT PRIMARY KEY, source_id TEXT, event TEXT, market TEXT, selection TEXT,
    advised_price REAL, advised_stake_units REAL, tip_time TEXT, expiry TEXT, origin TEXT,
    first_seen TEXT, outcome TEXT, settled_return REAL, extra TEXT);
CREATE TABLE IF NOT EXISTS bets(
    bet_id TEXT PRIMARY KEY, account_id TEXT, body TEXT, received TEXT,
    status TEXT, actual_price REAL, updated TEXT);
CREATE TABLE IF NOT EXISTS outbox(
    id INTEGER PRIMARY KEY, msg_id TEXT UNIQUE, type TEXT, body TEXT, created TEXT, sent_at TEXT, acked_at TEXT);
CREATE TABLE IF NOT EXISTS inbox_seen(msg_id TEXT PRIMARY KEY, ts TEXT);
CREATE TABLE IF NOT EXISTS escalations(
    id INTEGER PRIMARY KEY, kind TEXT, ref TEXT, detail TEXT, created TEXT,
    outcome TEXT, resolved TEXT, reported INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, key TEXT, ts TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS balances(account_id TEXT, balance REAL, ts TEXT);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Store:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    # ---- tips ------------------------------------------------------------------------------------
    def add_tip(self, tip: dict[str, Any], tip_id: str | None = None) -> str | None:
        """Insert a candidate tip under `tip_id` (the Scraper's, minted where the tip was
        first seen), or one minted here when none is given. Returns its tip_id, or None
        if it was already stored."""
        tid = tip_id or tip_id_for(tip["source_id"], tip["event"], tip["market"], tip["selection"], tip["tip_time"])
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO tips(tip_id, source_id, event, market, selection, advised_price,
               advised_stake_units, tip_time, expiry, origin, first_seen, extra)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, tip["source_id"], tip["event"], tip["market"], tip["selection"], tip.get("advised_price"),
             tip.get("advised_stake_units"), tip["tip_time"], tip.get("expiry"), tip.get("origin", "scrape"),
             iso(utcnow()), json.dumps(tip.get("extra") or {})))
        self.conn.commit()
        return tid if cur.rowcount else None

    def settle_tip(self, tip_id: str, outcome: str, settled_return: float) -> None:
        self.conn.execute("UPDATE tips SET outcome=?, settled_return=? WHERE tip_id=?", (outcome, settled_return, tip_id))
        self.conn.commit()

    def source_record(self, source_id: str) -> dict[str, Any]:
        """Rolling strike rate / ROI per source, feeding the trust weight."""
        rows = self.conn.execute(
            "SELECT outcome, settled_return, advised_stake_units FROM tips WHERE source_id=? AND outcome IS NOT NULL",
            (source_id,)).fetchall()
        n = len(rows)
        wins = sum(1 for r in rows if r["outcome"] == "won")
        staked = sum(float(r["advised_stake_units"] or 1.0) for r in rows)
        returned = sum(float(r["settled_return"] or 0.0) for r in rows)
        return {"settled": n, "strike_rate": (wins / n) if n else None,
                "roi": ((returned - staked) / staked) if staked else None}

    # ---- bets ------------------------------------------------------------------------------------
    def bet_exists(self, bet_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM bets WHERE bet_id=?", (bet_id,)).fetchone() is not None

    def upsert_bet(self, bet_id: str, account_id: str, body: dict, status: str) -> None:
        now = iso(utcnow())
        self.conn.execute(
            """INSERT INTO bets(bet_id, account_id, body, received, status, updated) VALUES(?,?,?,?,?,?)
               ON CONFLICT(bet_id) DO UPDATE SET status=excluded.status, updated=excluded.updated""",
            (bet_id, account_id, json.dumps(body), now, status, now))
        self.conn.commit()

    def set_bet_status(self, bet_id: str, status: str, actual_price: float | None = None) -> None:
        self.conn.execute("UPDATE bets SET status=?, actual_price=COALESCE(?, actual_price), updated=? WHERE bet_id=?",
                          (status, actual_price, iso(utcnow()), bet_id))
        self.conn.commit()

    def open_exposure(self, account_id: str) -> float:
        """Sum of stakes for bets placed (or in flight) and not yet settled on this account."""
        rows = self.conn.execute("SELECT body FROM bets WHERE account_id=? AND status IN ('received','placing','placed')",
                                 (account_id,)).fetchall()
        return sum(float(json.loads(r["body"]).get("stake", 0.0)) for r in rows)

    def _migrate(self) -> None:
        """Columns added after a database was first created."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(outbox)").fetchall()}
        if "acked_at" not in cols:
            self.conn.execute("ALTER TABLE outbox ADD COLUMN acked_at TEXT")
            self.conn.commit()

    # ---- outbox / inbox --------------------------------------------------------------------------
    # A message is `sent` when it went down the socket and `acked` when Render answered with its
    # msg_id (PROTOCOL.md, "Idempotency and acknowledgement"). Only an ack finishes it: on every
    # reconnect `outbox_resend_unacked` puts the sent-but-unacked ones back in the queue, same msg_id,
    # so Render's replay check does the de-duplication. Control frames (ack, pong) are never acked by
    # Render and are finished at send time.
    def outbox_add(self, type_: str, body: dict, msg_id: str | None = None) -> str:
        msg_id = msg_id or new_id()
        self.conn.execute("INSERT OR IGNORE INTO outbox(msg_id, type, body, created) VALUES(?,?,?,?)",
                          (msg_id, type_, json.dumps(body), iso(utcnow())))
        self.conn.commit()
        return msg_id

    def outbox_pending(self, limit: int = 100) -> list[sqlite3.Row]:
        """What to send now: never sent on the current socket and not yet acked."""
        return self.conn.execute("SELECT * FROM outbox WHERE sent_at IS NULL AND acked_at IS NULL ORDER BY id LIMIT ?",
                                 (limit,)).fetchall()

    def outbox_unacked(self) -> list[sqlite3.Row]:
        """Everything Render has not acknowledged, sent or not."""
        return self.conn.execute("SELECT * FROM outbox WHERE acked_at IS NULL ORDER BY id").fetchall()

    def outbox_mark_sent(self, msg_id: str, acked: bool = False) -> None:
        now = iso(utcnow())
        self.conn.execute("UPDATE outbox SET sent_at=?, acked_at=COALESCE(acked_at, ?) WHERE msg_id=?",
                          (now, now if acked else None, msg_id))
        self.conn.commit()

    def outbox_mark_acked(self, msg_id: str) -> bool:
        """Render acknowledged msg_id. Returns False if it was unknown or already acked."""
        now = iso(utcnow())
        cur = self.conn.execute("UPDATE outbox SET acked_at=?, sent_at=COALESCE(sent_at, ?) WHERE msg_id=? AND acked_at IS NULL",
                                (now, now, msg_id))
        self.conn.commit()
        return cur.rowcount == 1

    def outbox_resend_unacked(self) -> int:
        """On a new socket: queue every sent-but-unacked message again. Returns how many."""
        cur = self.conn.execute("UPDATE outbox SET sent_at=NULL WHERE acked_at IS NULL AND sent_at IS NOT NULL")
        self.conn.commit()
        return cur.rowcount

    def inbox_seen(self, msg_id: str) -> bool:
        """Record an inbound msg_id. Returns True if it had been seen before (replay)."""
        cur = self.conn.execute("INSERT OR IGNORE INTO inbox_seen(msg_id, ts) VALUES(?,?)", (msg_id, iso(utcnow())))
        self.conn.commit()
        return cur.rowcount == 0

    # ---- escalations -----------------------------------------------------------------------------
    def add_escalation(self, kind: str, ref: str, detail: str) -> int:
        cur = self.conn.execute("INSERT INTO escalations(kind, ref, detail, created) VALUES(?,?,?,?)",
                                (kind, ref, detail, iso(utcnow())))
        self.conn.commit()
        return int(cur.lastrowid)

    def resolve_escalation(self, esc_id: int, outcome: str) -> None:
        self.conn.execute("UPDATE escalations SET outcome=?, resolved=? WHERE id=?", (outcome, iso(utcnow()), esc_id))
        self.conn.commit()

    def open_escalations(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM escalations WHERE resolved IS NULL ORDER BY id").fetchall()

    def escalation(self, esc_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM escalations WHERE id=?", (esc_id,)).fetchone()

    # ---- alerts / balances -----------------------------------------------------------------------
    def alert_recent(self, key: str, within: timedelta) -> bool:
        row = self.conn.execute("SELECT 1 FROM alerts WHERE key=? AND ts>=? LIMIT 1",
                                (key, iso(utcnow() - within))).fetchone()
        return row is not None

    def add_alert(self, key: str, message: str) -> None:
        self.conn.execute("INSERT INTO alerts(key, ts, message) VALUES(?,?,?)", (key, iso(utcnow()), message))
        self.conn.commit()

    def add_balance(self, account_id: str, amount: float) -> None:
        self.conn.execute("INSERT INTO balances(account_id, balance, ts) VALUES(?,?,?)", (account_id, amount, iso(utcnow())))
        self.conn.commit()

    def latest_balance(self, account_id: str) -> float | None:
        row = self.conn.execute("SELECT balance FROM balances WHERE account_id=? ORDER BY ts DESC LIMIT 1",
                                (account_id,)).fetchone()
        return float(row[0]) if row else None

    # ---- readers for the Hub Console -----------------------------------------------------------
    def recent_tips(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT tip_id, source_id, event, market, selection, advised_price, tip_time, first_seen, outcome "
            "FROM tips ORDER BY first_seen DESC LIMIT ?", (limit,)).fetchall()

    def tip_sources(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT source_id FROM tips ORDER BY source_id").fetchall()]

    def recent_bets(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM bets ORDER BY updated DESC, received DESC LIMIT ?", (limit,)).fetchall()

    def accounts(self) -> list[str]:
        """Every account seen in a bet or a balance snapshot."""
        return [r[0] for r in self.conn.execute(
            "SELECT account_id FROM bets UNION SELECT account_id FROM balances ORDER BY 1").fetchall() if r[0]]

    def recent_outbox(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT id, msg_id, type, created, sent_at, acked_at FROM outbox ORDER BY id DESC LIMIT ?",
                                 (limit,)).fetchall()

    def outbox_counts(self) -> dict[str, int]:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {"pending": q("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL AND acked_at IS NULL"),
                "unacked": q("SELECT COUNT(*) FROM outbox WHERE acked_at IS NULL"),
                "total": q("SELECT COUNT(*) FROM outbox")}

    def resolved_escalations(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM escalations WHERE resolved IS NOT NULL ORDER BY resolved DESC LIMIT ?",
                                 (limit,)).fetchall()

    # ---- summaries -------------------------------------------------------------------------------
    def summary_since(self, since: datetime) -> dict[str, Any]:
        s = iso(since)
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "tips": q("SELECT COUNT(*) FROM tips WHERE first_seen>=?", s),
            "bets_received": q("SELECT COUNT(*) FROM bets WHERE received>=?", s),
            "bets_placed": q("SELECT COUNT(*) FROM bets WHERE updated>=? AND status='placed'", s),
            "bets_failed": q("SELECT COUNT(*) FROM bets WHERE updated>=? AND status='failed'", s),
            "escalations_open": q("SELECT COUNT(*) FROM escalations WHERE resolved IS NULL"),
            "outbox_pending": q("SELECT COUNT(*) FROM outbox WHERE acked_at IS NULL"),
        }
