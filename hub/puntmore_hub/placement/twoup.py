"""The Bet Placer's 2-Up reader, stage 1: Render's 2-Up feed read, and in dry-run what would
have been placed recorded in the `bets` table (docs/placer-spec.md, "What it reads").

One pass (`read_once`, run by `puntmore placer 2up` and, when switched on, the daemon):

1. Nothing at all when the kill switch is on, or when no account is set
   (`PUNTMORE_PLACER_2UP_ACCOUNT`), or the account is not an enabled entry in sub_clients.yaml,
   or that file cannot be read. The result says which, once; no bet is recorded and nothing is
   escalated.
2. `GET /api/2up/feed?value_only=true` on `PUNTMORE_RENDER_HTTP` with `PUNTMORE_RENDER_API_KEY`
   as a Bearer key (the `accabonus` grant), through `aw26.request`. A refused key or an
   unreachable Render is said plainly in the result, never raised.
3. The board's verdict, the spec's rules 2 and 3: no bet on any row when `available` is false or
   the status says `killed`, `not_yet_refreshed`, `stale` or `cannot_price`. One exception: a
   board that is partly priced (a failed request beside ones that priced) keeps its priced rows,
   as Render's own value view does; a gap naming ALL, or an unreadable rescue table (every row
   priced on the flat fallback rescue), is no-bet. A status that is not an object is no-bet.
4. Each row: skipped when it is not value, its `priced_at` is missing or older than
   `PUNTMORE_PLACER_2UP_MAX_AGE_S` (3900 s: Render's slowest price tier plus the feed's own
   5 min), its kick-off has passed, its bet is already recorded or it repeats a selection
   earlier in the same feed; otherwise a single at the row's price, the flat stake
   `PUNTMORE_PLACER_FLAT_STAKE` (a test setting, not the staking logic), `not_after` the
   kick-off, bet id `2up:<event_id>:<side>`. The engine's idempotence on bet id keeps one
   record per selection however many passes see it.
5. The bets go to the engine one at a time. Before each, the kill switch and the account
   (sub_clients.yaml read again) are checked; if either has gone, the pass stops there
   ("stopped"), so a flip mid-pass never makes a failed row and an escalation per bet left.

Logging is at DEBUG only: the daemon's loop logs a pass when what it says changes.

Nothing here retries, and nothing here places: the engine is given no adapter, so it uses the
dry-run one whatever `PUNTMORE_DRY_RUN` says.
"""
from __future__ import annotations

import http.client
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .. import aw26
from ..config import Settings
from ..schemas import Bet, BetLeg
from ..store import Store, parse_iso
from .adapters.dry_run import DryRunAdapter
from .engine import ClientEngine, load_sub_clients

log = logging.getLogger("puntmore.placement.twoup")

FEED_PATH = "/api/2up/feed?value_only=true"
# The order a no-bet board is judged in; the first one seen is the verdict.
NO_BET_FLAGS = ("killed", "not_yet_refreshed", "stale")


def bet_id_for(event_id: str, side: str) -> str:
    """One selection, one bet id: `2up:<event_id>:<side>`."""
    return f"2up:{event_id}:{side}"


def _result(outcome: str, detail: str, **kw) -> dict:
    # `dry_run`: the engine this reader builds is given no adapter, so the dry-run one.
    return {"outcome": outcome, "detail": detail, "board": None, "seen": 0, "skipped": {},
            "recorded": 0, "statuses": {}, "dry_run": True, "rows": [], **kw}


def accounts_or_problem(cfg: Settings) -> tuple[dict[str, dict], str | None]:
    """sub_clients.yaml read, and why the configured account cannot be used (or None)."""
    try:
        accounts = load_sub_clients(cfg.sub_clients_file)
    except Exception as exc:  # noqa: BLE001 - a bad file is said, never a traceback every pass
        return {}, (f"{cfg.sub_clients_file.name} could not be read ({cfg.sub_clients_file}): "
                    f"{exc.__class__.__name__}: {exc}")[:300]
    return accounts, account_problem(cfg, accounts)


def account_problem(cfg: Settings, accounts: dict[str, dict]) -> str | None:
    """Why a pass cannot record anything for the configured account, or None."""
    acc = (cfg.placer_2up_account or "").strip()
    if not acc:
        return "no account set (PUNTMORE_PLACER_2UP_ACCOUNT is blank)"
    entry = accounts.get(acc)
    if entry is None:
        return f"account {acc} is not in {cfg.sub_clients_file.name} ({cfg.sub_clients_file})"
    if not entry.get("enabled", True):
        return f"account {acc} is disabled in {cfg.sub_clients_file.name}"
    return None


def board_verdict(feed: dict) -> tuple[str, str | None]:
    """("ok" | "partly priced" | "no-bet", why). `why` names the status seen."""
    status = feed.get("status")
    if not isinstance(status, dict):
        return "no-bet", f"status unreadable ({type(status).__name__})"
    raw = status.get("cannot_price") or []
    cannot = [str(x) for x in raw] if isinstance(raw, list) else [str(raw)]
    if not feed.get("available", False):
        return "no-bet", "available: false" + (f" ({cannot[0]})" if cannot else "")
    for flag in NO_BET_FLAGS:
        if status.get(flag):
            extra = f" (served_at {status.get('served_at')})" if flag == "stale" else ""
            return "no-bet", flag + extra
    if cannot:
        last = status.get("last_pass")
        priced = (last.get("priced_fixtures") or 0) if isinstance(last, dict) else 0
        whole = [c for c in cannot if c.startswith("ALL") or c.startswith("rescue table unreadable")]
        if priced and not whole:
            return "partly priced", "cannot_price: " + "; ".join(cannot)
        return "no-bet", "cannot_price: " + "; ".join(whole or cannot)
    if status.get("degraded"):
        return "no-bet", "degraded, with no reason given in cannot_price"
    return "ok", None


def fetch(cfg: Settings, opener: aw26.Opener | None = None) -> tuple[dict | None, str | None, str | None]:
    """(feed, outcome, detail): the feed, or None with "refused" / "unreachable" and why."""
    try:
        code, raw, _ = aw26.request(cfg, "GET", FEED_PATH, opener=opener)
    except (OSError, http.client.HTTPException) as exc:     # URLError, timeout, refused connection
        return None, "unreachable", f"Render could not be reached: {exc.__class__.__name__}: {exc}"[:300]
    if code == 401:
        return None, "refused", "Render did not accept the Hub's key (HTTP 401)"
    if code == 403:
        return None, "refused", "the Hub's key is not granted accabonus (HTTP 403)"
    if code == 404:
        return None, "refused", "Render has no 2-Up feed (HTTP 404; ENABLE_TWOUP off?)"
    if code != 200:
        return None, "refused", f"Render answered HTTP {code}: {aw26._detail(raw)}"[:300]
    try:
        feed = json.loads(raw)
    except ValueError:
        return None, "refused", "Render's answer was not JSON"
    if not isinstance(feed, dict):
        return None, "refused", "Render's answer was not a feed"
    return feed, None, None


def _when(value: Any) -> datetime | None:
    try:
        return parse_iso(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _row_bet(cfg: Settings, row: dict, store: Store, now: datetime) -> tuple[str | None, Bet | str]:
    """(bet id or None, the Bet, or the reason the row is skipped)."""
    event_id, side = str(row.get("event_id") or ""), str(row.get("side") or "")
    try:
        price = float(row.get("price"))
    except (TypeError, ValueError):
        price = 0.0
    kickoff = _when(row.get("kickoff"))
    if not event_id or not side or not row.get("selection") or price <= 1.0 or kickoff is None:
        return None, "bad_row"
    bid = bet_id_for(event_id, side)
    if store.bet_exists(bid):
        return bid, "already_recorded"
    if row.get("value") is not True:
        return bid, "not_value"
    priced_at = _when(row.get("priced_at"))
    if priced_at is None:
        return bid, "no_priced_at"
    if (now - priced_at).total_seconds() > cfg.placer_2up_max_age_s:
        return bid, "stale_price"
    if kickoff <= now:
        return bid, "kicked_off"
    return bid, Bet(
        bet_id=bid, account_id=cfg.placer_2up_account.strip(),
        legs=[BetLeg(event_id=event_id, market=str(row.get("market") or "Match Result"),
                     selection=str(row["selection"]), price=price)],
        stake=float(cfg.placer_flat_stake), expected_price=price, bet_type="single",
        not_after=kickoff.isoformat(timespec="seconds"))


def read_once(cfg: Settings, store: Store, *, opener: aw26.Opener | None = None,
              now: datetime | None = None, engine: ClientEngine | None = None) -> dict:
    """One pass. {"outcome", "detail", "board", "seen", "skipped": {reason: n}, "recorded",
    "statuses": {engine status: n}, "dry_run", "rows": [...]}. The outcome is one of "kill
    switch", "no account", "not configured", "refused", "unreachable", "no-bet", "stopped" (the
    kill switch or the account went mid-pass) or "done". `dry_run` is whether the engine's
    adapter is the dry-run one (always, in this stage)."""
    now = now or datetime.now(timezone.utc)
    if cfg.kill_switch_on():
        return _result("kill switch", f"kill switch on ({cfg.kill_switch_file}): nothing read, nothing recorded")
    accounts, problem = accounts_or_problem(cfg)
    if problem:
        return _result("no account", problem + ": nothing read, nothing recorded")
    if not aw26.configured(cfg):
        return _result("not configured", "PUNTMORE_RENDER_HTTP or PUNTMORE_RENDER_API_KEY is unset")
    feed, outcome, detail = fetch(cfg, opener)
    if feed is None:
        return _result(outcome, detail)
    board, why = board_verdict(feed)
    raw = feed.get("candidates")
    rows = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    if board == "no-bet":
        log.debug("2up: no-bet board: %s (%d row(s) not looked at)", why, len(rows))
        return _result("no-bet", why, board=board, seen=len(rows))
    skipped: Counter = Counter()
    shown, bets, ids = [], [], set()
    for row in rows:
        bid, got = _row_bet(cfg, row, store, now)
        if not isinstance(got, str):
            if bid in ids:
                got = "repeated_in_feed"
            ids.add(bid)
        fate = got if isinstance(got, str) else "new"
        if isinstance(got, str):
            skipped[got] += 1
        else:
            bets.append(got)
        shown.append({"bet_id": bid, "event": row.get("event"), "selection": row.get("selection"),
                      "price": row.get("price"), "priced_at": row.get("priced_at"),
                      "kickoff": row.get("kickoff"), "fate": fate})
    statuses: Counter = Counter()
    fates: dict[str, str] = {}
    stopped = None
    eng = engine or ClientEngine(store, cfg, sub_clients=accounts)
    for bet in bets:
        # Between bets: the kill switch, and the account as sub_clients.yaml has it now.
        if cfg.kill_switch_on():
            stopped = f"kill switch came on ({cfg.kill_switch_file})"
        else:
            eng.sub_clients, problem = accounts_or_problem(cfg)
            stopped = problem
        if stopped:
            break
        fates[bet.bet_id] = eng.handle_bet(bet)
    statuses.update(fates.values())
    for r in shown:
        if r["fate"] == "new":
            r["fate"] = fates.get(r["bet_id"], "not handed")
    recorded = sum(n for s, n in statuses.items() if s != "duplicate")
    detail = (why if board == "partly priced" else "board ok")
    dry = isinstance(eng.effective_adapter(), DryRunAdapter)
    if stopped:
        left = len(bets) - len(fates)
        log.debug("2up: pass stopped: %s; %d bet(s) not handed", stopped, left)
        return _result("stopped", f"{stopped}: stopped with {left} bet(s) not handed; {detail}",
                       board=board, seen=len(rows), skipped=dict(skipped), recorded=recorded,
                       statuses=dict(statuses), dry_run=dry, rows=shown)
    log.debug("2up: %s; %d row(s), %d skipped %s, %d recorded %s", detail, len(rows),
             sum(skipped.values()), dict(skipped), recorded, dict(statuses))
    return _result("done", detail, board=board, seen=len(rows), skipped=dict(skipped),
                   recorded=recorded, statuses=dict(statuses), dry_run=dry, rows=shown)


def summary(result: dict) -> dict:
    """The pass without its rows: what the daemon writes to daemon-status.json."""
    return {k: v for k, v in result.items() if k != "rows"}
