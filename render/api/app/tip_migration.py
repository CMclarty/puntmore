"""One-off: `hub_tips` + `betfinder_tips` -> the one `tips` table.

A tip used to be two Render rows: `hub_tips` (what the endpoint wrote, 30
days) and `betfinder_tips` (the Bet Finder's copy, 90 days, its own dedupe
key). They are now one row (`models.Tip`). A fresh database only ever gets
`tips` (`create_all`); an existing one runs `migrate_tips` at startup:

- every `betfinder_tips` row becomes a `tips` row with its Bet Finder state,
  joined to its `hub_tips` row (by tip_id) for the wire columns; a Bet
  Finder row whose hub row was already swept keeps its own copies, and its
  `body` is rebuilt from them (marked `"migrated": true`);
- every `hub_tips` row the Bet Finder never ingested becomes a `tips` row
  with its Bet Finder columns NULL, so the next pass ingests it;
- a tip_id already in `tips` is never touched (a second run adds nothing);
- it all happens in ONE transaction, and a marker row in `schema_migrations`
  is written in that transaction, so it runs once: a later run cannot bring
  back a tip the retention sweep has since dropped;
- the old tables are LEFT IN PLACE, unused. Dropping them is a later manual
  step (`DROP TABLE hub_tips; DROP TABLE betfinder_tips;`).

Nothing is read or written by name outside this module: the old models are
gone, the old tables are reflected.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.engine import Engine

from .betfinder.pipeline import clean_text, price_or_none
from .models import Tip
from .time_utils import utcnow

log = logging.getLogger("uvicorn.error")

MARKER = "tips_one_table"
OLD = ("hub_tips", "betfinder_tips")

# Bet Finder state copied as is from a betfinder_tips row.
_STATE = (
    "status", "source", "origin", "market", "dedupe_key", "tip_time", "tip_day", "expiry",
    "advised_stake_units", "quarantine", "duplicate_of", "event_id", "event", "home", "away",
    "selection", "side", "kickoff", "competition", "match_score", "price", "p_win",
    "fair_price", "edge", "priced_at", "bet_ids", "outcome", "staked", "returned",
    "settled_at", "updated_at",
)


def _iso(dt) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat() + ("+00:00" if dt.tzinfo is None else "")
    return str(dt or "")


def _row(hub: dict | None, bf: dict | None) -> dict:
    """The `tips` row for one tip_id, from its hub row, its Bet Finder row, or both."""
    h, b = hub or {}, bf or {}
    out = {"tip_id": h.get("tip_id") or b.get("tip_id")}
    out["client_id"] = h.get("client_id") or b.get("hub_client_id") or "-"
    out["source_id"] = h.get("source_id") or b.get("source") or "-"
    # The Bet Finder's copies were what it matched on; the hub's otherwise.
    for col, hub_col in (("event_raw", "event"), ("market_raw", "market"),
                         ("selection_raw", "selection")):
        out[col] = b.get(col) if bf is not None else clean_text(h.get(hub_col))
        out[col] = out[col] or "-"
    out["advised_price"] = (b.get("advised_price") if bf is not None
                            else price_or_none(h.get("advised_price")))
    out["tip_time_raw"] = h.get("tip_time") or _iso(b.get("tip_time"))
    if h.get("body"):
        out["body"] = h["body"]
    else:
        out["body"] = json.dumps({
            "tip_id": out["tip_id"], "source_id": out["source_id"], "event": out["event_raw"],
            "market": out["market_raw"], "selection": out["selection_raw"],
            "advised_price": out["advised_price"],
            "advised_stake_units": b.get("advised_stake_units"),
            "tip_time": out["tip_time_raw"],
            "expiry": _iso(b.get("expiry")) if b.get("expiry") else None,
            "origin": b.get("origin"), "source_scope": "owner", "migrated": True,
        })
    out["received_at"] = h.get("received_at") or b.get("created_at") or utcnow()
    for col in _STATE:
        out[col] = b.get(col) if bf is not None else None
    if bf is not None and not out["bet_ids"]:
        out["bet_ids"] = "[]"
    return out


def _done(conn) -> bool:
    conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations "
                      "(name VARCHAR PRIMARY KEY, applied_at VARCHAR NOT NULL)"))
    return conn.execute(text("SELECT 1 FROM schema_migrations WHERE name = :n"),
                        {"n": MARKER}).first() is not None


def migrate_tips(bind: Engine) -> dict | None:
    """Copy the two old tip tables into `tips`, once. Returns counts, or None
    when there is nothing to do (a fresh database, or already done)."""
    present = [t for t in OLD if t in inspect(bind).get_table_names()]
    if not present:
        return None
    with bind.begin() as conn:
        if _done(conn):
            return None
        Tip.__table__.create(conn, checkfirst=True)
        meta = MetaData()
        rows: dict[str, dict[str, dict]] = {}
        for name in present:
            t = Table(name, meta, autoload_with=conn)
            order = [c for c in (t.c.get("received_at"), t.c.get("created_at"), t.c.get("id"))
                     if c is not None]
            rows[name] = {r["tip_id"]: dict(r)
                          for r in conn.execute(select(t).order_by(*order)).mappings()}
        hub, bf = rows.get("hub_tips", {}), rows.get("betfinder_tips", {})
        have = set(conn.execute(select(Tip.__table__.c.tip_id)).scalars())
        # Bet Finder rows first, in their own order (dedupe's "first" is the
        # lowest id), then the hub rows it had not ingested yet.
        order = list(bf) + [tid for tid in hub if tid not in bf]
        new = [_row(hub.get(tid), bf.get(tid)) for tid in order if tid not in have]
        if new:
            conn.execute(Tip.__table__.insert(), new)
        conn.execute(text("INSERT INTO schema_migrations (name, applied_at) VALUES (:n, :at)"),
                     {"n": MARKER, "at": utcnow().isoformat()})
    counts = {"hub_tips": len(hub), "betfinder_tips": len(bf), "copied": len(new),
              "already_there": len(order) - len(new),
              "not_ingested": sum(1 for tid in hub if tid not in bf)}
    log.warning("tips migration: %d row(s) copied into `tips` (%d hub_tips, %d betfinder_tips, "
                "%d not yet ingested); hub_tips and betfinder_tips are left in place, unused",
                counts["copied"], counts["hub_tips"], counts["betfinder_tips"],
                counts["not_ingested"])
    return counts
