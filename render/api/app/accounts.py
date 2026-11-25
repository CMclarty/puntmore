"""Accounts, as the Users page manages them (routers/admin.py).

The database is the source of truth for accounts (the register, 25 Nov 2026).
A grant set here is tools joined by "+": the ticked grantable ones
(`security.grantable_tools`), and on a regrant every token of the stored grant
the page does not show (a tool switched off, a dead name), kept as it was;
never "*", never `admin`. An account whose grant
is "*" is never changed or removed here, so the admin cannot lock themselves
out and no one is made or unmade an admin on the page. A password is stored
only as its bcrypt hash and is never logged or returned.
"""
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import User
from .security import (
    ADMIN, grantable_tools, granted_tools, hash_password, is_admin, password_problem,
    username_problem,
)


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
    tools: list[str]      # "*": what it may see; else its grantable tools, the tick boxes


def _tokens(perms: str) -> list[str]:
    return [t.strip() for t in (perms or "").split("+") if t.strip()]


def _account(user: User) -> Account:
    perms = user.permissions or ""
    if is_admin(perms):
        tools = granted_tools(perms)
    else:
        held = set(_tokens(perms))
        tools = [t for t in grantable_tools() if t in held]
    return Account(username=user.username, permissions=perms, tools=tools)


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
    # Spaces at either end are trimmed, nothing else: a tab or newline is refused.
    # The rule (security.username_problem) also keeps out ":" (a key acts as
    # "api:<name>") and "/" (unreachable at /admin/users/{username}).
    name = (raw or "").strip(" ")
    problem = username_problem(name)
    if problem:
        raise AccountError(422, problem)
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
    """Replace the account's grantable tools with `tools`. Every other token of
    its stored grant (a tool switched off, a name the page does not offer) is
    kept, after them, as it was; `admin` and "*" are dropped."""
    user = _editable(db, username)
    grantable = set(grantable_tools())
    kept = [t for t in _tokens(user.permissions) if t not in grantable and t not in (ADMIN, "*")]
    user.permissions = "+".join([_grant(tools)] + list(dict.fromkeys(kept)))
    db.commit()
    return _account(user)


def delete_account(db: Session, username: str) -> None:
    """Delete the row. Their sessions end at once: every request checks the
    session against its live row (security.session_user), and an account made
    again under the same name has another fingerprint."""
    db.delete(_editable(db, username))
    db.commit()
