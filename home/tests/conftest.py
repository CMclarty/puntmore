from __future__ import annotations

from pathlib import Path

import pytest

from puntmore_home.config import Settings
from puntmore_home.store import Store

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(
        client_id="test",
        render_ws_url="", render_api_key="",
        dry_run=True,
        kill_switch_file=tmp_path / "KILL",
        data_dir=tmp_path, db_path=tmp_path / "home.sqlite", raw_dir=tmp_path / "raw",
        sources_dir=REPO / "sources", parsers_dir=REPO / "parsers",
        sub_clients_file=tmp_path / "sub_clients.yaml", chrome_profile_dir=tmp_path / "chrome",
        discord_webhook="", hermes_bin="", escalate_to_local_ai=False,
    )


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def fixture_html() -> str:
    return (REPO / "fixtures" / "example_tipster" / "page.html").read_text()
