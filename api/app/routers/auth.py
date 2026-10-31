from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User
from ..schemas import OkOut
from ..site import SHALGO, SHALGO_TOOLS, site_for
from ..security import (
    KeyAuth,
    LoginThrottle,
    api_key_auth,
    granted_tools,
    login_permissions,
    require_user,
    verify_login,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_throttle = LoginThrottle(
    settings.login_max_attempts, settings.login_lockout_seconds
)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
    permissions: str          # raw grant ("*" or tools joined by "+")
    tools: list[str]          # tools this user may see (grant ∩ enabled tools)


def _user_out(user: User) -> UserOut:
    perms = user.permissions or "*"
    return UserOut(username=user.username, permissions=perms,
                   tools=granted_tools(login_permissions(user.username, perms)))


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"

    if _throttle.is_locked(body.username, ip):
        # 429 rather than 401: an attacker learns only that they are throttled,
        # not whether the credentials were right.
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again later.",
        )

    user = db.query(User).filter(User.username == body.username).first()
    # verify_login does equal work whether or not the user exists, so response
    # time does not reveal which usernames are valid.
    if not verify_login(body.password, user):
        _throttle.record_failure(body.username, ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _throttle.reset(body.username, ip)
    # Sign in only on a site holding one of the user's tools: a Shalgo-only
    # account (cards, hills) is refused on Puntmore, and the reverse. The
    # session cookie is per host, so this keeps them to their own site.
    tools = _user_out(user).tools
    on_site = [t for t in tools if (t in SHALGO_TOOLS) == (site_for(request.headers.get("host")) == SHALGO)]
    if tools and not on_site:
        raise HTTPException(status_code=403, detail="This account signs in on the other site")
    # Drop any pre-existing session contents before establishing identity, so
    # nothing from an unauthenticated session survives into an authenticated
    # one. Cheap, and it keeps the invariant "a logged-in session was built by
    # this login" true regardless of what else ever gets put in the session.
    request.session.clear()
    request.session["user"] = user.username
    return _user_out(user)


@router.post("/logout", response_model=OkOut)
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(
    username: str = Depends(require_user),
    auth: KeyAuth = Depends(api_key_auth),
    db: Session = Depends(get_db),
):
    """Who is calling: the session user, or an API client ("api:<name>") with
    its grant — a key's own way to check what it may reach."""
    if auth.principal is not None:
        perms = auth.principal.permissions
        return UserOut(username=auth.principal.username, permissions=perms,
                       tools=granted_tools(perms))
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _user_out(user)
