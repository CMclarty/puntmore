import logging
import secrets
import time
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from .database import engine, SessionLocal
from .models import Base, User
from .seed import seed
from .config import settings
from .security import (
    require_tool,
    seed_users,
    ensure_schema,
    weak_seed_passwords,
    MIN_PASSWORD_LENGTH,
)
from .odds.client import TheOddsApiClient
from . import coupon_types
from .providers import odds_api
from .providers.odds_api import TheOddsApiProvider
from .providers.oddspapi_coupon import OddsPapiCouponProvider
from .providers.mock_odds import MockOddsProvider
from .providers.claude_ocr import ClaudeOCRProvider
from .providers.gemini_ocr import GeminiOCRProvider
from .providers.mock_ocr import MockOCRProvider
from .providers.verified_ocr import VerifiedOCRProvider, describe_ocr
from .notifications.discord import NullNotificationProvider
from .notifications.routes import RoutedNotifier
from .scheduler import cleanup_kicked_off_legs, cleanup_old_coupons, start_scheduler
from .routers import coupons
from .routers.upload import router as upload_router
from .routers import accabonus
from .routers import cards as cards_router
from .routers import saves_ingest as saves_ingest_router
from .routers import goalscorers as goalscorers_router
from .routers import pnl as pnl_router
from .routers import shop as shop_router
from .routers import aw26 as aw26_router
from .routers import aw26_ingest as aw26_ingest_router
from .routers import auth as auth_router
from .routers import state as state_router
from .routers import diagnostics as diagnostics_router
from . import diagnostics
from .cards.client import CardsApiClient

logger = logging.getLogger("uvicorn.error")

# A request slower than this is recorded. Render returns 502 when its own
# timeout is hit, and the app never sees that, so the run-up is the only
# warning available. OCR uploads are legitimately slow, so this sits well above
# a normal page load rather than at it.
SLOW_REQUEST_MS = 8000


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    db = SessionLocal()
    try:
        seed(db)
        created, regranted = seed_users(db, settings.auth_users)
        if created:
            logger.info("Seeded %d user(s) from AUTH_USERS", created)
        # Every grant change is logged with both sides. A permission edit is
        # invisible until someone hits a 403, so the log is the only record that
        # a redeploy widened or narrowed what an account can reach.
        for username, old, new in regranted:
            logger.info(
                "Regranted %s from AUTH_USERS: %r -> %r", username, old, new
            )
        # Login is reachable from the public internet, so a guessable password
        # is the whole attack. Warn every startup until it is fixed — seeding
        # never rewrites a PASSWORD, so changing AUTH_USERS alone is not enough
        # for one: delete the row too. (Permissions, by contrast, do reconcile.)
        weak = weak_seed_passwords(settings.auth_users)
        if weak:
            logger.warning(
                "WEAK PASSWORD for user(s): %s — guessable or under %d chars. "
                "Change AUTH_USERS *and* delete the existing row(s), or the old "
                "password stays live.",
                ", ".join(weak), MIN_PASSWORD_LENGTH,
            )
    finally:
        db.close()

    if settings.odds_api_key:
        odds_client = TheOddsApiClient(
            settings.odds_api_key, cache_ttl=settings.odds_cache_ttl
        )
        app.state.arbs_client = odds_client
        app.state.cards_client = CardsApiClient(odds_client)
    else:
        odds_client = None
        app.state.arbs_client = None
        app.state.cards_client = None
    # Paddy Power's corners/cards come from OddsPapi when it is configured:
    # TheOddsApi does not carry them (cards/oddspapi.py). Pinnacle's side comes
    # from OddsPapi too (`oddspapi_fair_book`).
    if settings.oddspapi_api_key:
        from .cards.oddspapi import OddsPapiClient, OddsPapiPaddySource
        app.state.cards_client = OddsPapiPaddySource(
            OddsPapiClient(settings.oddspapi_api_key)
        )
        logger.info("Cards: Paddy Power side from OddsPapi, fair side from %s",
                    f"OddsPapi {settings.oddspapi_fair_book}"
                    if settings.oddspapi_fair_book else "none (set ODDSPAPI_FAIR_BOOK)")

    # Coupon odds provider (behind the OddsProvider seam).
    #
    # OddsPapi is the live source: Pinnacle's 1X2 + goal totals + team goal
    # totals come back in one bulk request per tournament chunk, which the
    # provider-agnostic GridCouponProvider fits the score grid from and derives
    # GG / GG-No-Draw / TTS / Win-and-Over / Win-to-Nil off. Without a key it
    # degrades to TheOddsApi, which cannot derive those off the grid and prices
    # compound legs naively — a stopgap, not an equivalent.
    # "bettingiscool" is accepted as a LEGACY alias for "oddspapi": bettingiscool
    # is gone, but a stale COUPON_ODDS_SOURCE=bettingiscool on Render must route
    # to OddsPapi rather than silently drop the tracker to the mock.
    source = settings.coupon_odds_source
    provider = None

    if source in ("oddspapi", "bettingiscool"):
        if settings.oddspapi_api_key:
            from .cards.oddspapi import OddsPapiClient
            provider = OddsPapiCouponProvider(
                OddsPapiClient(settings.oddspapi_api_key),
                cache_ttl=settings.coupon_odds_cache_ttl,
            )
        else:
            logger.warning(
                "COUPON_ODDS_SOURCE=%s but ODDSPAPI_API_KEY is unset — "
                "falling back to TheOddsApi for coupon pricing.", source
            )

    if provider is None and odds_client is not None and source in (
        "oddspapi", "bettingiscool", "theoddsapi",
    ):
        provider = TheOddsApiProvider(
            odds_client, cache_ttl=settings.coupon_odds_cache_ttl
        )

    app.state.odds_provider = provider or MockOddsProvider()
    logger.info(
        "Coupon odds provider: %s (source=%s, cache_ttl=%ss)",
        type(app.state.odds_provider).__name__, source,
        settings.coupon_odds_cache_ttl,
    )
    # Say OUT LOUD what a degraded provider cannot price. Both fallbacks fail
    # SILENTLY and identically to a bad photo: the legs save, the coupon opens,
    # every row simply has no fair odds. A "Team to Score 2" sheet on TheOddsApi
    # is 154 selections and 154 blanks, which reads as "the scan is broken"
    # rather than "this deploy has no OddsPapi key" — the actual cause.
    if isinstance(app.state.odds_provider, MockOddsProvider):
        logger.warning(
            "COUPON PRICING IS MOCKED — no odds provider is configured, so "
            "EVERY selection will show blank fair odds. Set "
            "ODDSPAPI_API_KEY (or ODDS_API_KEY) to price real legs."
        )
    elif isinstance(app.state.odds_provider, TheOddsApiProvider):
        logger.warning(
            "Coupon pricing is on the TheOddsApi FALLBACK. It serves h2h + "
            "totals only, so these coupon types cannot price at all and every "
            "selection on them will be blank: %s. Set ODDSPAPI_API_KEY to "
            "restore them.",
            ", ".join(
                t.label for t in coupon_types.COUPON_TYPES
                if odds_api.unsupported_reason(t.selection) is not None
            ),
        )
    # The default TTL suits OddsPapi (per-request, generous daily budget).
    # TheOddsApi bills
    # 500 credits/MONTH, where a short cache burns the whole allowance in hours.
    if (
        isinstance(app.state.odds_provider, TheOddsApiProvider)
        and settings.coupon_odds_cache_ttl < 72000
    ):
        logger.warning(
            "COUPON_ODDS_CACHE_TTL=%ss with TheOddsApi: the free tier is 500 "
            "credits/MONTH and this will exhaust it. Raise to 86400.",
            settings.coupon_odds_cache_ttl,
        )

    # A real provider is always WRAPPED: every price on a priced grid is read
    # twice, by separate calls over separate pieces of the page, and kept only
    # where the reads agree. Not a dial — a switch to turn the check off is a
    # switch that ends up off, and the failure it catches (a whole column of
    # prices one row out) is invisible on screen. The mock is not wrapped: it
    # returns canned rows whatever it is shown, so there is nothing to compare.
    if settings.google_api_key:
        app.state.ocr_provider = VerifiedOCRProvider(
            GeminiOCRProvider(settings.google_api_key)
        )
    elif settings.anthropic_api_key:
        app.state.ocr_provider = VerifiedOCRProvider(
            ClaudeOCRProvider(settings.anthropic_api_key)
        )
    else:
        # Falling back to the mock in a deployment means every upload silently
        # returns canned rows and the user's photo is never read. That has
        # happened; make it impossible to miss.
        app.state.ocr_provider = MockOCRProvider()
        logger.error(
            "NO OCR KEY SET — using MockOCRProvider. Uploads will return "
            "hard-coded test rows and your coupon image will NOT be read. "
            "Set ANTHROPIC_API_KEY (or GOOGLE_API_KEY) and restart."
        )
    logger.info("OCR provider: %s", describe_ocr(app.state.ocr_provider))

    # Per-tool / per-edge channels (DISCORD_ROUTES) over the default webhook.
    routed = RoutedNotifier(settings.discord_webhook_url, settings.discord_routes)
    app.state.notifier = routed if routed.configured else NullNotificationProvider()

    # Startup cleanup (catches anything missed while container was down) — the
    # kicked-off sweep especially, since a redeploy is exactly when a coupon
    # sits unattended through a round of fixtures.
    cleanup_old_coupons(notifier=app.state.notifier)
    cleanup_kicked_off_legs()

    scheduler = start_scheduler(
        odds_provider=app.state.odds_provider,
        notifier=app.state.notifier,
        odds_client=odds_client,
        cards_client=app.state.cards_client,
        arbs_client=app.state.arbs_client,
    )
    app.state.scheduler = scheduler

    diagnostics.record(
        "boot",
        note="startup complete",
        ocr=describe_ocr(app.state.ocr_provider) if hasattr(app.state, "ocr_provider") else None,
        odds=type(app.state.odds_provider).__name__ if hasattr(app.state, "odds_provider") else None,
    )

    yield

    # A clean shutdown is itself evidence: if the boot log has no matching
    # "shutting down", the process was killed rather than redeployed — which is
    # what an OOM or a crash behind a 502 looks like from the outside.
    diagnostics.record("shutdown", note="lifespan exit, scheduler stopping")
    scheduler.shutdown(wait=False)


app = FastAPI(title="Puntmore API", lifespan=lifespan)

# Session signing key. A fixed SECRET_KEY in prod keeps sessions alive across
# restarts; a random one (dev default) resets them on every restart.
_secret_key = settings.secret_key
if not _secret_key:
    _secret_key = secrets.token_urlsafe(32)
    logger.warning(
        "SECRET_KEY not set — using a random key; sessions reset on restart."
    )

# CORS must allow credentials for the session cookie to round-trip in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=_secret_key,
    same_site="lax",
    # Sets the cookie's Secure flag, i.e. whether the BROWSER will send it over
    # plain HTTP. Independent of the proxy hop: TLS still terminates at Caddy and
    # the cookie still travels http internally, which is fine. Off only when
    # SESSION_COOKIE_INSECURE=1 for local dev over http://localhost.
    https_only=not settings.session_cookie_insecure,
    max_age=30 * 24 * 60 * 60,  # 30 days
)

# Auth router is open (login/logout); /auth/me self-guards.
app.include_router(auth_router.router)

# Each data router is gated on its tool. require_tool enforces both a logged-in
# session and the per-user grant (seeing a tool = full use of it). Upload is
# part of the coupon tracker, so it shares the "coupons" grant.
app.include_router(coupons.router, dependencies=[Depends(require_tool("coupons"))])
app.include_router(upload_router, dependencies=[Depends(require_tool("coupons"))])
# AccaBonus and Cards/Corners are hidden for now (coupon-tracker-only mode,
# PLAN.md v5). Code is kept; the routers are only mounted when re-enabled via
# the ENABLE_ACCABONUS / ENABLE_CARDS flags in .env.
if settings.enable_accabonus:
    app.include_router(accabonus.router, dependencies=[Depends(require_tool("accabonus"))])
if settings.enable_cards:
    # The tool is "Paddy" now — corners, cards, goals and saves — so it lives
    # under /paddy. /cards stays as an alias for installed app shells and
    # scripts still pointing there, the same arrangement as /tennis.
    # (The permission and the flag keep the name "cards": renaming them would
    # revoke access for existing accounts and break the Render config.)
    for prefix, listed in (("/paddy", True), ("/cards", False)):
        app.include_router(cards_router.router, prefix=prefix, include_in_schema=listed,
                           dependencies=[Depends(require_tool("cards"))])
        # The local Bookie Bashing script's way in: key-guarded, not session-guarded.
        app.include_router(saves_ingest_router.router, prefix=prefix, include_in_schema=listed)
if settings.enable_goalscorers:
    app.include_router(
        goalscorers_router.router,
        dependencies=[Depends(require_tool("goalscorers"))],
    )
if settings.enable_pnl:
    app.include_router(pnl_router.router, dependencies=[Depends(require_tool("pnl"))])
# The shop tracker reads and writes only its own rows and calls no vendor API,
# so there is no flag to gate it on — access is per-user via the grant alone.
app.include_router(shop_router.router, dependencies=[Depends(require_tool("shop"))])
# AW 26 — the acca project's slips. No vendor API of its own and no quota, so
# no flag: the grant alone gates it. Its fair prices arrive from the local Bookie
# Bashing script through the key-guarded ingest, not a session.
app.include_router(aw26_router.router, dependencies=[Depends(require_tool("aw26"))])
app.include_router(aw26_ingest_router.router)

# /state self-guards with a static API key (machine consumers, no session).
app.include_router(state_router.router)

# /diagnostics self-guards too (admin session OR the state key).
app.include_router(diagnostics_router.router)


# Every request is timed, and anything that fails or drags is recorded. This is
# the only view into a deploy that has no shell, so it errs toward capturing.
@app.middleware("http")
async def observe_requests(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        # An exception that escapes the routers reaches here. Record it with the
        # request that caused it and hand the caller a reference, so a screenshot
        # of the error is enough to find the traceback.
        ms = round((time.perf_counter() - started) * 1000, 1)
        ref = diagnostics.record_exception(
            exc,
            method=request.method,
            path=request.url.path,
            user=request.session.get("user") if "session" in request.scope else None,
        )
        diagnostics.record(
            "server_error_timing", ref=ref, path=request.url.path, duration_ms=ms
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "error_id": ref},
        )

    ms = round((time.perf_counter() - started) * 1000, 1)
    # 5xx that a handler produced itself (rather than raising) still matters.
    if response.status_code >= 500:
        diagnostics.record(
            "server_error",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=ms,
            user=request.session.get("user") if "session" in request.scope else None,
        )
    # Render cuts a request off after its own timeout and returns 502 — the app
    # never sees that, so a request creeping up on it is the closest thing to a
    # warning we get. Uploads legitimately take seconds (OCR), hence a threshold
    # rather than an alarm on anything slow.
    elif ms >= SLOW_REQUEST_MS:
        diagnostics.record(
            "slow_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=ms,
        )
    response.headers["X-Request-Ms"] = str(ms)
    return response


@app.get("/health", include_in_schema=False)
def health():
    """Liveness probe.

    Deliberately does NOT touch the database or any vendor API: this answers
    "is the process up", and a slow upstream must not cause the host to kill an
    otherwise healthy container. Unauthenticated by design — it leaks nothing.

    Because it has no dependencies it will answer 200 on a process that cannot
    serve a single real request. Do NOT use it to gate deploys — that is what
    /ready below is for.
    """
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
def ready():
    """Readiness probe — gates deploy promotion.

    Exists because a broken deploy once went live with /health green: the app
    was mounted as a sub-application whose lifespan never ran, so it had no
    tables, no users and no providers, and every real request failed while the
    host believed the service was healthy. This checks the things that startup
    is responsible for, so that failure mode cannot pass again.

    Unauthenticated like /health, and says only whether each check passed —
    no schema, no config, no vendor detail.
    """
    checks: dict[str, bool] = {}

    # The lifespan runs create_all + ensure_schema + seed_users. Querying a
    # table it is responsible for proves all three happened against the same
    # database this process actually reads.
    try:
        db = SessionLocal()
        try:
            db.query(User).limit(1).all()
            checks["database"] = True
        finally:
            db.close()
    except Exception:
        logger.exception("Readiness: database check failed")
        checks["database"] = False

    # Providers are assigned by the lifespan; missing means it did not run.
    checks["providers"] = all(
        getattr(app.state, name, None) is not None
        for name in ("odds_provider", "ocr_provider", "notifier")
    )

    ok = all(checks.values())
    # Not a readiness failure — the app runs fine on mocks in dev — but it is
    # reported, because a deployment on mock OCR looks healthy while silently
    # ignoring every uploaded image.
    #
    # The odds entry NAMES the provider rather than saying "live". "live" was
    # actively misleading: the TheOddsApi fallback reported live while being
    # unable to price six of the nine coupon types, so a deploy showing every
    # selection blank looked healthy here. `degraded` is the one-word answer to
    # "why is nothing pricing", and `cannot_price` says exactly which coupons
    # are affected. No secrets — a class name and some coupon labels.
    odds_provider = getattr(app.state, "odds_provider", None)
    odds = {
        "provider": type(odds_provider).__name__ if odds_provider else None,
        "source": settings.coupon_odds_source,
        "degraded": isinstance(odds_provider, (MockOddsProvider, TheOddsApiProvider)),
    }
    # The last pricing pass, when the live provider ran one. This is the
    # difference between "the feed is down", "the fixtures aren't matching" and
    # "Pinnacle doesn't post this market" — three different problems that all
    # look like a blank tracker.
    last_pass = getattr(odds_provider, "last_pass", None)
    if last_pass:
        odds["last_pass"] = last_pass
    if isinstance(odds_provider, MockOddsProvider):
        odds["cannot_price"] = ["ALL — no odds provider configured"]
    elif isinstance(odds_provider, TheOddsApiProvider):
        odds["cannot_price"] = [
            t.label for t in coupon_types.COUPON_TYPES
            if odds_api.unsupported_reason(t.selection) is not None
        ]
    providers = {
        "ocr": "mock" if isinstance(
            getattr(app.state, "ocr_provider", None), MockOCRProvider
        ) else "live",
        "odds": "mock" if isinstance(odds_provider, MockOddsProvider) else "live",
        "odds_detail": odds,
    }
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "not ready",
            "checks": checks,
            "providers": providers,
        },
    )
