"""Minimal auth for a small trusted syndicate: bcrypt + signed session cookie.

No registration / reset flows. Accounts live in the database (the register,
25 Nov 2026): a `*` account adds, regrants and removes them on the Users page
(routers/admin.py). Settings.auth_users only bootstraps an empty users table.
require_user is the single guard applied to every data router.
"""
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from .config import settings
from .database import engine, get_db
from .models import ApiClient, User
from .time_utils import utcnow

# Every tool the suite knows about. "*" in a permission string means all of
# these plus any added later (so an admin never needs their entry edited when a
# tool ships). Keep in sync with the routers mounted in main.py.
# `finder` (Nov 2026) is Callum's tips board on `me` (docs/finder-spec.md):
# Callum's only, so a key or account issued to anyone but
# Callum never carries "*", only named grants. `aw26` is Callum's ledger of
# slips, also on `me`. `props` (Nov 2026) is Callum's player props board, on
# `me`, granted to no one else and to no API client. `admin` (25 Nov 2026) is
# the Users page on `me`: held ONLY by an account whose raw grant is "*", never
# by name (parse_permissions drops it from a named grant), and never by a key
# (its routes take a session only: routers/admin.py).
ADMIN = "admin"
ALL_TOOLS = ("coupons", "accabonus", "cards", "hills", "goalscorers", "pnl", "kelly", "boost", "shop",
             "aw26", "finder", "props", ADMIN)

# Grants that name no page — they exist for machine callers. `value` reads the
# cross-tool value feed (GET /api/value); `state` is the whole-app snapshot
# (GET /api/state); `ingest` is the write paths of the Scraper service, the
# Bookie Bashing script, HubAI and the Bet Placer's Finder status.
# STATE_API_KEY and SAVES_INGEST_KEY are these last two, as static keys. A key
# holding a grant name that no longer exists loads and is granted nothing for it.
MACHINE_GRANTS = ("value", "state", "ingest")
# The names a grant may carry. Not `admin`: it cannot be granted by name.
ALL_GRANTS = tuple(t for t in ALL_TOOLS if t != ADMIN) + MACHINE_GRANTS


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

    # Ceiling on tracked (username, IP) pairs. Expired keys are dropped only
    # when that key is looked at again, so without this a spray across many
    # IPs — the exact traffic this defends against — grows the dict for as long
    # as the process lives. Well above any real usage: this app has four users.
    MAX_KEYS = 4096

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

    def _sweep(self) -> None:
        """Drop keys whose window has passed, and cap what's left.

        Only runs when the dict is over the ceiling, so the normal path — a
        handful of keys — never pays for it. Evicting a live counter early is
        harmless: the worst case is an attacker getting a fresh allowance, and
        they had to fill 4096 slots to earn it.
        """
        if len(self._failures) <= self.MAX_KEYS:
            return
        now = time.monotonic()
        for key, hits in list(self._failures.items()):
            if not hits or now - hits[-1] >= self._window:
                self._failures.pop(key, None)
        while len(self._failures) > self.MAX_KEYS:
            self._failures.pop(next(iter(self._failures)), None)

    def is_locked(self, username: str, ip: str) -> bool:
        return len(self._recent((username, ip))) >= self._max

    def record_failure(self, username: str, ip: str) -> None:
        key = (username, ip)
        self._recent(key)
        self._failures.setdefault(key, []).append(time.monotonic())
        self._sweep()

    def reset(self, username: str, ip: str) -> None:
        self._failures.pop((username, ip), None)


# --- machine keys -------------------------------------------------------------
#
# ONE mechanism for every machine caller. A key arrives as
# `Authorization: Bearer <key>` (preferred) or `X-API-Key: <key>` and resolves
# to a Principal: an ApiClient row (looked up by the key's sha256), or one of
# the two static env keys, which are the grants `state` (STATE_API_KEY) and
# `ingest` (SAVES_INGEST_KEY). A key is never logged or echoed anywhere.

API_KEY_PREFIX = "pm_"
# last_used_at is written at most this often per client, so a bot polling every
# few seconds does not turn every read into a SQLite write.
LAST_USED_EVERY = timedelta(minutes=1)
# The static env keys, as (setting, grant). Kept working exactly as before —
# the scraper box and the PC scripts send them.
STATIC_KEYS = (("state_api_key", "state"), ("saves_ingest_key", "ingest"))

_bearer = HTTPBearer(
    auto_error=False, scheme_name="Bearer",
    description="An API client key: `Authorization: Bearer <key>`.",
)
_x_api_key = APIKeyHeader(
    name="X-API-Key", auto_error=False, scheme_name="X-API-Key",
    description="The same key as the X-API-Key header (also how the static "
                "STATE_API_KEY / SAVES_INGEST_KEY are sent).",
)


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    """Who a key belongs to. `kind` is "client" (an ApiClient row) or
    "static" (STATE_API_KEY / SAVES_INGEST_KEY)."""
    name: str
    permissions: str
    kind: str

    @property
    def username(self) -> str:
        """The name a client acts under where a route records who did it (a
        P&L bet, an AW 26 slip). Prefixed so it can never be a real user's."""
        return f"api:{self.name}"


@dataclass(frozen=True)
class KeyAuth:
    presented: bool                 # a key was sent at all
    principal: Principal | None     # ... and it is valid (None = bad key)


def presented_key(
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_api_key: str | None = Depends(_x_api_key),
) -> str | None:
    """The key on the request, Bearer first. None when there is none."""
    if bearer is not None and bearer.credentials.strip():
        return bearer.credentials.strip()
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    return None


def resolve_api_key(key: str, db: Session) -> Principal | None:
    """The principal behind `key`, or None. Never raises on a bad key."""
    raw = key.encode()
    for setting, grant in STATIC_KEYS:
        configured = getattr(settings, setting)
        if configured and secrets.compare_digest(raw, configured.encode()):
            return Principal(name=f"{grant}-key", permissions=grant, kind="static")
    client = (
        db.query(ApiClient).filter(ApiClient.key_hash == hash_api_key(key)).first()
    )
    if client is None or not client.enabled:
        return None
    now = utcnow()
    if client.last_used_at is None or now - client.last_used_at >= LAST_USED_EVERY:
        client.last_used_at = now
        db.commit()
    return Principal(name=client.name, permissions=client.permissions or "", kind="client")


def api_key_auth(
    key: str | None = Depends(presented_key), db: Session = Depends(get_db),
) -> KeyAuth:
    """FastAPI dependency: the request's key, resolved. Cached per request by
    FastAPI, so several guards on one route resolve it once."""
    if key is None:
        return KeyAuth(presented=False, principal=None)
    return KeyAuth(presented=True, principal=resolve_api_key(key, db))


# --- sessions -------------------------------------------------------------------
#
# The key SessionMiddleware signs the cookie with (main.py). SECRET_KEY in
# production; a random one (dev, tests) resets sessions on every restart.
SESSION_SECRET = settings.secret_key or secrets.token_urlsafe(32)


def account_mark(password_hash: str) -> str:
    """The session's fingerprint of an account: an HMAC, keyed with the session
    secret, of the row's stored password hash. bcrypt's salt is random, so an
    account deleted and made again under the same name (or given a new password)
    never has the old mark, and an old cookie is not signed in to it. The hash
    itself never goes in the cookie."""
    return hmac.new(SESSION_SECRET.encode(), b"account:" + (password_hash or "").encode(),
                    hashlib.sha256).hexdigest()


def sign_in(request: Request, user: User) -> None:
    """Start a session for `user`: its name and its account's fingerprint.
    Anything in the session before is dropped."""
    request.session.clear()
    request.session["user"] = user.username
    request.session["acct"] = account_mark(user.password_hash)


def session_user(request: Request, db: Session) -> User | None:
    """The live row a session belongs to, or None: no session, no row by that
    name, or a fingerprint missing or not this row's (the account was deleted
    and made again, or its password changed). The ONE place a session becomes
    a user; every guard that reads a session goes through it."""
    username = request.session.get("user")
    mark = request.session.get("acct")
    if not username or not isinstance(mark, str) or not mark:
        return None
    user = db.query(User).filter(User.username == username).first()
    if user is None or not hmac.compare_digest(mark, account_mark(user.password_hash)):
        return None
    return user


def require_user(
    request: Request,
    auth: KeyAuth = Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> str:
    """FastAPI dependency: who is calling — the session username, or
    "api:<name>" for an API client — else 401.

    A key, when sent, IS the identity: a bad key is a 401 even beside a valid
    session cookie, so a script can never silently act as a browser's user.
    A session counts only while it matches its live row (`session_user`).
    Overridable in tests via app.dependency_overrides[require_user].
    """
    if auth.presented:
        if auth.principal is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return auth.principal.username
    user = session_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user.username


def parse_permissions(perms: str) -> set[str]:
    """Expand a permission string to the concrete set of tool names it grants.

    "*" expands to every known tool, `admin` included. Otherwise tools are
    joined by "+" (e.g. "coupons+accabonus"), and `admin` named there confers
    nothing: it belongs to "*" alone. Does NOT apply the server's enable flags
    — use granted_tools for what a user may actually see.
    """
    perms = (perms or "").strip()
    if perms == "*":
        return set(ALL_TOOLS)
    return {t.strip() for t in perms.split("+") if t.strip() and t.strip() != ADMIN}


def is_admin(perms: str) -> bool:
    """True for the raw grant "*", the only one that holds `admin`."""
    return (perms or "").strip() == "*"


def has_permission(perms: str, tool: str) -> bool:
    """True if the permission string grants access to `tool` ("*" grants all)."""
    if (perms or "").strip() == "*":
        return True
    return tool in parse_permissions(perms)


def enabled_tools() -> list[str]:
    """Tools the server currently surfaces, in nav order. coupons is always on;
    Cards is gated by its ENABLE_* flag (PLAN.md v5); the acca boost returns inside the 365 tool (item 11)."""
    tools = ["coupons"]
    if settings.enable_cards:
        tools.append("cards")
    if settings.enable_goalscorers:
        tools.append("goalscorers")
    if settings.enable_pnl:
        tools.append("pnl")
    # Kelly is a stateless client-side calculator — no router, no data, no
    # quota — so it is always surfaced; access is still per-user via the grant.
    tools.append("kelly")
    # The virtual-racing boost calculator is the same shape as Kelly: pure
    # client-side maths, no router or data, so always surfaced; grant-gated.
    tools.append("boost")
    # The shop tracker has a router, but it touches only its own per-user rows
    # and no vendor API — there is no quota or feed to switch off, so it is
    # always surfaced and gated by the per-user grant alone.
    tools.append("shop")
    # AW 26 (Callum's slips, on `me`): its own rows and no vendor API, so no
    # flag — grant-gated like the shop tracker. HubAI records slips with an API
    # client key granted `aw26`, on the same routes as the page.
    tools.append("aw26")
    # Hills (the Paddy tool against William Hill): no flag of its own — the
    # grant alone gates it, and its source exists whenever OddsPapi does.
    tools.append("hills")
    # Finder (Callum's tips, on `me`): rows come from HubAI on the Hub;
    # ENABLE_FINDER switches the routes, jobs and page off together.
    if settings.enable_finder:
        tools.append("finder")
    # Props (Callum's player props, on `me`): reads the Paddy sweep's boards;
    # ENABLE_PROPS switches the routes and the page off together.
    if settings.enable_props:
        tools.append("props")
    # The Users page (on `me`): always surfaced, held by "*" alone.
    tools.append(ADMIN)
    return tools


# Granted on the Users page though enabled_tools() does not list it: `accabonus`
# is the 365 tool, whose routes and tab are gated on the grant.
EXTRA_GRANTABLE = ("accabonus",)


def grantable_tools() -> list[str]:
    """The tools the Users page may grant, in ALL_TOOLS order: the enabled ones
    less `admin`, plus EXTRA_GRANTABLE. What the rest of the app surfaces
    (enabled_tools, granted_tools) is unchanged by the extra."""
    on = set(enabled_tools()) - {ADMIN}
    return [t for t in ALL_TOOLS if t in on or t in EXTRA_GRANTABLE]


def granted_tools(perms: str) -> list[str]:
    """Tools a user may actually see: their permission grant intersected with
    the tools the server has enabled. This is what the frontend nav gates on."""
    allowed = parse_permissions(perms)
    return [t for t in enabled_tools() if t in allowed]


def user_permissions(
    request: Request,
    db: Session = Depends(get_db),
    auth: KeyAuth = Depends(api_key_auth),
) -> str:
    """FastAPI dependency: the caller's permission string — an API client's
    grant when a key is sent, else the session user's, read live from the DB
    (so a re-seed takes effect without waiting for re-login).

    A single overridable seam — tests replace this one function to bypass
    per-tool authorization, and every require_tool guard resolves through it.
    That is also why API clients are resolved HERE: every existing guard admits
    a client with the grant, with no per-router change.
    """
    if auth.presented:
        if auth.principal is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return auth.principal.permissions
    # A session whose row is gone, was made again, or has a new password is
    # not signed in (session_user).
    user = session_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user.permissions or ""


def require_tool(tool: str):
    """Build a dependency that admits a request only if the caller (session
    user or API client) is granted `tool`. Router-level guard: seeing a tool
    means full use of it."""

    def guard(perms: str = Depends(user_permissions)) -> None:
        if not has_permission(perms, tool):
            raise HTTPException(
                status_code=403, detail="Insufficient permissions"
            )

    return guard


def require_any_grant(perms: str = Depends(user_permissions)) -> str:
    """Any authenticated caller with at least one KNOWN grant — the docs gate.
    A name that is no longer a grant (the dead `hub`) counts for nothing."""
    if not parse_permissions(perms) & set(ALL_GRANTS):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return perms


def require_key_grant(grant: str, static_setting: str):
    """Guard for the KEY-ONLY machine routes (/state, the ingest paths).

    Admits an API client granted `grant`, or the static env key that IS that
    grant. Sessions do not count, exactly as before these were unified. The old
    answers are kept: no static key configured and no client admitted → 503;
    a missing or wrong key → 401. A valid client key without the grant → 403.
    """

    def guard(auth: KeyAuth = Depends(api_key_auth)) -> None:
        p = auth.principal
        if p is not None and has_permission(p.permissions, grant):
            return
        if p is not None and p.kind == "client":
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if not getattr(settings, static_setting):
            raise HTTPException(
                status_code=503, detail=f"{static_setting.upper()} not configured"
            )
        raise HTTPException(status_code=401, detail="Invalid API key")

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


# bcrypt reads at most 72 bytes; the library refuses more rather than cut it.
MAX_PASSWORD_BYTES = 72


def _unencodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def password_problem(password: str) -> str | None:
    """Why a password set on the Users page is refused, or None. The message
    never repeats the password."""
    if not password.strip():
        return "Password cannot be blank"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    # Characters bcrypt cannot take: a control character (a NUL makes an
    # account nobody can sign in to) or one that will not encode.
    if not password.isprintable() or not password.isascii() and _unencodable(password):
        return "Password has a character that cannot be used"
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes (fewer characters if not plain letters)"
    if password.lower() in _COMMON_PASSWORDS:
        return "Password is too common"
    return None


# A username set on the Users page: ASCII letters, digits, space, dot,
# underscore and hyphen, 1 to 32 characters, starting with a letter or digit
# and not ending with a space. ASCII only, so case-blind uniqueness (SQLite's
# lower() folds ASCII alone) means what it says.
_USERNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 ._-]{0,30}[A-Za-z0-9._-])?")


def username_problem(name: str) -> str | None:
    """Why a username set on the Users page is refused, or None."""
    if not name:
        return "Username is required"
    if name in (".", "..") or not _USERNAME.fullmatch(name):
        return ("Username must be 1 to 32 characters: letters, digits, spaces, dots, "
                "underscores and hyphens, starting with a letter or digit")
    return None


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
    """Bootstrap accounts from auth_users, ONLY into an empty users table.

    Returns the number of accounts created. The database is the source of truth
    for accounts (the register, 25 Nov 2026): they are added, regranted and
    removed on the Users page, so once any account exists this creates nothing
    and never touches an existing row's grant or password. AUTH_USERS is how a
    fresh database gets its first (`*`) account.
    """
    if db.query(User.id).first() is not None:
        return 0
    created = 0
    for username, password, permissions in parse_auth_users(raw):
        if db.query(User).filter(func.lower(User.username) == username.lower()).first():
            continue    # a repeated name in AUTH_USERS: the first one wins
        db.add(User(
            username=username,
            password_hash=hash_password(password),
            permissions=permissions,
        ))
        db.flush()
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
            # V5 migration: auto-confirm OCR — edit protection and the match
            # score behind the (since-removed) confidence check.
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
            # Fred Bundles: a selection belongs to a ready-made acca, and that
            # acca carries the price. Nullable with no default — every existing
            # leg belongs to no bundle, which is exactly what null means here.
            if "bundle" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN bundle VARCHAR"))
            if "bundle_odds" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN bundle_odds FLOAT"))
            # Sept 2026: the printed kickoff, which checks the fixture match.
            # Null on legs read before it existed — those are matched on name
            # and competition only, and age out with the 8-day cleanup.
            if "printed_kickoff" not in cols:
                conn.execute(text("ALTER TABLE legs ADD COLUMN printed_kickoff DATETIME"))

        # legs.odds must become NULLABLE: GG Bonus / GG No Draw coupons print no
        # per-selection price (any 3 pay a flat 5.5), so those rows have no odds
        # and the old NOT NULL would reject the whole upload. SQLite cannot
        # ALTER a column's nullability, so the table is rebuilt in place.
        odds_col = next(
            (c for c in inspector.get_columns("legs") if c["name"] == "odds"), None
        )
        if odds_col is not None and not odds_col["nullable"]:
            # Only columns the MODEL still has: the rebuilt table is created
            # from the model, so carrying a column the model has dropped (as
            # `flags` was) would INSERT into a column that no longer exists.
            model_cols = set(Base.metadata.tables["legs"].columns.keys())
            carried = [
                c["name"] for c in inspector.get_columns("legs")
                if c["name"] in model_cols
            ]
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
            # The rebuilt table already has `edited` from the model (it carries
            # a SQL server_default so the INSERT above fills it), so no further
            # ALTERs are needed here — the previous ones ran on a closed
            # connection and duplicated existing columns.

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
            # Grid scan audit (Aug 2026). NULL on every coupon read before the
            # check existed — meaningfully so: "not measured" is not "measured
            # and clean", and backfilling a clean verdict onto an unmeasured
            # scan would assert something nobody checked.
            if "scan_audit" not in ccols:
                conn.execute(text("ALTER TABLE coupons ADD COLUMN scan_audit TEXT"))

    # P&L stake/return entry mode: bets logged with both figures carry them
    # alongside the net amount. NULL on every pre-existing (flat) row — that
    # NULL is meaningful, so don't backfill it with a zero stake.
    if "bets" in tables:
        bcols = {c["name"] for c in inspector.get_columns("bets")}
        with engine.begin() as conn:
            if "stake" not in bcols:
                conn.execute(text("ALTER TABLE bets ADD COLUMN stake FLOAT"))
            if "returned" not in bcols:
                conn.execute(text("ALTER TABLE bets ADD COLUMN returned FLOAT"))
            # Price taken vs BFSP — its own series, NULL on every bet logged
            # before it existed and on any bet where the odds weren't recorded.
            if "price_taken" not in bcols:
                conn.execute(text("ALTER TABLE bets ADD COLUMN price_taken FLOAT"))
            if "bfsp" not in bcols:
                conn.execute(text("ALTER TABLE bets ADD COLUMN bfsp FLOAT"))
            # Two-ledger split: bets are scoped to a category ("dogs" /
            # "virtuals"). Rows written before the split had no category, so the
            # SQL DEFAULT files them under "dogs" — the ledger that existed until
            # now. The matching index mirrors the model's index=True so a fresh
            # DB and a migrated one end up identical.
            if "category" not in bcols:
                conn.execute(text(
                    "ALTER TABLE bets ADD COLUMN category VARCHAR "
                    "NOT NULL DEFAULT 'dogs'"
                ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_bets_category ON bets (category)"
            ))
            # Each-way (virtuals): stake holds the total outlay, place_fraction
            # the terms the place part was paid at. Pre-existing rows are all
            # win-only, which is what the DEFAULT 0 records.
            if "each_way" not in bcols:
                conn.execute(text(
                    "ALTER TABLE bets ADD COLUMN each_way BOOLEAN "
                    "NOT NULL DEFAULT 0"
                ))
            if "place_fraction" not in bcols:
                conn.execute(text("ALTER TABLE bets ADD COLUMN place_fraction FLOAT"))
            # Staff tips (virtuals): a cash loss that is not a bet. Every
            # pre-existing row is a real bet, which is what the DEFAULT 0 records.
            if "is_tip" not in bcols:
                conn.execute(text(
                    "ALTER TABLE bets ADD COLUMN is_tip BOOLEAN "
                    "NOT NULL DEFAULT 0"
                ))
            # Boost EV (virtuals): NULL on every earlier bet, none of which
            # recorded the pre-boost price it would be measured from.
            if "ev" not in bcols:
                conn.execute(text("ALTER TABLE bets ADD COLUMN ev FLOAT"))

    # AW 26 (9 Nov 2026): a leg whose fair is taken as its odds. Every earlier
    # leg had a fair found or none at all, which is what the DEFAULT 0 records.
    if "aw_legs" in tables:
        awcols = {c["name"] for c in inspector.get_columns("aw_legs")}
        if "fair_assumed" not in awcols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE aw_legs ADD COLUMN fair_assumed BOOLEAN "
                    "NOT NULL DEFAULT 0"
                ))

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
