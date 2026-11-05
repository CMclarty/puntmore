"""Settings (environment) and targets (targets.toml on the Hub).

WHAT is read is config: a list of targets — pages, any mix of Paddy Power and
Bookie Bashing — that changes without a code change. HOW each site is treated
— its budget, quiet hours, what a block means — is code (sites.py), because
those are the guarantees, and a config edit must not be able to loosen them.

The targets are ONE file: data/targets.toml, or the file SCRAPER_TARGETS
names (tests, by hand). It lives on the Hub and Hermes edits it; nothing on
Render configures the Worker (PLAN.md, decision 5 Nov 2026). The runner
checks the file every TARGETS_CHECK_S and applies an edit without a restart.

Every rate limit is a CEILING: config can make the scraper slower or read
less, never faster.
"""
import os
import pathlib
import tomllib
from dataclasses import dataclass, field
from urllib.parse import urlparse

HERE = pathlib.Path(__file__).resolve().parent

# The only hosts a paddypower / bookiebashing target may open. localhost is for
# the tests' fake site. A `generic` target (the Bet Finder's sources) may open
# any host EXCEPT these two: the bookmakers are read only under their own site
# rules, never under the looser generic budget.
ALLOWED_HOSTS = ("paddypower.com", "bookiebashing.net", "localhost", "127.0.0.1")
BOOKMAKER_HOSTS = ("paddypower.com", "bookiebashing.net")


def _on(host: str, hosts) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def host_allowed(url: str, site: str | None = None) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if site == "generic":
        return bool(host) and not _on(host, BOOKMAKER_HOSTS)
    return _on(host, ALLOWED_HOSTS)


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
    mode: str = "keep"                  # keep: page stays open (Paddy/BB) | fetch: load, parse, close
    enabled: bool = True
    min_rows: int = 0                   # fewer parsed rows than this = unhealthy (layout change?)
    trust_weight: float = 1.0           # Bet Finder: sent to the Hub beside the rows
    wait_for: str | None = None         # CSS selector to wait for before parsing (fetch targets)


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
    targets_file: pathlib.Path | None = None   # re-read while running when it changes
    capture_tab_mb: float = 8.0         # each tab's share of capture_mb (disk samples)


def _target(t: dict) -> Target:
    from .sites import SITES
    name, site, url = t["name"], t["site"], t["url"]
    if site not in SITES:
        raise ValueError(f"target {name}: unknown site {site!r} (have {', '.join(SITES)})")
    if not host_allowed(url, site):
        raise ValueError(f"target {name}: {url} is not on an allowed host for site {site!r}")
    mode = t.get("mode") or ("fetch" if site == "generic" else "keep")
    if mode not in ("keep", "fetch"):
        raise ValueError(f"target {name}: mode must be keep or fetch, not {mode!r}")
    m = t.get("matches")
    matches = None
    if m is not None and site == "generic":
        raise ValueError(f"target {name}: a generic target cannot have [target.matches]")
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
        mode=mode, enabled=bool(t.get("enabled", True)),
        min_rows=int(_clamp(int(t.get("min_rows", 0)), lo=0)),
        trust_weight=_clamp(float(t.get("trust_weight", 1.0)), 0.0, 1.0),
        wait_for=t.get("wait_for") or None,
    )


def load_targets(path: pathlib.Path) -> list[Target]:
    if not path.exists():
        return []
    raw = tomllib.loads(path.read_text())
    targets = [_target(t) for t in raw.get("target", [])]
    names = [t.name for t in targets]           # a disabled target still reserves its name
    targets = [t for t in targets if t.enabled]
    if len(names) != len(set(names)):
        raise ValueError("target names must be unique")
    return targets


def load() -> Settings:
    data = pathlib.Path(os.environ.get("SCRAPER_DATA_DIR", HERE / "data"))
    targets_file = pathlib.Path(os.environ.get("SCRAPER_TARGETS") or data / "targets.toml")
    return Settings(
        data_dir=data,
        profile_dir=pathlib.Path(os.environ.get("SCRAPER_PROFILE_DIR", data / "profile")),
        channel=os.environ.get("SCRAPER_CHANNEL", "chrome") or None,
        alert_webhook=os.environ.get("SCRAPER_ALERT_WEBHOOK") or None,
        healthcheck_url=os.environ.get("SCRAPER_HEALTHCHECK_URL") or None,
        puntmore_url=os.environ.get("PUNTMORE_URL") or "https://puntmore.com",
        ingest_key=os.environ.get("SAVES_INGEST_KEY") or None,
        capture_mb=_clamp(float(os.environ.get("SCRAPER_CAPTURE_MB", 200)), hi=2000.0),
        targets=load_targets(targets_file),
        targets_file=targets_file,
        capture_tab_mb=_clamp(float(os.environ.get("SCRAPER_CAPTURE_TAB_MB", 8)), hi=100.0),
    )
