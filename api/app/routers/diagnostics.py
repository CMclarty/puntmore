"""Read the error buffer back out of a running deploy, and let the UI post to it.

Two ways in, because the two readers are different:

- An admin session (a grant of "*"). Open /api/diagnostics/errors in the browser
  you are already logged into and read the JSON — no key to find.
- The static STATE_API_KEY as X-API-Key, the same self-guard /state uses, so it
  can be curled from outside a browser.

Either is sufficient. It is READ-only apart from the client-error intake and an
explicit clear, and it deliberately exposes no request bodies or headers — see
the module docstring in app/diagnostics.py.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import diagnostics
from ..database import get_db
from ..models import User
from ..schemas import ClearedOut, ClientErrorAckOut, ErrorsOut
from ..security import KeyAuth, api_key_auth, has_permission

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def require_admin_or_key(
    request: Request,
    auth: KeyAuth = Depends(api_key_auth),
    db: Session = Depends(get_db),
):
    """Admin session OR the state key (or an API client granted `state` or "*").

    Resolved by hand rather than through require_tool/user_permissions, because
    those raise 401 on a missing session — which would reject a perfectly valid
    key-authenticated call before the key was ever looked at.
    """
    p = auth.principal
    if p is not None and (p.permissions.strip() == "*" or has_permission(p.permissions, "state")):
        return
    username = request.session.get("user")
    if username:
        user = db.query(User).filter(User.username == username).first()
        # "*" only: diagnostics spans every tool, so a per-tool grant is not
        # enough to read it.
        if user and (user.permissions or "") == "*":
            return
    raise HTTPException(status_code=403, detail="Admin session or X-API-Key required")


@router.get("/errors", response_model=ErrorsOut)
def get_errors(_: None = Depends(require_admin_or_key)):
    """Recent failures, newest first, plus boot id and uptime.

    Compare `boot_id` between two reads: if it changed, the container restarted
    in between — which is what a 502 caused by a crash looks like from here,
    since the crash itself can never be logged by the process it killed.
    """
    return diagnostics.snapshot()


@router.delete("/errors", response_model=ClearedOut)
def clear_errors(_: None = Depends(require_admin_or_key)):
    """Drop the buffer, so the next reproduction starts from a clean slate."""
    return {"cleared": diagnostics.clear()}


class ClientError(BaseModel):
    """One failure as the BROWSER saw it.

    The frontend knows things the server cannot: that a fetch never resolved,
    that the response was a proxy's HTML error page rather than the API's JSON,
    which route the user was on. Those are exactly the 502-shaped failures, so
    the UI reports them here and both halves land in one buffer.
    """
    message: str = Field(max_length=2000)
    # Where in the UI, and what it was calling. Free text, clipped on storage.
    route: str | None = Field(default=None, max_length=300)
    request: str | None = Field(default=None, max_length=300)
    status: int | None = Field(default=None, ge=0, le=999)
    detail: str | None = Field(default=None, max_length=4000)


@router.post("/client", status_code=202, response_model=ClientErrorAckOut)
def report_client_error(body: ClientError, request: Request):
    """Intake for frontend errors. Deliberately open to any LOGGED-IN user.

    Not key-guarded: the whole point is to catch failures on the phone in the
    user's hand, which has a session and nothing else. It is still not public —
    an anonymous caller cannot fill the buffer — and every record is clipped and
    bounded by the same ring buffer as everything else.
    """
    if not request.session.get("user"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    ref = diagnostics.record(
        "client_error",
        user=request.session.get("user"),
        route=body.route,
        request=body.request,
        status=body.status,
        message=body.message,
        detail=body.detail,
    )
    return {"id": ref}
