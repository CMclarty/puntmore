"""The Users page's API: add an account, pick its tools, remove it.

Every route takes a signed-in session whose raw grant is "*" (`require_admin`);
an API key, even one granted "*", is refused. The work is `app/accounts.py`.
Nothing here logs or returns a password or a hash.
"""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import accounts
from ..database import get_db
from ..models import User
from ..schemas import AdminUserOut, AdminUsersOut
from ..security import KeyAuth, api_key_auth, grantable_tools, is_admin


def require_admin(
    request: Request,
    auth: KeyAuth = Depends(api_key_auth),
    db: Session = Depends(get_db),
) -> str:
    """A session (never a key) whose user's raw grant, read live from the
    table, is "*". A bad key is 401, as everywhere; a valid key is 403."""
    if auth.presented:
        if auth.principal is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        raise HTTPException(status_code=403, detail="Users are managed from a signed-in session")
    username = request.session.get("user")
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_admin(user.permissions):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return username


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class NewUserIn(BaseModel):
    username: str
    password: str
    tools: list[str]


class ToolsIn(BaseModel):
    tools: list[str]


def _refused(e: accounts.AccountError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


@router.get("/users", response_model=AdminUsersOut)
def list_users(db: Session = Depends(get_db)):
    """Every account with its raw grant and tools, and the tools that can be granted."""
    return AdminUsersOut(users=[AdminUserOut(**asdict(a)) for a in accounts.list_accounts(db)],
                         grantable=grantable_tools())


@router.post("/users", response_model=AdminUserOut, status_code=201)
def add_user(body: NewUserIn, db: Session = Depends(get_db)):
    try:
        return AdminUserOut(**asdict(accounts.create_account(db, body.username, body.password,
                                                             body.tools)))
    except accounts.AccountError as e:
        raise _refused(e) from None


@router.patch("/users/{username}", response_model=AdminUserOut)
def set_user_tools(username: str, body: ToolsIn, db: Session = Depends(get_db)):
    """Replace an account's tools. A "*" account is refused (403)."""
    try:
        return AdminUserOut(**asdict(accounts.set_tools(db, username, body.tools)))
    except accounts.AccountError as e:
        raise _refused(e) from None


@router.delete("/users/{username}", status_code=204)
def remove_user(username: str, db: Session = Depends(get_db)):
    """Delete an account; its sessions end at once. A "*" account is refused (403)."""
    try:
        accounts.delete_account(db, username)
    except accounts.AccountError as e:
        raise _refused(e) from None
