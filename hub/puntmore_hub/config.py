"""Settings for the Hub.

Everything comes from environment variables (a ``.env`` in ``hub/`` is loaded if present).
Nothing operational is hard-coded, so an agent changes behaviour by editing ``.env``, never
code. See ``.env.example`` for every variable.

Scraping is not configured here: the Scraper (``scraper/``) owns every page load and its
own settings. The Hub only knows where the Scraper's data directory is.
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(os.environ.get("PUNTMORE_HUB_ROOT", Path(__file__).resolve().parent.parent))


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


def _env_first(*names: str, default: str) -> str:
    """The first of ``names`` that is set and non-blank (a renamed variable, then its old name)."""
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and raw.strip():
            return raw
    return default


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
    db_path: Path = ROOT / "data" / "hub.sqlite"
    sub_clients_file: Path = ROOT / "sub_clients.yaml"

    # The Scraper (scraper/) writes its heartbeat, status.json and the hub/ drop folders here:
    # the native service's data dir (in the Docker fallback, the container's /data volume).
    # PUNTMORE_WORKER_DATA / PUNTMORE_WORKER_STALE_MINUTES are the old names, still read for
    # one release when the new ones are unset.
    scraper_data_dir: Path = Path(_env_first("PUNTMORE_SCRAPER_DATA", "PUNTMORE_WORKER_DATA",
                                             default="/opt/puntmore-scraper/data"))
    scraper_stale_minutes: int = int(_env_first("PUNTMORE_SCRAPER_STALE_MINUTES",
                                                "PUNTMORE_WORKER_STALE_MINUTES", default="10"))
    # Where the Scraper's code is (the directory holding the `scraper/` package), for the console's
    # Scraper tab, which changes targets.toml through `scraper.targets` and nothing else. Default:
    # the repo this package was checked out in, so the console and the library are the same commit.
    scraper_src: Path = Path(os.environ.get("PUNTMORE_SCRAPER_SRC") or Path(__file__).resolve().parents[2])

    # Alerting and the local AI
    discord_webhook: str = os.environ.get("PUNTMORE_DISCORD_WEBHOOK", "")
    hermes_bin: str = os.environ.get("PUNTMORE_HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
    hermes_profile: str = os.environ.get("PUNTMORE_HERMES_PROFILE", "default")
    escalate_to_local_ai: bool = _env_bool("PUNTMORE_ESCALATE_TO_LOCAL_AI", True)
    alert_dedup_hours: float = float(os.environ.get("PUNTMORE_ALERT_DEDUP_HOURS", "6"))

    timezone: str = os.environ.get("PUNTMORE_TZ", "Europe/London")

    # The Hub Console (`puntmore web`). Bound to localhost by default: binding to a LAN address
    # exposes the actions to anyone on the LAN, so the token is then required. A blank token
    # disables every action; the read-only views still work.
    web_bind: str = os.environ.get("PUNTMORE_WEB_BIND", "127.0.0.1:8090")
    web_token: str = os.environ.get("PUNTMORE_WEB_TOKEN", "")
    llama_url: str = os.environ.get("PUNTMORE_LLAMA_URL", "http://127.0.0.1:8080")
    # The https base of Render, for its /api/ready health gate. Blank = not configured.
    render_http: str = os.environ.get("PUNTMORE_RENDER_HTTP", "")

    def with_overrides(self, **changes) -> "Settings":
        return dataclasses.replace(self, **changes)

    @property
    def link_configured(self) -> bool:
        return bool(self.render_ws_url and self.render_api_key)

    @property
    def inbox_dir(self) -> Path:
        return self.scraper_data_dir / "hub"

    @property
    def scraper_targets_file(self) -> Path:
        """What the Scraper reads (scraper/config.py `load`, with SCRAPER_DATA_DIR = our data dir)."""
        return self.scraper_data_dir / "targets.toml"

    @property
    def web_host_port(self) -> tuple[str, int]:
        host, _, port = self.web_bind.rpartition(":")
        return (host or "127.0.0.1"), int(port or 8090)

    def kill_switch_on(self) -> bool:
        return self.kill_switch_file.exists()


settings = Settings()
