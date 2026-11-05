from __future__ import annotations

from pathlib import Path

import pytest

from puntmore_hub.config import Settings
from puntmore_hub.store import Store

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(
        client_id="test",
        render_ws_url="", render_api_key="",
        dry_run=True,
        kill_switch_file=tmp_path / "KILL",
        data_dir=tmp_path, db_path=tmp_path / "hub.sqlite",
        sub_clients_file=tmp_path / "sub_clients.yaml",
        worker_data_dir=tmp_path / "worker",
        discord_webhook="", hermes_bin="", escalate_to_local_ai=False,
    )


@pytest.fixture
def store() -> Store:
    return Store(":memory:")
