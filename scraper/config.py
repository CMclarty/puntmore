"""Settings (environment) and targets (targets.toml on the Hub).

WHAT is read is config: a list of targets — pages, any mix of Paddy Power,
Bookie Bashing and generic sources (Finder's, and any other page a tool needs) — that changes without a code change. HOW each site is treated
— its budget, quiet hours, what a block means — is code (sites.py), because
those are the guarantees, and a config edit must not be able to loosen them.

The targets are ONE file: data/targets.toml, or the file SCRAPER_TARGETS
names (tests, by hand). It lives on the Hub and HubAI edits it (through targets.py); nothing on
Render configures the Scraper (docs/decisions.md, 5 Nov 2026). The runner
checks the file every TARGETS_CHECK_S and applies an edit without a restart.

Every rate limit is a CEILING: config can make the scraper slower or read
less, never faster.
"""
import os
import pathlib
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

HERE = pathlib.Path(__file__).resolve().parent

# The only hosts a paddypower / bookiebashing target may open. localhost is for
# the tests' fake site. A `generic` target (Finder's sources) may open
# any host EXCEPT these two: the bookmakers are read only under their own site
# rules, never under the looser generic budget.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")   # a target's `days`, in week order

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
    """A listing page whose match links are each opened in a kept-open tab.
    `listings`: further listing pages (other competitions) for the same job,
    each kept open in its own tab; their match links are interleaved with the
    url's, under the one max_tabs (Callum, 12 Nov 2026)."""
    link_re: str = r"-v-"
    max_tabs: int = 3
    market_tabs: tuple[str, ...] = ()   # in-page tabs clicked once on arrival
    open_gap_min: float = 4.0           # at least this long between opening tabs
    tab_max_hours: float = 8.0          # retire a match tab after this long
    relist_min: float = 60.0            # reload the listing to find new matches
    listings: tuple[str, ...] = ()      # further listing pages, after the target's url


@dataclass(frozen=True)
class Target:
    name: str
    site: str                           # "paddypower" | "bookiebashing"
    url: str
    every_min: float | None = None      # reload on this interval; None = only when stale
    stale_min: float = 20.0             # reload a page that has gone this quiet
    parser: str | None = None           # name in parsers.PARSERS
    post: str | None = None             # Render route the Hub posts these price rows to (the Scraper only writes it in the drop file)
    matches: Matches | None = None
    mode: str = "keep"                  # keep: page stays open (Paddy/BB) | fetch: load, parse, close
    enabled: bool = True
    min_rows: int = 0                   # fewer parsed rows than this = unhealthy (layout change?)
    trust_weight: float = 1.0           # Finder: sent to the Hub beside the rows, for HubAI
    wait_for: str | None = None         # CSS selector to wait for before parsing (fetch targets)
    # Acted on by the runner (schedule.py): higher priority wins a site's
    # contended hourly budget, and outside its window ("HH:MM-HH:MM", local
    # time) a target is not loaded and its tabs are closed.
    priority: int = 0
    window: str | None = None
    # The kick-off sweep (schedule.py): while a kick-off the target's own rows
    # report is within near_hours, it reloads (or runs) every near_min instead
    # of every_min, and goes first among equals when a site's budget is short.
    near_min: float | None = None
    near_hours: float = 3.0
    # When the job runs at all (schedule.inactive): only on these `days` (none
    # = every day), and not after `until` ("YYYY-MM-DD", the last day it runs).
    # A window past midnight belongs to the day it started.
    days: tuple[str, ...] = ()
    until: str | None = None


@dataclass(frozen=True)
class Settings:
    data_dir: pathlib.Path
    profile_dir: pathlib.Path
    channel: str | None
    alert_webhook: str | None
    healthcheck_url: str | None
    capture_mb: float
    targets: list[Target] = field(default_factory=list)
    targets_file: pathlib.Path | None = None   # re-read while running when it changes
    capture_tab_mb: float = 8.0         # each tab's share of capture_mb (disk samples)


_WINDOW = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


def _window(name: str, w) -> str | None:
    """"HH:MM-HH:MM" in local time; may wrap midnight ("22:00-02:00")."""
    if w is None or w == "":
        return None
    if not isinstance(w, str) or not _WINDOW.match(w):
        raise ValueError(f"target {name}: window must be \"HH:MM-HH:MM\" (24 h, local time), not {w!r}")
    start, end = w.split("-")
    if start == end:
        raise ValueError(f"target {name}: window {w!r} is empty (start equals end)")
    return w


def _priority(name: str, p) -> int:
    if isinstance(p, bool) or not isinstance(p, int):
        raise ValueError(f"target {name}: priority must be a whole number, not {p!r}")
    return p


def _days_refused(name: str, days) -> ValueError:
    """The one refusal for a bad `days` (targets.py raises it too)."""
    return ValueError(f"target {name}: days must be a list of {', '.join(WEEKDAYS)} with no repeats, not {days!r}")


def _days(name: str, days) -> tuple[str, ...]:
    """The run days, in the order given; none = every day."""
    if days is None:
        return ()
    if (not isinstance(days, (list, tuple)) or not all(isinstance(d, str) and d in WEEKDAYS for d in days)
            or len(set(days)) != len(days)):
        raise _days_refused(name, days)
    return tuple(days)


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _until(name: str, until) -> str | None:
    """The last day the job runs, "YYYY-MM-DD"; none = forever."""
    if until is None or until == "":
        return None
    try:
        if not isinstance(until, str) or not _DATE.match(until):
            raise ValueError
        date.fromisoformat(until)
    except ValueError:
        raise ValueError(f"target {name}: until must be a date \"YYYY-MM-DD\", not {until!r}") from None
    return until


MAX_LISTINGS = 8                        # further listing pages one matches job may keep open


def _listings(name: str, site: str, url: str, listings) -> tuple[str, ...]:
    """A matches job's further listing pages: each on an allowed host for
    the site (as url is), none repeating url or another, at most MAX_LISTINGS."""
    if listings is None:
        return ()
    if not isinstance(listings, (list, tuple)) or not all(isinstance(u, str) and u for u in listings):
        raise ValueError(f"target {name}: matches.listings must be a list of page urls, not {listings!r}")
    if len(listings) > MAX_LISTINGS:
        raise ValueError(f"target {name}: matches.listings may have at most {MAX_LISTINGS} pages, "
                         f"not {len(listings)}")
    for u in listings:
        if not host_allowed(u, site):
            raise ValueError(f"target {name}: listing {u} is not on an allowed host for site {site!r}")
        if u == url:
            raise ValueError(f"target {name}: listing {u} repeats url")
    if len(set(listings)) != len(listings):
        raise ValueError(f"target {name}: matches.listings names a page more than once")
    return tuple(listings)


def _target(t: dict) -> Target:
    from .sinks import PRICE_ROUTES
    from .sites import SITES
    name, site, url = t["name"], t["site"], t["url"]
    post = t.get("post")
    if post is not None and not (isinstance(post, str) and post.startswith("hub:")) and post not in PRICE_ROUTES:
        raise ValueError(f"target {name}: post must be \"hub:<channel>\" or one of {', '.join(PRICE_ROUTES)}, not {post!r}")
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
            listings=_listings(name, site, url, m.get("listings")),
        )
    every = t.get("every_min")
    return Target(
        name=name, site=site, url=url,
        every_min=None if every is None else _clamp(float(every), lo=5.0),
        stale_min=_clamp(float(t.get("stale_min", 20.0)), lo=10.0),
        parser=t.get("parser"), post=post, matches=matches,
        mode=mode, enabled=bool(t.get("enabled", True)),
        min_rows=int(_clamp(int(t.get("min_rows", 0)), lo=0)),
        trust_weight=_clamp(float(t.get("trust_weight", 1.0)), 0.0, 1.0),
        wait_for=t.get("wait_for") or None,
        priority=_priority(name, t.get("priority", 0)),
        window=_window(name, t.get("window")),
        near_min=None if t.get("near_min") is None else _clamp(float(t["near_min"]), lo=5.0),
        near_hours=_clamp(float(t.get("near_hours", 3.0)), lo=0.5, hi=48.0),
        days=_days(name, t.get("days")),
        until=_until(name, t.get("until")),
    )


def load_targets(path: pathlib.Path, include_disabled: bool = False) -> list[Target]:
    """The targets the Scraper runs. include_disabled keeps the `enabled =
    false` ones too (scraper/targets.py, which rewrites the whole file)."""
    if not path.exists():
        return []
    raw = tomllib.loads(path.read_text())
    targets = [_target(t) for t in raw.get("target", [])]
    names = [t.name for t in targets]           # a disabled target still reserves its name
    if len(names) != len(set(names)):
        raise ValueError("target names must be unique")
    return targets if include_disabled else [t for t in targets if t.enabled]


def load() -> Settings:
    data = pathlib.Path(os.environ.get("SCRAPER_DATA_DIR", HERE / "data"))
    targets_file = pathlib.Path(os.environ.get("SCRAPER_TARGETS") or data / "targets.toml")
    return Settings(
        data_dir=data,
        profile_dir=pathlib.Path(os.environ.get("SCRAPER_PROFILE_DIR", data / "profile")),
        channel=os.environ.get("SCRAPER_CHANNEL", "chrome") or None,
        alert_webhook=os.environ.get("SCRAPER_ALERT_WEBHOOK") or None,
        healthcheck_url=os.environ.get("SCRAPER_HEALTHCHECK_URL") or None,
        capture_mb=_clamp(float(os.environ.get("SCRAPER_CAPTURE_MB", 200)), hi=2000.0),
        targets=load_targets(targets_file),
        targets_file=targets_file,
        capture_tab_mb=_clamp(float(os.environ.get("SCRAPER_CAPTURE_TAB_MB", 8)), hi=100.0),
    )
