"""Settings (environment) and targets (Puntmore's /scraper page, or targets.toml).

WHAT is read is config: a list of targets — pages, any mix of Paddy Power and
Bookie Bashing — that changes without a code change. HOW each site is treated
— its budget, quiet hours, what a block means — is code (sites.py), because
those are the guarantees, and a config edit must not be able to loosen them.

Where the targets come from, first that works:
  1. SCRAPER_TARGETS set           -> that toml file, always (tests, by hand)
  2. PUNTMORE_URL + SAVES_INGEST_KEY -> GET /api/scraper/config, cached to
                                      data/config.json
  3. the cache, data/config.json   -> server unreachable
  4. data/targets.toml             -> neither (offline)
The server serves the same structure targets.toml holds, so both go through
`_target`. The runner re-fetches every CONFIG_EVERY_S and applies a change
without a restart.

Every rate limit is a CEILING: config can make the scraper slower or read
less, never faster.
"""
import json
import os
import pathlib
import tomllib
from dataclasses import dataclass, field
from urllib.parse import urlparse

HERE = pathlib.Path(__file__).resolve().parent

# The only hosts a page may be opened on. localhost is for the tests' fake site.
ALLOWED_HOSTS = ("paddypower.com", "bookiebashing.net", "localhost", "127.0.0.1")


def host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _clamp(v, lo=None, hi=None):
    if lo is not None:
        v = max(v, lo)
    if hi is not None:
        v = min(v, hi)
    return v


@dataclass(frozen=True)
class Matches:
    """A listing page whose match links are each opened in a kept-open tab."""
    link_re: str = r"-v-"
    max_tabs: int = 3
    market_tabs: tuple[str, ...] = ()   # in-page tabs clicked once on arrival
    open_gap_min: float = 4.0           # at least this long between opening tabs
    tab_max_hours: float = 8.0          # retire a match tab after this long
    relist_min: float = 60.0            # reload the listing to find new matches


@dataclass(frozen=True)
class Target:
    name: str
    site: str                           # "paddypower" | "bookiebashing"
    url: str
    every_min: float | None = None      # reload on this interval; None = only when stale
    stale_min: float = 20.0             # reload a page that has gone this quiet
    parser: str | None = None           # name in parsers.PARSERS
    post: str | None = None             # puntmore path to POST {"prices": rows} to
    matches: Matches | None = None


@dataclass(frozen=True)
class Settings:
    data_dir: pathlib.Path
    profile_dir: pathlib.Path
    channel: str | None
    alert_webhook: str | None
    healthcheck_url: str | None
    puntmore_url: str
    ingest_key: str | None
    capture_mb: float
    targets: list[Target] = field(default_factory=list)
    from_server: bool = False           # targets come from Puntmore, re-fetched while running
    targets_source: str = "toml"        # server | cache | toml
    capture_tab_mb: float = 8.0         # each tab's share of capture_mb (disk samples)


def _target(t: dict) -> Target:
    from .sites import SITES
    name, site, url = t["name"], t["site"], t["url"]
    if site not in SITES:
        raise ValueError(f"target {name}: unknown site {site!r} (have {', '.join(SITES)})")
    if not host_allowed(url):
        raise ValueError(f"target {name}: {url} is not on an allowed host")
    m = t.get("matches")
    matches = None
    if m is not None:
        matches = Matches(
            link_re=m.get("link_re", Matches.link_re),
            max_tabs=int(_clamp(m.get("max_tabs", 3), 0, 6)),
            market_tabs=tuple(m.get("market_tabs", ())),
            open_gap_min=_clamp(float(m.get("open_gap_min", 4.0)), lo=2.0),
            tab_max_hours=_clamp(float(m.get("tab_max_hours", 8.0)), hi=24.0),
            relist_min=_clamp(float(m.get("relist_min", 60.0)), lo=30.0),
        )
    every = t.get("every_min")
    return Target(
        name=name, site=site, url=url,
        every_min=None if every is None else _clamp(float(every), lo=5.0),
        stale_min=_clamp(float(t.get("stale_min", 20.0)), lo=10.0),
        parser=t.get("parser"), post=t.get("post"), matches=matches,
    )


def load_targets(path: pathlib.Path) -> list[Target]:
    if not path.exists():
        return []
    raw = tomllib.loads(path.read_text())
    targets = [_target(t) for t in raw.get("target", [])]
    names = [t.name for t in targets]
    if len(names) != len(set(names)):
        raise ValueError("target names must be unique")
    return targets


def targets_from(raw: list[dict]) -> list[Target]:
    """Targets from the server's list. Lenient where load_targets is strict:
    one bad target is skipped and named, rather than stopping every other."""
    out, names = [], set()
    for t in raw:
        try:
            target = _target(t)
        except (KeyError, TypeError, ValueError) as exc:
            print(f"config: skipped target {t.get('name') if isinstance(t, dict) else t!r}: {exc}",
                  flush=True)
            continue
        if target.name in names:
            print(f"config: skipped duplicate target {target.name}", flush=True)
            continue
        names.add(target.name)
        out.append(target)
    return out


CONFIG_PATH = "/api/scraper/config"
CACHE_NAME = "config.json"


def fetch_server(puntmore_url: str, key: str, data_dir: pathlib.Path,
                 timeout: float = 15) -> list[Target]:
    """GET the targets from Puntmore and cache the answer. Raises on any
    failure (network, HTTP status, shape) so the caller can fall back."""
    import httpx
    r = httpx.get(puntmore_url.rstrip("/") + CONFIG_PATH, headers={"X-API-Key": key},
                  timeout=timeout)
    r.raise_for_status()
    body = r.json()
    raw = body["targets"]
    if not isinstance(raw, list):
        raise ValueError("config: targets is not a list")
    targets = targets_from(raw)
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / (CACHE_NAME + ".tmp")
    tmp.write_text(json.dumps(body, indent=1))
    tmp.replace(data_dir / CACHE_NAME)
    return targets


def load_cache(data_dir: pathlib.Path) -> list[Target] | None:
    path = data_dir / CACHE_NAME
    if not path.exists():
        return None
    try:
        return targets_from(json.loads(path.read_text())["targets"])
    except (ValueError, KeyError, TypeError) as exc:
        print(f"config: cache unreadable ({exc})", flush=True)
        return None


def load() -> Settings:
    data = pathlib.Path(os.environ.get("SCRAPER_DATA_DIR", HERE / "data"))
    forced = os.environ.get("SCRAPER_TARGETS")
    targets_file = pathlib.Path(forced or data / "targets.toml")
    puntmore_url = os.environ.get("PUNTMORE_URL") or ""
    key = os.environ.get("SAVES_INGEST_KEY") or None
    from_server = bool(not forced and puntmore_url and key)

    targets, source = None, "toml"
    if from_server:
        try:
            targets, source = fetch_server(puntmore_url, key, data), "server"
        except Exception as exc:
            print(f"config: server unreachable ({exc.__class__.__name__}: {exc}); "
                  "using the cache", flush=True)
            targets, source = load_cache(data), "cache"
    if targets is None:
        targets, source = load_targets(targets_file), "toml"

    return Settings(
        data_dir=data,
        profile_dir=pathlib.Path(os.environ.get("SCRAPER_PROFILE_DIR", data / "profile")),
        channel=os.environ.get("SCRAPER_CHANNEL", "chrome") or None,
        alert_webhook=os.environ.get("SCRAPER_ALERT_WEBHOOK") or None,
        healthcheck_url=os.environ.get("SCRAPER_HEALTHCHECK_URL") or None,
        puntmore_url=puntmore_url or "https://puntmore.com",
        ingest_key=key,
        capture_mb=_clamp(float(os.environ.get("SCRAPER_CAPTURE_MB", 200)), hi=2000.0),
        targets=targets,
        from_server=from_server,
        targets_source=source,
        capture_tab_mb=_clamp(float(os.environ.get("SCRAPER_CAPTURE_TAB_MB", 8)), hi=100.0),
    )
