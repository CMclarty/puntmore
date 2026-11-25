"""Accounts, as the Users page manages them (routers/admin.py).

The database is the source of truth for accounts (the register, 25 Nov 2026).
A grant set here is always tools joined by "+", each one grantable
(`security.grantable_tools`): never "*", never `admin`. An account whose grant
is "*" is never changed or removed here, so the admin cannot lock themselves
out and no one is made or unmade an admin on the page. A password is stored
only as its bcrypt hash and is never logged or returned.
"""
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import User
from .security import grantable_tools, granted_tools, hash_password, is_admin, password_problem


class AccountError(Exception):
    """A refused change: `status` is the HTTP status, the message its detail."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Account:
    username: str
    permissions: str      # the raw grant
    tools: list[str]      # what it may see (security.granted_tools)


def _account(user: User) -> Account:
    perms = user.permissions or ""
    return Account(username=user.username, permissions=perms, tools=granted_tools(perms))


def list_accounts(db: Session) -> list[Account]:
    return [_account(u) for u in db.query(User).order_by(User.id).all()]


def _grant(tools: list[str]) -> str:
    """The stored grant for a tick list: the grantable tools in nav order."""
    allowed = grantable_tools()
    wanted = {t.strip() for t in tools}
    unknown = sorted(t for t in wanted if t not in allowed)
    if unknown:
        raise AccountError(422, f"Not a tool that can be granted: {', '.join(unknown)}")
    if not wanted:
        raise AccountError(422, "Pick at least one tool")
    return "+".join(t for t in allowed if t in wanted)


def _username(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise AccountError(422, "Username is required")
    # A key acts as "api:<name>" where a route records who did something, so a
    # person's name never holds a colon (nor could one from AUTH_USERS). A
    # slash would leave the account unreachable at /admin/users/{username}.
    if ":" in name or "/" in name:
        raise AccountError(422, "Username cannot contain : or /")
    return name


def _editable(db: Session, username: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise AccountError(404, "No such user")
    if is_admin(user.permissions):
        raise AccountError(403, "An admin account cannot be changed here")
    return user


def create_account(db: Session, username: str, password: str, tools: list[str]) -> Account:
    name = _username(username)
    problem = password_problem(password or "")
    if problem:
        raise AccountError(422, problem)
    grant = _grant(tools)
    if db.query(User).filter(func.lower(User.username) == name.lower()).first():
        raise AccountError(409, "That username is taken")
    user = User(username=name, password_hash=hash_password(password), permissions=grant)
    db.add(user)
    db.commit()
    return _account(user)


def set_tools(db: Session, username: str, tools: list[str]) -> Account:
    user = _editable(db, username)
    user.permissions = _grant(tools)
    db.commit()
    return _account(user)


def delete_account(db: Session, username: str) -> None:
    """Delete the row. Their sessions end at once: every request checks the
    session's user against the table (security.user_permissions, /auth/me)."""
    db.delete(_editable(db, username))
    db.commit()
