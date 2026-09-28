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
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import diagnostics
from ..config import settings
from ..database import get_db
from ..models import User

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def require_admin_or_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Admin session OR the state key.

    Resolved by hand rather than through require_tool/user_permissions, because
    those raise 401 on a missing session — which would reject a perfectly valid
    key-authenticated call before the key was ever looked at.
    """
    if x_api_key and settings.state_api_key:
        if secrets.compare_digest(x_api_key, settings.state_api_key):
            return
    username = request.session.get("user")
    if username:
        user = db.query(User).filter(User.username == username).first()
        # "*" only: diagnostics spans every tool, so a per-tool grant is not
        # enough to read it.
        if user and (user.permissions or "") == "*":
            return
    raise HTTPException(status_code=403, detail="Admin session or X-API-Key required")


@router.get("/errors")
def get_errors(_: None = Depends(require_admin_or_key)):
    """Recent failures, newest first, plus boot id and uptime.

    Compare `boot_id` between two reads: if it changed, the container restarted
    in between — which is what a 502 caused by a crash looks like from here,
    since the crash itself can never be logged by the process it killed.
    """
    return diagnostics.snapshot()


@router.get("/oddspapi")
def oddspapi_check(
    request: Request,
    tournament: int = 17,
    books: str = "bet365,pinnacle",
    sport: int = 10,
    find: str = "scorer",
    tournament_find: str = "",
    _: None = Depends(require_admin_or_key),
):
    """What OddsPapi carries for the tools still on TheOddsApi — read BEFORE
    porting Goalscorers (bet365 vs Pinnacle first goalscorer) and AccaBonus
    (bet365 vs Pinnacle moneylines, several sports).

    Costs one request per book named (odds for ONE tournament), plus the
    sports list and the sport's tournament list on first use (cached a day).

      - `catalogue_matches`: markets whose name contains `find` for `sport`
        — the first-goalscorer market ids, and whether they are player props.
      - `coverage`: per book, two-sided lines per fixture by market type.
      - `player_prop_sample`: per book, one player-prop market raw (a few
        players) — where a player's NAME lives decides how names are matched.
      - `sports` / `tournament_search`: ids for AccaBonus's other sports.
    """
    from ..cards.oddspapi import (
        _as_list, _first, catalogue_index, coverage, player_prop_sample,
        tournament_names,
    )
    client = getattr(request.app.state, "oddspapi_client", None)
    if client is None:
        return {"error": "OddsPapi is not configured — set ODDSPAPI_API_KEY."}
    out: dict = {"tournament": tournament, "sport": sport}
    index = {}
    try:
        cat = client.catalogue()
        index = catalogue_index(cat)
        words = [w.strip().lower() for w in find.split(",") if w.strip()]
        out["catalogue_matches"] = [
            {"market_id": mid, **info}
            for mid, info in index.items()
            if str(info.get("sport")) == str(sport)
            and any(w in (info["name"] + " " + info["type"]).lower() for w in words)
        ][:40]
        out["catalogue_matches_raw_first"] = next((
            m for m in _as_list(cat)
            if out["catalogue_matches"]
            and str(_first(m, "marketId", "id")) == out["catalogue_matches"][0]["market_id"]
        ), None)
    except Exception as exc:
        out["catalogue_error"] = str(exc)
    try:
        out["sports"] = {
            str(_first(x, "sportId", "id")): _first(x, "sportName", "name", "slug")
            for x in _as_list(client.sports())
        }
    except Exception as exc:
        out["sports_error"] = str(exc)
    if sport != 10 or tournament_find:
        try:
            known = tournament_names(client.tournaments(sport))
            words = [w.strip().lower() for w in tournament_find.split(",") if w.strip()]
            out["tournament_count"] = len(known)
            out["tournament_search"] = {
                str(i): n for i, n in sorted(known.items())
                if not words or any(w in n.lower() for w in words)
            } if words else dict(list(sorted(known.items()))[:60])
        except Exception as exc:
            out["tournaments_error"] = str(exc)
    out["coverage"], out["player_prop_sample"] = {}, {}
    for book in [b.strip() for b in books.split(",") if b.strip()]:
        try:
            fixtures = client.odds_by_tournaments([tournament], bookmaker=book)
            out["coverage"][book] = coverage(fixtures, index, book)
            out["player_prop_sample"][book] = player_prop_sample(fixtures, index, book)
        except Exception as exc:
            out["coverage"][book] = {"error": str(exc)}
    out["requests_today"] = client.requests_today
    return out


@router.delete("/errors")
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


@router.post("/client", status_code=202)
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
