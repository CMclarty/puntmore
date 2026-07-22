"""Minimal auth for a small trusted syndicate: bcrypt + signed session cookie.

No registration / reset flows. Users are seeded from Settings.auth_users on
startup. require_user is the single guard applied to every data router.
"""
import time

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from .config import settings
from .database import engine, get_db
from .models import User

# Every tool the suite knows about. "*" in a permission string means all of
# these plus any added later (so an admin never needs their entry edited when a
# tool ships). Keep in sync with the routers mounted in main.py.
ALL_TOOLS = ("coupons", "accabonus", "cards")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


# A real bcrypt hash to check against when the username doesn't exist, so a
# miss costs the same ~100ms as a wrong password. Without this, an unknown
# username returns immediately and a known one doesn't — a timing oracle that
# enumerates valid usernames before an attacker spends a single guess.
_DUMMY_HASH = hash_password("dummy-password-for-constant-time-comparison")


def verify_login(password: str, user) -> bool:
    """Check a password, doing equal work whether or not the user exists."""
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, user.password_hash)


class LoginThrottle:
    """In-process failed-login counter, keyed by (username, client IP).

    Keyed on both so one attacker cannot lock out a legitimate user by
    hammering their username, and one IP cannot spray many usernames freely.
    Counters live in memory: they reset on restart, which is acceptable here —
    this raises the cost of a brute-force attempt, it is not an audit log.
    """

    def __init__(self, max_attempts: int, lockout_seconds: int):
        self._max = max_attempts
        self._window = lockout_seconds
        self._failures: dict[tuple[str, str], list[float]] = {}

    def _recent(self, key: tuple[str, str]) -> list[float]:
        now = time.monotonic()
        hits = [t for t in self._failures.get(key, []) if now - t < self._window]
        if hits:
            self._failures[key] = hits
        else:
            self._failures.pop(key, None)
        return hits

    def is_locked(self, username: str, ip: str) -> bool:
        return len(self._recent((username, ip))) >= self._max

    def record_failure(self, username: str, ip: str) -> None:
        key = (username, ip)
        self._recent(key)
        self._failures.setdefault(key, []).append(time.monotonic())

    def reset(self, username: str, ip: str) -> None:
        self._failures.pop((username, ip), None)


def require_user(request: Request) -> str:
    """FastAPI dependency: returns the session username or raises 401.

    Overridable in tests via app.dependency_overrides[require_user].
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def parse_permissions(perms: str) -> set[str]:
    """Expand a permission string to the concrete set of tool names it grants.

    "*" expands to every known tool. Otherwise tools are joined by "+"
    (e.g. "coupons+accabonus"). Does NOT apply the server's enable flags — use
    granted_tools for what a user may actually see.
    """
    perms = (perms or "").strip()
    if perms == "*":
        return set(ALL_TOOLS)
    return {t.strip() for t in perms.split("+") if t.strip()}


def has_permission(perms: str, tool: str) -> bool:
    """True if the permission string grants access to `tool` ("*" grants all)."""
    if (perms or "").strip() == "*":
        return True
    return tool in parse_permissions(perms)


def enabled_tools() -> list[str]:
    """Tools the server currently surfaces, in nav order. coupons is always on;
    AccaBonus and Cards are gated by their ENABLE_* flags (PLAN.md v5)."""
    tools = ["coupons"]
    if settings.enable_accabonus:
        tools.append("accabonus")
    if settings.enable_cards:
        tools.append("cards")
    return tools


def granted_tools(perms: str) -> list[str]:
    """Tools a user may actually see: their permission grant intersected with
    the tools the server has enabled. This is what the frontend nav gates on."""
    allowed = parse_permissions(perms)
    return [t for t in enabled_tools() if t in allowed]


def user_permissions(request: Request, db: Session = Depends(get_db)) -> str:
    """FastAPI dependency: the current user's permission string, read live from
    the DB (so a re-seed takes effect without waiting for re-login).

    A single overridable seam — tests replace this one function to bypass
    per-tool authorization, and every require_tool guard resolves through it.
    """
    username = require_user(request)
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        # Session names a user that no longer exists (deleted between requests).
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user.permissions or ""


def require_tool(tool: str):
    """Build a dependency that admits a request only if the session user is
    granted `tool`. Router-level guard: seeing a tool means full use of it."""

    def guard(perms: str = Depends(user_permissions)) -> None:
        if not has_permission(perms, tool):
            raise HTTPException(
                status_code=403, detail="Insufficient permissions"
            )

    return guard


def parse_auth_users(raw: str) -> list[tuple[str, str, str]]:
    """Parse "u:p:perms,..." → [("u","p","perms"), ...]. Tolerant of whitespace.

    The third field is optional and defaults to "*" (all tools), preserving the
    two-field "user:pass" format. It is "*" or tools joined by "+"
    (e.g. "coupons+accabonus"). Because ":" separates the three fields, a
    password itself cannot contain ":".
    """
    entries = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        parts = chunk.split(":", 2)
        username, password = parts[0].strip(), parts[1].strip()
        permissions = parts[2].strip() if len(parts) > 2 and parts[2].strip() else "*"
        if username and password:
            entries.append((username, password, permissions))
    return entries


# Passwords that are effectively public knowledge. Not an exhaustive strength
# check — just the ones an attacker's first ten guesses will cover.
_COMMON_PASSWORDS = {
    "admin", "password", "changeme", "letmein", "secret", "test",
    "123456", "12345678", "qwerty", "puntmore",
}
MIN_PASSWORD_LENGTH = 12


def weak_seed_passwords(raw: str) -> list[str]:
    """Usernames in AUTH_USERS whose password is guessable.

    Returns usernames only — never the passwords, since the caller logs this.
    """
    weak = []
    for username, password, _permissions in parse_auth_users(raw):
        if (
            password.lower() in _COMMON_PASSWORDS
            or password.lower() == username.lower()
            or len(password) < MIN_PASSWORD_LENGTH
        ):
            weak.append(username)
    return weak


def seed_users(db: Session, raw: str) -> int:
    """Create any users from auth_users that don't already exist.

    Existing users are never modified (change a password by deleting the row).
    Returns the number of users created.
    """
    created = 0
    for username, password, permissions in parse_auth_users(raw):
        exists = db.query(User).filter(User.username == username).first()
        if exists:
            continue
        db.add(User(
            username=username,
            password_hash=hash_password(password),
            permissions=permissions,
        ))
        created += 1
    db.commit()
    return created


def ensure_schema() -> None:
    """Idempotent SQLite migrations. Safe to run on every startup."""
    from .models import Base  # local import avoids circular at module level

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    # Split-auth migration: users gain a permission grant. Default "*" (all
    # tools) so users seeded before the split keep full access — nobody is
    # locked out by the column appearing.
    if "users" in tables:
        ucols = {c["name"] for c in inspector.get_columns("users")}
        if "permissions" not in ucols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN permissions VARCHAR "
                    "NOT NULL DEFAULT '*'"
                ))

    # R1 migration: old schema had sets/versions/matches + template coupons.
    # Detect by presence of 'sets' or 'versions' (new schema has neither).
    if "sets" in tables or "versions" in tables:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS matches"))
            conn.execute(text("DROP TABLE IF EXISTS versions"))
            conn.execute(text("DROP TABLE IF EXISTS sets"))
            conn.execute(text("DROP TABLE IF EXISTS coupons"))
            conn.execute(text("DROP TABLE IF EXISTS legs"))
        Base.metadata.create_all(bind=engine)
        return  # fresh schema created; nothing else to migrate

    # Old coupons table with sort_order (template model) but without sets/versions.
    if "coupons" in tables:
        cols = {c["name"] for c in inspector.get_columns("coupons")}
        if "sort_order" in cols:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS coupons"))
                conn.execute(text("DROP TABLE IF EXISTS legs"))
            Base.metadata.create_all(bind=engine)
            return

    # R7 migration: matched-fixture transparency + notification dedupe columns.
    if "legs" in tables:
        cols = {c["name"] for c in inspector.get_columns("legs")}
        with engine.begin() as conn:
            if "matched_fixture" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN matched_fixture VARCHAR"))
            if "notified_tier" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN notified_tier VARCHAR"))
            # V5 migration: auto-confirm OCR — anomaly flags, edit protection,
            # and the match score behind the low-confidence flag.
            if "match_score" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN match_score FLOAT"))
            if "kickoff" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN kickoff DATETIME"))
            if "fair_odds_home" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN fair_odds_home FLOAT"))
            if "fair_odds_away" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN fair_odds_away FLOAT"))
            if "side" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN side VARCHAR"))

        # legs.odds must become NULLABLE: GG Bonus / GG No Draw coupons print no
        # per-selection price (any 3 pay a flat 5.5), so those rows have no odds
        # and the old NOT NULL would reject the whole upload. SQLite cannot
        # ALTER a column's nullability, so the table is rebuilt in place.
        odds_col = next(
            (c for c in inspector.get_columns("legs") if c["name"] == "odds"), None
        )
        if odds_col is not None and not odds_col["nullable"]:
            carried = [c["name"] for c in inspector.get_columns("legs")]
            with engine.begin() as conn:
                # FKs off for the swap, or legs' own FK to coupons blocks the
                # drop. Re-enabled by the connect listener on the next checkout.
                conn.execute(text("PRAGMA foreign_keys = OFF"))
                conn.execute(text("ALTER TABLE legs RENAME TO legs_old"))
                Base.metadata.tables["legs"].create(bind=conn)
                cols_sql = ", ".join(f'"{c}"' for c in carried)
                conn.execute(text(
                    f"INSERT INTO legs ({cols_sql}) SELECT {cols_sql} FROM legs_old"
                ))
                conn.execute(text("DROP TABLE legs_old"))
            if "flags" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN flags VARCHAR"))
            if "edited" not in cols:
                conn.execute(text(
                    "ALTER TABLE legs ADD COLUMN edited BOOLEAN NOT NULL DEFAULT 0"
                ))

    # Coupons cover a window (Mon–Wed / Wed–Fri / Sat / Sun), not one day.
    if "coupons" in tables:
        ccols = {c["name"] for c in inspector.get_columns("coupons")}
        with engine.begin() as conn:
            if "period" not in ccols:
                conn.execute(text("ALTER TABLE coupons ADD COLUMN period VARCHAR"))
            if "period_ends" not in ccols:
                conn.execute(text("ALTER TABLE coupons ADD COLUMN period_ends DATE"))
            if "payout_ladder" not in ccols:
                conn.execute(text("ALTER TABLE coupons ADD COLUMN payout_ladder TEXT"))

    # V5 status remap: pending|live -> processing|confirmed. Existing coupons
    # were all past the old review gate, so 'pending' rows are stale uploads
    # that never got confirmed; they map to 'processing' and re-price on the
    # next refresh.
    if "coupons" in tables:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE coupons SET status = 'confirmed' WHERE status = 'live'"
            ))
            conn.execute(text(
                "UPDATE coupons SET status = 'processing' WHERE status = 'pending'"
            ))
