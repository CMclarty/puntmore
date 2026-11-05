"""The Bet Finder source registry: per-source config as DATA.

One row per source (`betfinder_sources`): name, kind, trust weight (0..1),
active flag, stake mode. Seeded from `BETFINDER_SOURCES` on startup —
missing rows only, an existing row is never modified — and edited by hand
through `PATCH /api/2up/betfinder/sources/{name}`. A tip from a source with
no row creates one INACTIVE at trust 0.5, so a new tipster is visible
straight away and bets nothing until Cal switches it on.

The trust weight is never adjusted by the code: the per-source stats are
shown beside it so Cal can set it.

`kind`:
- `hub`  — tips arrive as `tip` frames over the Hub link (the only kind
           that does anything).
- `imap` — a SEAM, not implemented. An email/Substack tipster is fetched
           on the Hub (residential IP, its own mailbox) and arrives as a
           `tip` like any other. Render never polls a mailbox; a row of this
           kind is just a label until the Hub sends its tips.
"""
from __future__ import annotations

import logging
import math
import re

from sqlalchemy.orm import Session

from ..config import settings
from ..models import BetFinderSource
from ..time_utils import utcnow

logger = logging.getLogger("uvicorn.error")

KINDS = ("hub", "imap")
STAKE_MODES = ("auto", "advised", "kelly")
DEFAULT_TRUST = 0.5
NAME_MAX = 64
_NAME_RE = re.compile(r"[^a-z0-9._\- ]")


def source_name(raw) -> str:
    """The registry key for a tip's source_id: lower-cased, reduced to
    letters, digits and `._- `, trimmed, truncated. Untrusted text, so
    bounded and stripped of control characters before it is stored or
    named in a log line."""
    return _NAME_RE.sub("", str(raw or "").lower()).strip()[:NAME_MAX]


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def parse_sources(spec: str) -> list[dict]:
    """"a:trust=0.6,active=1,stake=advised,kind=hub;b" -> [{name, ...}, ...].
    An unreadable value is dropped (logged once for the spec); the row then
    takes the default for it."""
    out, bad = [], []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        head, _, opts = part.partition(":")
        name = source_name(head)
        if not name:
            continue
        row: dict = {"name": name}
        for opt in opts.split(","):
            key, _, val = opt.partition("=")
            key, val = key.strip().lower(), val.strip()
            if not key:
                continue
            try:
                if key == "trust":
                    t = float(val)
                    if not (math.isfinite(t) and 0.0 <= t <= 1.0):
                        raise ValueError
                    row["trust_weight"] = t
                elif key == "active":
                    row["active"] = _truthy(val)
                elif key == "stake":
                    if val.lower() not in STAKE_MODES:
                        raise ValueError
                    row["stake_mode"] = val.lower()
                elif key == "kind":
                    if val.lower() not in KINDS:
                        raise ValueError
                    row["kind"] = val.lower()
                else:
                    raise ValueError
            except ValueError:
                bad.append(f"{name}.{key}")
        out.append(row)
    if bad:
        logger.warning("betfinder: BETFINDER_SOURCES: ignored %d unreadable option(s): %s",
                       len(bad), ", ".join(bad[:10]))
    return out


def ensure_sources(db: Session, spec: str | None = None) -> int:
    """Create the sources named in `betfinder_sources` that have no row.
    Never modifies an existing row. Returns how many were created."""
    made = 0
    for row in parse_sources(settings.betfinder_sources if spec is None else spec):
        if db.get(BetFinderSource, row["name"]) is not None:
            continue
        db.add(BetFinderSource(
            name=row["name"], kind=row.get("kind", "hub"),
            trust_weight=row.get("trust_weight", DEFAULT_TRUST),
            active=row.get("active", False), stake_mode=row.get("stake_mode", "auto"),
        ))
        db.flush()
        made += 1
    db.commit()
    return made


def get_or_create(db: Session, name: str) -> tuple[BetFinderSource, bool]:
    """The source row for this name, creating it INACTIVE at trust 0.5 when
    there is none. Does not commit (the pipeline commits once per pass)."""
    src = db.get(BetFinderSource, name)
    if src is not None:
        return src, False
    src = BetFinderSource(name=name, kind="hub", trust_weight=DEFAULT_TRUST,
                          active=False, stake_mode="auto")
    db.add(src)
    db.flush()
    return src, True


def all_sources(db: Session) -> list[BetFinderSource]:
    return db.query(BetFinderSource).order_by(BetFinderSource.name).all()


def update(db: Session, name: str, *, trust_weight=None, active=None, stake_mode=None,
           kind=None, note=None) -> BetFinderSource | None:
    """A hand edit. Values are validated by the route's schema; this checks
    again so a caller in code cannot store a trust outside 0..1."""
    src = db.get(BetFinderSource, name)
    if src is None:
        return None
    if trust_weight is not None:
        t = float(trust_weight)
        if not (math.isfinite(t) and 0.0 <= t <= 1.0):
            raise ValueError("trust_weight must be within 0..1")
        src.trust_weight = t
    if active is not None:
        src.active = bool(active)
    if stake_mode is not None:
        if stake_mode not in STAKE_MODES:
            raise ValueError(f"stake_mode must be one of {', '.join(STAKE_MODES)}")
        src.stake_mode = stake_mode
    if kind is not None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        src.kind = kind
    if note is not None:
        src.note = str(note)[:500]
    src.updated_at = utcnow()
    db.commit()
    logger.info("betfinder: source %s edited: trust %.2f, %s, stake %s",
                src.name, src.trust_weight, "active" if src.active else "inactive",
                src.stake_mode)
    return src
