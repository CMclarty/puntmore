"""The client engine."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .. import escalation, schemas
from ..config import Settings, settings as default_settings
from ..schemas import Bet
from ..store import Store
from .adapters.base import PlacementAdapter, PlacementFailed, PriceChanged
from .adapters.dry_run import DryRunAdapter

log = logging.getLogger("puntmore.placement")


def load_sub_clients(path: Path) -> dict[str, dict]:
    """sub_clients.yaml: list of accounts keyed by account_id. Missing file = no accounts."""
    if not Path(path).exists():
        return {}
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    out = {}
    for entry in data.get("sub_clients", []):
        out[str(entry["account_id"])] = entry
    return out


class ClientEngine:
    def __init__(self, store: Store, cfg: Settings = default_settings, adapter: PlacementAdapter | None = None,
                 sub_clients: dict[str, dict] | None = None, enqueue=None, notify: bool = True):
        self.store = store
        self.cfg = cfg
        self.adapter = adapter or DryRunAdapter()
        self.sub_clients = sub_clients if sub_clients is not None else load_sub_clients(cfg.sub_clients_file)
        self.enqueue = enqueue or (lambda type_, body: store.outbox_add(type_, body))
        self.notify = notify

    def _report(self, rep: schemas.Report) -> None:
        self.enqueue("report", rep.to_dict())

    def handle_bet_list(self, bets: list[Bet]) -> list[str]:
        """Process a bet list. Returns the status assigned to each bet, in order."""
        statuses = []
        for bet in bets:
            statuses.append(self.handle_bet(bet))
        return statuses

    def handle_bet(self, bet: Bet) -> str:
        if self.store.bet_exists(bet.bet_id):
            log.info("bet %s already seen; ignoring duplicate", bet.bet_id)
            return "duplicate"
        self.store.upsert_bet(bet.bet_id, bet.account_id, bet.to_dict(), "received")

        if self.cfg.kill_switch_on():
            return self._fail(bet, "kill switch on", escalate_kind="kill_switch")
        account = self.sub_clients.get(bet.account_id)
        if account is None:
            return self._fail(bet, f"unknown account_id {bet.account_id}")
        if not account.get("enabled", True):
            return self._fail(bet, f"account {bet.account_id} disabled")

        adapter = self.adapter if not self.cfg.dry_run else (self.adapter if isinstance(self.adapter, DryRunAdapter) else DryRunAdapter())
        self.store.set_bet_status(bet.bet_id, "placing")
        try:
            result = adapter.place(bet, account)
        except PriceChanged as exc:
            self.store.set_bet_status(bet.bet_id, "price_changed", actual_price=exc.new_price)
            self._report(schemas.price_changed(bet.bet_id, exc.new_price))
            return "price_changed"
        except PlacementFailed as exc:
            return self._fail(bet, str(exc))
        except Exception as exc:  # noqa: BLE001
            return self._fail(bet, f"adapter error: {exc!r}")

        self.store.set_bet_status(bet.bet_id, "placed", actual_price=result.actual_price)
        self._report(schemas.placed(bet.bet_id, result.actual_price, dry_run=self.cfg.dry_run))
        return "placed"

    def _fail(self, bet: Bet, reason: str, escalate_kind: str = "failed_bet") -> str:
        self.store.set_bet_status(bet.bet_id, "failed")
        self._report(schemas.failed(bet.bet_id, reason))
        escalation.raise_escalation(self.store, escalate_kind, bet.bet_id,
                                    f"bet {bet.bet_id} on {bet.account_id} ({bet.bet_type}, stake {bet.stake}) failed: {reason}",
                                    cfg=self.cfg, notify=self.notify)
        return "failed"

    def report_balance(self, account_id: str, amount: float) -> None:
        self.store.add_balance(account_id, amount)
        self._report(schemas.balance(account_id, amount))

    def report_settled(self, bet_id: str, outcome: str, return_amount: float) -> None:
        self.store.set_bet_status(bet_id, "settled")
        self._report(schemas.settled(bet_id, outcome, return_amount))
