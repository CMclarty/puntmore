import logging
import secrets
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from .database import engine, SessionLocal
from .models import Base
from .seed import seed
from .config import settings
from .security import require_user, seed_users, ensure_schema
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
    # bettingiscool is the intended source (SPEC §5). Its transport is not yet
    # implemented, so selecting it without a key degrades to TheOddsApi rather
    # than to the mock: a deployed instance must keep pricing real legs. The
    # moment BETTINGISCOOL_API_KEY is set AND the provider is implemented, this
    # branch takes it and TheOddsApi drops out of the coupon path entirely.
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
        "Coupon odds provider: %s (source=%s)",
        type(app.state.odds_provider).__name__, source,
    )

    if settings.google_api_key:
        app.state.ocr_provider = GeminiOCRProvider(settings.google_api_key)
    elif settings.anthropic_api_key:
        app.state.ocr_provider = ClaudeOCRProvider(settings.anthropic_api_key)
    else:
        app.state.ocr_provider = MockOCRProvider()

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
    https_only=False,  # TLS terminates at Caddy; cookie travels http internally
    max_age=30 * 24 * 60 * 60,  # 30 days
)

# Auth router is open (login/logout); /auth/me self-guards.
app.include_router(auth_router.router)

# All data routers require a logged-in session.
_guard = [Depends(require_user)]
app.include_router(coupons.router, dependencies=_guard)
app.include_router(upload_router, dependencies=_guard)
# AccaBonus and Cards/Corners are hidden for now (coupon-tracker-only mode,
# PLAN.md v5). Code is kept; the routers are only mounted when re-enabled via
# the ENABLE_ACCABONUS / ENABLE_CARDS flags in .env.
if settings.enable_accabonus:
    app.include_router(accabonus.router, dependencies=_guard)
if settings.enable_cards:
    app.include_router(cards_router.router, dependencies=_guard)

# /state self-guards with a static API key (machine consumers, no session).
app.include_router(state_router.router)
