"""The 2-Up engine, as the Hub link sees it — a thin adapter.

The engine (branch `twoup-engine`) provides `app/twoup/ledger.py` with:

    record_bet_list(db, client_id, bets)
    apply_report(db, client_id, report)          # report = a Report body (PROTOCOL.md)
    open_exposure(db, client_id, account_id)
    pending_bet_lists(db, client_id) -> list[dict]
    mark_sent(db, client_id, bet_ids)

This module imports it LAZILY and, while the package is absent, falls back to
in-memory stubs so the link can be built and tested on its own. With the stubs
nothing is ever pending unless a test records it, so a deploy without the
engine sends no bets.

What the link assumes of `pending_bet_lists`: each dict is one bet list,
`{"bets": [Bet, ...], ...}` (Bet as in PROTOCOL.md; other keys are ignored),
sent as ONE `bet_list` envelope. A bare Bet dict (one with `bet_id` and no
`bets`) is tolerated too: all of those in one poll go out as one list. A list
keeps coming back until `mark_sent` is called with its bet_ids, which the link
does only once the Hub has acked the frame.

The link never decides what to bet. It forwards what the engine lists and
reports what the Hub says.
"""
from __future__ import annotations

import importlib
import logging
import threading
from collections import deque

log = logging.getLogger(__name__)

LEDGER_MODULE = "app.twoup.ledger"
FUNCTIONS = ("record_bet_list", "apply_report", "open_exposure", "pending_bet_lists", "mark_sent")

_ledger = None
_lock = threading.Lock()


class StubLedger:
    """In-memory stand-in for `app.twoup.ledger`. Bounded: at most MAX_* of
    each thing is kept, oldest dropped."""

    MAX_LISTS = 1000
    MAX_REPORTS = 1000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.lists: dict[str, deque] = {}
            self.reports: deque = deque(maxlen=self.MAX_REPORTS)
            self.sent: dict[str, set] = {}

    def record_bet_list(self, db, client_id, bets):
        with self._lock:
            self.lists.setdefault(client_id, deque(maxlen=self.MAX_LISTS)).append(
                {"bets": [dict(b) for b in bets]})

    def apply_report(self, db, client_id, report):
        with self._lock:
            self.reports.append((client_id, dict(report)))

    def open_exposure(self, db, client_id, account_id):
        return 0.0

    def pending_bet_lists(self, db, client_id):
        with self._lock:
            sent = self.sent.get(client_id, set())
            out = []
            for lst in self.lists.get(client_id, ()):
                bets = [b for b in lst["bets"] if b["bet_id"] not in sent]
                if bets:
                    out.append({"bets": bets})
            return out

    def mark_sent(self, db, client_id, bet_ids):
        with self._lock:
            self.sent.setdefault(client_id, set()).update(bet_ids)


stub = StubLedger()


def _load():
    """The real ledger module, or the stub when the engine is not installed.
    Only the engine package being ABSENT selects the stub: an ImportError from
    inside a present ledger is a bug and is raised."""
    global _ledger
    if _ledger is not None:
        return _ledger
    with _lock:
        if _ledger is None:
            try:
                mod = importlib.import_module(LEDGER_MODULE)
            except ModuleNotFoundError as exc:
                if exc.name not in ("app.twoup", LEDGER_MODULE):
                    raise
                log.warning("2-Up engine (%s) not installed: the Hub link runs on "
                            "in-memory stubs and sends no bets", LEDGER_MODULE)
                mod = stub
            else:
                missing = [f for f in FUNCTIONS if not hasattr(mod, f)]
                if missing:
                    raise ImportError(f"{LEDGER_MODULE} lacks {', '.join(missing)}")
            _ledger = mod
    return _ledger


def using_stub() -> bool:
    return _load() is stub


def use(ledger) -> None:
    """Point the adapter at a given ledger (tests); None re-resolves."""
    global _ledger
    _ledger = ledger


def record_bet_list(db, client_id: str, bets: list[dict]) -> None:
    return _load().record_bet_list(db, client_id, bets)


def apply_report(db, client_id: str, report: dict) -> None:
    return _load().apply_report(db, client_id, report)


def open_exposure(db, client_id: str, account_id: str):
    return _load().open_exposure(db, client_id, account_id)


def pending_bet_lists(db, client_id: str) -> list[dict]:
    return list(_load().pending_bet_lists(db, client_id) or [])


def mark_sent(db, client_id: str, bet_ids: list[str]) -> None:
    return _load().mark_sent(db, client_id, bet_ids)
