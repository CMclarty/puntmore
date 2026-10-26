"""/api/scraper — the scraper's control plane.

The scraper (top-level `scraper/`, its own Docker image, on its own box) is a
worker: it asks GET /scraper/config what to read, and reports POST
/scraper/status. Both are key-guarded with the key the box already has
(SAVES_INGEST_KEY). The page at /scraper reads and edits the same rows,
session-guarded by the `scraper` grant (main.py).

WHAT is read is here: leagues (competition listings), markets (what is read on
a site, and where its rows are posted) and per-site timings. HOW each site is
treated — budgets, quiet hours, what a block means — stays in the scraper's
`sites.py`. Timings are clamped to the scraper's own floors
(`scraper/config.py::_target`) on write, so an edit here can make it slower,
never faster than the code allows.

GET /scraper/config serves `targets` in the SAME shape `config._target` reads
from targets.toml, so the toml and the server are two sources of one
structure: enabled leagues x enabled markets of that site (a "matches" target
each), plus every enabled market with its own `url` (a page target).
"""
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    ScraperLeague, ScraperMarket, ScraperSiteStatus, ScraperTargetStatus, ScraperTiming,
)
from ..schemas import ScraperConfigOut, ScraperStateOut, ScraperStatusAckOut
from ..time_utils import utcnow
from .saves_ingest import require_ingest_key

router = APIRouter(prefix="/scraper", tags=["scraper"])          # session + `scraper` grant
worker_router = APIRouter(prefix="/scraper", tags=["scraper"],   # the scraper box
                          dependencies=[Depends(require_ingest_key)])

# The hosts a site's pages may be on. The scraper refuses any other host
# (config.ALLOWED_HOSTS); refusing here too means a typo is caught on the page
# instead of the scraper dropping the target.
SITE_HOSTS = {"paddypower": "paddypower.com", "bookiebashing": "bookiebashing.net"}
SITE_PREFIX = {"paddypower": "pp", "bookiebashing": "bb"}
LINK_RE = {"paddypower": "-v-"}       # a match link on a competition listing

# (lo, hi) per timing — the scraper's own clamps (config._target) and nothing
# looser. tab_max_hours also has a floor here: a tab retired every few minutes
# is just more page loads.
CLAMPS = {
    "every_min": (5.0, None),
    "stale_min": (10.0, None),
    "max_tabs": (0, 6),
    "open_gap_min": (2.0, None),
    "relist_min": (30.0, None),
    "tab_max_hours": (0.5, 24.0),
}

DEFAULT_LEAGUES = [
    ("EPL", "paddypower", "https://www.paddypower.com/football/english-premier-league"),
]
DEFAULT_MARKETS = [
    # key, label, site, tabs, parser, post
    ("most", "Most corners / cards", "paddypower", ["Corners", "Cards"], "pp_most",
     "/api/paddy/most-prices"),
    # Bookie Bashing has no saves page: the fair is read by driving its Bet
    # Builder (scraper/parsers/bb_saves.py), so that is the page.
    ("bb_saves", "Saves fair", "bookiebashing", [], "bb_saves", "/api/paddy/saves-fair"),
]
BB_BUILDER_URL = "https://www.bookiebashing.net/tools/betbuilder/"
DEFAULT_TIMINGS = {
    "paddypower": dict(every_min=None, stale_min=20.0, max_tabs=3, open_gap_min=4.0,
                       relist_min=60.0, tab_max_hours=8.0),
    # No timed reload: the builder is driven in place and its driver keeps the
    # page active, and every reload costs the builder's ~60 MB data pull.
    "bookiebashing": dict(every_min=None, stale_min=20.0, max_tabs=3, open_gap_min=4.0,
                          relist_min=60.0, tab_max_hours=8.0),
}


def ensure_seeded(db: Session) -> None:
    """Fill each table with the scraper's current setup when it is empty.
    Idempotent, so it runs at startup and before every read."""
    changed = False
    if db.query(ScraperLeague).first() is None:
        for name, site, url in DEFAULT_LEAGUES:
            db.add(ScraperLeague(name=name, site=site, url=url, enabled=True))
        changed = True
    if db.query(ScraperMarket).first() is None:
        for key, label, site, tabs, parser, post in DEFAULT_MARKETS:
            db.add(ScraperMarket(key=key, label=label, site=site, tabs=json.dumps(tabs),
                                 parser=parser, post=post, enabled=True,
                                 url=BB_BUILDER_URL if key == "bb_saves" else None))
        changed = True
    # Seeded before the builder was known to be the page (26 Oct 2026): fill it
    # in once rather than leave the market off until someone pastes it.
    bb = db.query(ScraperMarket).filter(ScraperMarket.key == "bb_saves").first()
    if bb is not None and not bb.url:
        bb.url = BB_BUILDER_URL
        changed = True
    have = {t.site for t in db.query(ScraperTiming).all()}
    for site, t in DEFAULT_TIMINGS.items():
        if site not in have:
            db.add(ScraperTiming(site=site, **t))
            changed = True
    if changed:
        db.commit()


def clamp(field: str, v):
    if v is None:
        return None
    lo, hi = CLAMPS[field]
    v = int(v) if field == "max_tabs" else float(v)
    if lo is not None:
        v = max(v, lo)
    if hi is not None:
        v = min(v, hi)
    return v


def _check_url(site: str, url: str) -> str:
    url = url.strip()
    host = (urlparse(url).hostname or "").lower()
    want = SITE_HOSTS.get(site)
    if (urlparse(url).scheme != "https" or want is None
            or not (host == want or host.endswith("." + want))):
        raise HTTPException(422, f"{site} pages must be https on {want}")
    return url


def _slug(s: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in s.lower()).split())


def build_config(db: Session) -> dict:
    """The targets the scraper runs, in config._target's shape, plus timings."""
    timings = {t.site: t for t in db.query(ScraperTiming).all()}
    leagues = db.query(ScraperLeague).filter(ScraperLeague.enabled.is_(True)) \
        .order_by(ScraperLeague.id).all()
    markets = db.query(ScraperMarket).filter(ScraperMarket.enabled.is_(True)) \
        .order_by(ScraperMarket.key).all()
    targets = []
    for m in markets:
        t = timings.get(m.site)
        if t is None:
            continue
        base = {"site": m.site, "every_min": t.every_min, "stale_min": t.stale_min,
                "parser": m.parser, "post": m.post}
        if _is_page_market(m):                       # a page target of its own
            if m.url:                                # none until its page is set
                targets.append({"name": m.key.replace("_", "-"), "url": m.url, **base})
            continue
        for lg in leagues:
            if lg.site != m.site:
                continue
            targets.append({
                "name": f"{SITE_PREFIX.get(m.site, m.site)}-{_slug(lg.name)}-{m.key}",
                "url": lg.url, **base,
                "matches": {"link_re": LINK_RE.get(m.site, "-v-"), "max_tabs": t.max_tabs,
                            "market_tabs": m.tab_list, "open_gap_min": t.open_gap_min,
                            "tab_max_hours": t.tab_max_hours, "relist_min": t.relist_min},
            })
    return {"targets": targets, "timings": {s: _timing_out(t) for s, t in timings.items()}}


def _timing_out(t: ScraperTiming) -> dict:
    return {"site": t.site, "every_min": t.every_min, "stale_min": t.stale_min,
            "max_tabs": t.max_tabs, "open_gap_min": t.open_gap_min,
            "relist_min": t.relist_min, "tab_max_hours": t.tab_max_hours}


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


def _state(db: Session) -> dict:
    ensure_seeded(db)
    targets = db.query(ScraperTargetStatus).order_by(ScraperTargetStatus.name).all()
    sites = db.query(ScraperSiteStatus).order_by(ScraperSiteStatus.site).all()
    reported = max([r.updated_at for r in [*targets, *sites]], default=None)
    return {
        "leagues": [{"id": lg.id, "name": lg.name, "site": lg.site, "url": lg.url,
                     "enabled": lg.enabled}
                    for lg in db.query(ScraperLeague).order_by(ScraperLeague.id).all()],
        "markets": [{"key": m.key, "label": m.label, "site": m.site, "tabs": m.tab_list,
                     "parser": m.parser, "post": m.post, "url": m.url, "enabled": m.enabled,
                     "page": _is_page_market(m)}
                    for m in db.query(ScraperMarket).order_by(ScraperMarket.key).all()],
        "timings": [{**_timing_out(t), "listings": t.site in LINK_RE}
                    for t in db.query(ScraperTiming).order_by(ScraperTiming.site).all()],
        "status": {
            "reported_at": _iso(reported),
            "targets": [{"name": r.name, "site": r.site, "url": r.url, "state": r.state,
                         "detail": r.detail, "last_load": _iso(r.last_load),
                         "last_parse": _iso(r.last_parse), "rows_posted": r.rows_posted}
                        for r in targets],
            "sites": [{"site": r.site, "loads_hour": r.loads_hour, "per_hour": r.per_hour,
                       "loads_day": r.loads_day, "per_day": r.per_day, "cooloff": r.cooloff,
                       "cooloff_left_h": r.cooloff_left_h, "needs_resume": r.needs_resume}
                      for r in sites],
        },
    }


# --- the scraper box (key) ---------------------------------------------------

@worker_router.get("/config", response_model=ScraperConfigOut)
def get_config(db: Session = Depends(get_db)):
    ensure_seeded(db)
    return build_config(db)


class TargetStatusIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    site: str = Field(min_length=1, max_length=40)
    url: str = Field(max_length=2000)
    state: str = Field(pattern="^(running|paused|blocked|retired)$")
    detail: str | None = Field(default=None, max_length=300)
    last_load: float | None = None          # epoch seconds
    last_parse: float | None = None
    rows_posted: int = Field(default=0, ge=0)


class SiteStatusIn(BaseModel):
    site: str = Field(min_length=1, max_length=40)
    loads_hour: int = Field(ge=0)
    per_hour: int = Field(ge=0)
    loads_day: int = Field(ge=0)
    per_day: int = Field(ge=0)
    cooloff: str | None = Field(default=None, max_length=200)
    cooloff_left_h: float | None = None
    needs_resume: bool = False


class StatusIn(BaseModel):
    targets: list[TargetStatusIn] = Field(max_length=500)
    sites: list[SiteStatusIn] = Field(max_length=20)


def _from_epoch(ts: float | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


@worker_router.post("/status", response_model=ScraperStatusAckOut)
def post_status(body: StatusIn, db: Session = Depends(get_db)):
    """Replace the last report with this one, whole."""
    now = utcnow()
    db.query(ScraperTargetStatus).delete()
    db.query(ScraperSiteStatus).delete()
    for t in {t.name: t for t in body.targets}.values():
        db.add(ScraperTargetStatus(
            name=t.name, site=t.site, url=t.url, state=t.state, detail=t.detail,
            last_load=_from_epoch(t.last_load), last_parse=_from_epoch(t.last_parse),
            rows_posted=t.rows_posted, updated_at=now))
    for s in {s.site: s for s in body.sites}.values():
        db.add(ScraperSiteStatus(updated_at=now, **s.model_dump()))
    db.commit()
    return {"targets": len(body.targets), "sites": len(body.sites)}


# --- the page (session) -------------------------------------------------------

@router.get("", response_model=ScraperStateOut)
def get_scraper(db: Session = Depends(get_db)):
    return _state(db)


class LeagueIn(BaseModel):
    enabled: bool | None = None
    url: str | None = Field(default=None, max_length=2000)


@router.put("/leagues/{league_id}", response_model=ScraperStateOut)
def put_league(league_id: int, body: LeagueIn, db: Session = Depends(get_db)):
    ensure_seeded(db)
    lg = db.get(ScraperLeague, league_id)
    if lg is None:
        raise HTTPException(404, "no such league")
    if body.url is not None:
        lg.url = _check_url(lg.site, body.url)
    if body.enabled is not None:
        lg.enabled = body.enabled
    db.commit()
    return _state(db)


class MarketIn(BaseModel):
    enabled: bool | None = None
    tabs: list[str] | None = Field(default=None, max_length=6)
    url: str | None = Field(default=None, max_length=2000)


@router.put("/markets/{key}", response_model=ScraperStateOut)
def put_market(key: str, body: MarketIn, db: Session = Depends(get_db)):
    ensure_seeded(db)
    m = db.get(ScraperMarket, key)
    if m is None:
        raise HTTPException(404, "no such market")
    if body.tabs is not None:
        tabs = [t.strip()[:40] for t in body.tabs if t.strip()]
        m.tabs = json.dumps(tabs)
    if "url" in body.model_fields_set:
        # Only a page market has a url of its own; a league market's pages
        # are its leagues'. Blank clears it (the target stops).
        if not _is_page_market(m):
            raise HTTPException(422, f"{key} is read on its leagues' pages, not a url of its own")
        m.url = _check_url(m.site, body.url) if (body.url or "").strip() else None
    if body.enabled is not None:
        m.enabled = body.enabled
    db.commit()
    return _state(db)


def _is_page_market(m: ScraperMarket) -> bool:
    """A site with no competition listings (Bookie Bashing) is read on one
    page per market; the rest are read on their leagues' match pages."""
    return m.site not in LINK_RE


class TimingIn(BaseModel):
    every_min: float | None = None
    stale_min: float | None = None
    max_tabs: int | None = None
    open_gap_min: float | None = None
    relist_min: float | None = None
    tab_max_hours: float | None = None


@router.put("/timings/{site}", response_model=ScraperStateOut)
def put_timing(site: str, body: TimingIn, db: Session = Depends(get_db)):
    ensure_seeded(db)
    t = db.get(ScraperTiming, site)
    if t is None:
        raise HTTPException(404, "no such site")
    for field in body.model_fields_set:
        v = getattr(body, field)
        if v is None and field != "every_min":         # only every_min may be blank
            continue
        setattr(t, field, clamp(field, v))
    db.commit()
    return _state(db)
