import logging
import secrets
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
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
from .providers.odds_api import TheOddsApiProvider
from .providers.bettingiscool import (
    BettingIsCoolProvider,
    BettingIsCoolProviderNotConfigured,
)
from .providers.mock_odds import MockOddsProvider
from .providers.claude_ocr import ClaudeOCRProvider
from .providers.gemini_ocr import GeminiOCRProvider
from .providers.mock_ocr import MockOCRProvider
from .notifications.discord import DiscordNotificationProvider, NullNotificationProvider
from .scheduler import cleanup_old_coupons, start_scheduler
from .routers import coupons
from .routers.upload import router as upload_router
from .routers import accabonus
from .routers import cards as cards_router
from .routers import auth as auth_router
from .routers import state as state_router
from .cards.client import CardsApiClient

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    db = SessionLocal()
    try:
        seed(db)
        created = seed_users(db, settings.auth_users)
        if created:
            logger.info("Seeded %d user(s) from AUTH_USERS", created)
        # Login is reachable from the public internet, so a guessable password
        # is the whole attack. Warn every startup until it is fixed — seeding
        # never rewrites an existing user, so changing AUTH_USERS alone is not
        # enough: delete the row too.
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

    # Coupon odds provider (behind the OddsProvider seam).
    #
    # bettingiscool is the live source (SPEC §5) and is implemented. Without a
    # key it degrades to TheOddsApi rather than to the mock: a deployed
    # instance must keep pricing real legs. Note TheOddsApi cannot derive
    # Win-and-Over off the score grid, so that fallback prices compound legs
    # naively — it is a stopgap, not an equivalent.
    #
    # TheOddsApi pulls one full /v4/sports/{sport}/odds response per sport and
    # reuses it for coupon_odds_cache_ttl across every leg and the 10-min
    # refresh loop.
    source = settings.coupon_odds_source
    provider = None

    if source == "bettingiscool":
        if settings.bettingiscool_api_key:
            try:
                provider = BettingIsCoolProvider(
                    settings.bettingiscool_api_key,
                    cache_ttl=settings.coupon_odds_cache_ttl,
                )
            except BettingIsCoolProviderNotConfigured as exc:
                logger.error("bettingiscool provider unavailable: %s", exc)
        else:
            logger.warning(
                "COUPON_ODDS_SOURCE=bettingiscool but BETTINGISCOOL_API_KEY is "
                "unset — falling back to TheOddsApi for coupon pricing."
            )

    if provider is None and odds_client is not None and source in (
        "bettingiscool", "theoddsapi",
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
    # The default TTL suits bettingiscool (500k tokens/DAY). TheOddsApi bills
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

    if settings.google_api_key:
        app.state.ocr_provider = GeminiOCRProvider(settings.google_api_key)
    elif settings.anthropic_api_key:
        app.state.ocr_provider = ClaudeOCRProvider(settings.anthropic_api_key)
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
    logger.info("OCR provider: %s", type(app.state.ocr_provider).__name__)

    if settings.discord_webhook_url:
        app.state.notifier = DiscordNotificationProvider(settings.discord_webhook_url)
    else:
        app.state.notifier = NullNotificationProvider()

    # Startup cleanup (catches anything missed while container was down)
    cleanup_old_coupons(notifier=app.state.notifier)

    scheduler = start_scheduler(
        odds_provider=app.state.odds_provider,
        notifier=app.state.notifier,
    )
    app.state.scheduler = scheduler

    yield

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
    app.include_router(cards_router.router, dependencies=[Depends(require_tool("cards"))])

# /state self-guards with a static API key (machine consumers, no session).
app.include_router(state_router.router)


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
    providers = {
        "ocr": "mock" if isinstance(
            getattr(app.state, "ocr_provider", None), MockOCRProvider
        ) else "live",
        "odds": "mock" if isinstance(
            getattr(app.state, "odds_provider", None), MockOddsProvider
        ) else "live",
    }
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "not ready",
            "checks": checks,
            "providers": providers,
        },
    )
