"""Settings for the Hub.

Everything comes from environment variables (a ``.env`` in the repo root is loaded if present).
Nothing operational is hard-coded, so an agent changes behaviour by editing ``.env`` or a
source YAML, never code. See ``.env.example`` for every variable.
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(os.environ.get("PUNTMORE_HOME_ROOT", Path(__file__).resolve().parent.parent))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class Settings:
    # Identity on Render. The Hub is one client of the Render service, with admin scope.
    client_id: str = os.environ.get("PUNTMORE_CLIENT_ID", "owner")
    render_ws_url: str = os.environ.get("PUNTMORE_RENDER_WS", "")
    render_api_key: str = os.environ.get("PUNTMORE_RENDER_API_KEY", "")

    # Safety. Dry-run is ON unless explicitly switched off; the kill switch is a file so it can be
    # flipped with `touch` from anywhere, including by an agent, with no code change.
    dry_run: bool = _env_bool("PUNTMORE_DRY_RUN", True)
    kill_switch_file: Path = ROOT / "data" / "KILL"

    # Paths
    data_dir: Path = ROOT / "data"
    db_path: Path = ROOT / "data" / "home.sqlite"
    raw_dir: Path = ROOT / "data" / "raw"
    sources_dir: Path = ROOT / "sources"
    parsers_dir: Path = ROOT / "parsers"
    sub_clients_file: Path = ROOT / "sub_clients.yaml"
    chrome_profile_dir: Path = Path(os.environ.get("PUNTMORE_CHROME_PROFILE", ROOT / "data" / "chrome-profile"))

    # Scraper hard rules (inherited from the PP scraper handover)
    pages_per_hour: int = int(os.environ.get("PUNTMORE_PAGES_PER_HOUR", "60"))
    pages_per_day: int = int(os.environ.get("PUNTMORE_PAGES_PER_DAY", "400"))
    block_cooloff_hours: int = int(os.environ.get("PUNTMORE_BLOCK_COOLOFF_HOURS", "24"))
    use_real_chrome: bool = _env_bool("PUNTMORE_USE_REAL_CHROME", True)
    headless: bool = _env_bool("PUNTMORE_HEADLESS", False)

    # Alerting and the local AI
    discord_webhook: str = os.environ.get("PUNTMORE_DISCORD_WEBHOOK", "")
    hermes_bin: str = os.environ.get("PUNTMORE_HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
    hermes_profile: str = os.environ.get("PUNTMORE_HERMES_PROFILE", "default")
    escalate_to_local_ai: bool = _env_bool("PUNTMORE_ESCALATE_TO_LOCAL_AI", True)
    alert_dedup_hours: float = float(os.environ.get("PUNTMORE_ALERT_DEDUP_HOURS", "6"))

    timezone: str = os.environ.get("PUNTMORE_TZ", "Europe/London")

    def with_overrides(self, **changes) -> "Settings":
        return dataclasses.replace(self, **changes)

    @property
    def link_configured(self) -> bool:
        return bool(self.render_ws_url and self.render_api_key)

    def kill_switch_on(self) -> bool:
        return self.kill_switch_file.exists()


settings = Settings()
