from fastapi import APIRouter, Query, Request

from ..config import settings
from ..schemas import CardFeedItem
# The feed lives in cards/feed.py; re-exported so existing imports keep working.
from ..cards.feed import (  # noqa: F401
    build_feed, describe, diagnose, market_name, price_events,
)

router = APIRouter()


@router.get("/feed", response_model=list[CardFeedItem])
def get_cards_feed(
    request: Request,
    min_edge: float = Query(default=None, ge=-0.50, le=0.50),
):
    if min_edge is None:
        min_edge = settings.cards_min_edge
    return build_feed(request.app.state.cards_client, min_edge)


@router.get("/shadow")
def shadow_report(days: int = Query(default=14, ge=1, le=30)):
    """How wrong the counts model is, MEASURED, by distance beyond Pinnacle's ladder.

    Hidden Pinnacle rungs predicted from the rest (cards/shadow_store.py). The
    numbers to set the beyond-ladder penalty from. Free: no vendor calls.
    """
    from ..database import SessionLocal
    from ..cards import shadow_store
    db = SessionLocal()
    try:
        return shadow_store.summary(db, days)
    finally:
        db.close()


@router.get("/oddspapi-check")
def oddspapi_check(request: Request, book: str | None = None, tournament: int = 17,
                   find: str = "nations,qualif,world cup,euro"):
    """What OddsPapi's live catalogue and lists actually say — read ONCE after
    switching, before trusting a price. Costs up to 3 requests (market
    catalogue, participants, tournaments), each cached for a day after.

    Answers the three things that could not be verified from where the adapter
    was written: which market ids are Paddy's corners/cards and at what lines,
    whether team names resolve, and whether the configured tournament ids are
    the competitions they are meant to be.
    """
    from ..cards.oddspapi import (
        OddsPapiPaddySource, _as_list, _first, _tournament_entries,
        GOALS, SAVES, catalogue_index, corner_card_markets, coverage,
        fixture_lines, match_total_markets, participant_names,
        resolve_tournaments, tournament_names,
    )
    cards = request.app.state.cards_client
    if not isinstance(cards, OddsPapiPaddySource):
        return {"error": "OddsPapi is not configured — set ODDSPAPI_API_KEY."}
    client = cards._client
    out: dict = {}
    try:
        cat = client.catalogue()
        found = corner_card_markets(cat)
        out["catalogue_entries"] = len(_as_list(cat))
        out["corner_card_markets"] = {
            mid: {"market": k, "line": line, "outcomes": sides}
            for mid, (k, line, sides) in sorted(found.items(), key=lambda x: (x[1][0], x[1][1]))
        }
        # Saves and match goals: reported, not priced yet. Lines per kind, and
        # the raw catalogue names behind them so a mis-classification shows.
        extra = match_total_markets(cat, kinds=(SAVES, GOALS))
        names = {str(_first(m, "marketId", "id", "_id")): _first(m, "marketName", "name")
                 for m in _as_list(cat)}
        out["other_total_markets"] = {
            kind: {
                "markets": sum(1 for k, _, _ in extra.values() if k == kind),
                "lines": sorted({line for k, line, _ in extra.values() if k == kind}),
                "names": sorted({str(names.get(mid)) for mid, (k, _, _) in extra.items() if k == kind})[:20],
            }
            for kind in (SAVES, GOALS)
        }
        # The raw entries behind any three ids, so the field names can be read.
        out["sample_raw_entries"] = [
            m for m in _as_list(cat)
            if str(_first(m, "marketId", "id", "_id")) in ("10803", "10807", "10811")
        ][:3]
    except Exception as exc:
        out["catalogue_error"] = str(exc)
    try:
        out["team_names_known"] = len(participant_names(client.participants()))
    except Exception as exc:
        out["participants_error"] = str(exc)
    try:
        known = tournament_names(client.tournaments())
        ids, unresolved = resolve_tournaments(_tournament_entries(), known)
        out["tournaments"] = {str(i): known.get(i, "NOT IN ODDSPAPI'S LIST") for i in ids}
        out["unresolved_tournaments"] = unresolved
        # Competitions whose name contains any of `find` — for locating an id.
        words = [w.strip().lower() for w in find.split(",") if w.strip()]
        out["tournament_search"] = {
            str(i): n for i, n in sorted(known.items())
            if any(w in n.lower() for w in words)
        }
    except Exception as exc:
        out["tournaments_error"] = str(exc)
    if book:
        # ONE request: this book's whole board for one competition, summarised
        # as two-sided lines per fixture by market type.
        try:
            board = client.odds_by_tournaments([tournament], bookmaker=client.resolve_book(book))
            index = catalogue_index(client.catalogue())
            out["coverage"] = coverage(board, index, book)
            out["coverage"]["tournament"] = tournament
            # The same board per fixture (no extra request): ids to quote to
            # the vendor when a match is missing a market the site shows.
            out["fixtures"] = fixture_lines(board, index, book,
                                            participant_names(client.participants()))
        except Exception as exc:
            out["coverage_error"] = str(exc)
    try:
        # Unmetered: the plan's allowance and what this period has used.
        acct = client.account()
        # The plan, not the whole record: it carries the API key and email.
        out["account"] = [
            {k: s.get(k) for k in ("plan", "request_limit", "request_count", "valid_until")}
            | {"bookmakers": sorted((s.get("bookmakers") or {}).keys())}
            for s in acct.get("subscriptions") or [] if s.get("is_active")
        ]
    except Exception as exc:
        out["account_error"] = str(exc)
    out["requests_today"] = client.requests_today
    out["last_sweep"] = cards.last_sweep
    return out


@router.get("/diagnostics")
def get_cards_diagnostics(request: Request):
    """One request that says why the screen looks the way it does."""
    return diagnose(request.app.state.cards_client)
