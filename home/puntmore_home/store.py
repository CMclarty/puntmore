"""SQLite state for the home box.

One file, plain tables, so any agent can inspect it with ``sqlite3 data/home.sqlite``.
Tables:
    runs          one row per scraper run (status, items, error, raw archive path)
    page_loads    every bookmaker page load, for the request budget
    source_state  health, cool-off and last-run per source
    tips          candidate bets from scrapes/tipsters, deduped, with settled outcome for ROI
    bets          bet lists received from Render and their placement status
    outbox        messages queued for Render (survive restarts, sent at least once)
    inbox_seen    msg_ids already processed (idempotency)
    escalations   things handed to the local AI and what it decided
    alerts        sent alerts, for de-dup
    balances      balance snapshots per account
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .schemas import new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY, source TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
    status TEXT, items INTEGER DEFAULT 0, error TEXT, raw_path TEXT);
CREATE INDEX IF NOT EXISTS runs_source ON runs(source, started);
CREATE TABLE IF NOT EXISTS page_loads(
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, source TEXT, url TEXT, status INTEGER);
CREATE INDEX IF NOT EXISTS page_loads_ts ON page_loads(ts);
CREATE TABLE IF NOT EXISTS source_state(
    source TEXT PRIMARY KEY, healthy INTEGER DEFAULT 1, cooloff_until TEXT,
    last_ok TEXT, last_run TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS tips(
    tip_id TEXT PRIMARY KEY, source_id TEXT, event TEXT, market TEXT, selection TEXT,
    advised_price REAL, advised_stake_units REAL, tip_time TEXT, expiry TEXT, origin TEXT,
    first_seen TEXT, outcome TEXT, settled_return REAL, extra TEXT);
CREATE TABLE IF NOT EXISTS bets(
    bet_id TEXT PRIMARY KEY, account_id TEXT, body TEXT, received TEXT,
    status TEXT, actual_price REAL, updated TEXT);
CREATE TABLE IF NOT EXISTS outbox(
    id INTEGER PRIMARY KEY, msg_id TEXT UNIQUE, type TEXT, body TEXT, created TEXT, sent_at TEXT);
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


def tip_id_for(source_id: str, event: str, market: str, selection: str, tip_time: str) -> str:
    """Stable id so the same tip seen twice (or from two sweeps) is stored once."""
    key = f"{source_id}|{event.strip().lower()}|{market.strip().lower()}|{selection.strip().lower()}|{tip_time[:10]}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


class Store:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- runs -----------------------------------------------------------------------------------
    def start_run(self, source: str) -> int:
        now = iso(utcnow())
        cur = self.conn.execute("INSERT INTO runs(source, started, status) VALUES(?,?,?)", (source, now, "running"))
        self.conn.execute(
            "INSERT INTO source_state(source, last_run) VALUES(?,?) ON CONFLICT(source) DO UPDATE SET last_run=excluded.last_run",
            (source, now))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, items: int = 0, error: str | None = None,
                   raw_path: str | None = None) -> None:
        self.conn.execute("UPDATE runs SET finished=?, status=?, items=?, error=?, raw_path=? WHERE id=?",
                          (iso(utcnow()), status, items, error, raw_path, run_id))
        self.conn.commit()

    def last_run(self, source: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE source=? ORDER BY id DESC LIMIT 1", (source,)).fetchone()

    def recent_runs(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ---- request budget --------------------------------------------------------------------------
    def log_page_load(self, source: str, url: str, status: int | None) -> None:
        self.conn.execute("INSERT INTO page_loads(ts, source, url, status) VALUES(?,?,?,?)",
                          (iso(utcnow()), source, url, status))
        self.conn.commit()

    def page_loads_since(self, since: datetime) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM page_loads WHERE ts>=?", (iso(since),)).fetchone()
        return int(row[0])

    # ---- source state ----------------------------------------------------------------------------
    def source_state(self, source: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM source_state WHERE source=?", (source,)).fetchone()
        if row is None:
            return {"source": source, "healthy": 1, "cooloff_until": None, "last_ok": None, "last_run": None, "note": None}
        return dict(row)

    def set_source_state(self, source: str, **fields: Any) -> None:
        allowed = {"healthy", "cooloff_until", "last_ok", "last_run", "note"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown source_state fields: {bad}")
        self.conn.execute("INSERT OR IGNORE INTO source_state(source) VALUES(?)", (source,))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            self.conn.execute(f"UPDATE source_state SET {sets} WHERE source=?", (*fields.values(), source))
        self.conn.commit()

    def all_source_states(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM source_state ORDER BY source").fetchall()]

    # ---- tips ------------------------------------------------------------------------------------
    def add_tip(self, tip: dict[str, Any]) -> str | None:
        """Insert a candidate tip. Returns its tip_id, or None if it was already stored."""
        tid = tip_id_for(tip["source_id"], tip["event"], tip["market"], tip["selection"], tip["tip_time"])
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

    # ---- outbox / inbox --------------------------------------------------------------------------
    def outbox_add(self, type_: str, body: dict, msg_id: str | None = None) -> str:
        msg_id = msg_id or new_id()
        self.conn.execute("INSERT OR IGNORE INTO outbox(msg_id, type, body, created) VALUES(?,?,?,?)",
                          (msg_id, type_, json.dumps(body), iso(utcnow())))
        self.conn.commit()
        return msg_id

    def outbox_pending(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY id LIMIT ?", (limit,)).fetchall()

    def outbox_mark_sent(self, msg_id: str) -> None:
        self.conn.execute("UPDATE outbox SET sent_at=? WHERE msg_id=?", (iso(utcnow()), msg_id))
        self.conn.commit()

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

    # ---- summaries -------------------------------------------------------------------------------
    def summary_since(self, since: datetime) -> dict[str, Any]:
        s = iso(since)
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "runs": q("SELECT COUNT(*) FROM runs WHERE started>=?", s),
            "runs_ok": q("SELECT COUNT(*) FROM runs WHERE started>=? AND status='ok'", s),
            "runs_bad": q("SELECT COUNT(*) FROM runs WHERE started>=? AND status NOT IN ('ok','running')", s),
            "tips": q("SELECT COUNT(*) FROM tips WHERE first_seen>=?", s),
            "bets_received": q("SELECT COUNT(*) FROM bets WHERE received>=?", s),
            "bets_placed": q("SELECT COUNT(*) FROM bets WHERE updated>=? AND status='placed'", s),
            "bets_failed": q("SELECT COUNT(*) FROM bets WHERE updated>=? AND status='failed'", s),
            "escalations_open": q("SELECT COUNT(*) FROM escalations WHERE resolved IS NULL"),
            "outbox_pending": q("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL"),
            "page_loads": q("SELECT COUNT(*) FROM page_loads WHERE ts>=?", s),
        }
