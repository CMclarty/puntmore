from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User
from ..security import (
    LoginThrottle,
    granted_tools,
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
    return UserOut(username=user.username, permissions=perms, tools=granted_tools(perms))


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
    # Drop any pre-existing session contents before establishing identity, so
    # nothing from an unauthenticated session survives into an authenticated
    # one. Cheap, and it keeps the invariant "a logged-in session was built by
    # this login" true regardless of what else ever gets put in the session.
    request.session.clear()
    request.session["user"] = user.username
    return _user_out(user)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(username: str = Depends(require_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _user_out(user)
