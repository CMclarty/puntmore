from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Odds provider — the-odds-api.com. Falls back to MockOddsProvider if absent.
    odds_api_key: str = ""
    # OCR provider keys — set one. GOOGLE_API_KEY takes priority; falls back to
    # ANTHROPIC_API_KEY; falls back to MockOCRProvider if neither is present.
    google_api_key: str = ""
    anthropic_api_key: str = ""
    discord_webhook_url: str = ""
    upload_dir: str = "uploads"
    # Odds API in-memory cache TTL (seconds). Default 24 h keeps the free tier
    # (500 credits/month) safe. Only lower this on a paid Odds API tier —
    # 1 h would burn ~336 credits/day on a single sport.
    odds_cache_ttl: int = 86400
    # Minimum edge vs de-vigged Pinnacle to include in the feeds. Stored as an
    # edge fraction; the UI presents it as %EV (bet odds as a percentage of
    # fair: %EV = (1 + edge) × 100). Default -0.02 = the 98% EV preset.
    arb_min_edge: float = -0.02
    cards_min_edge: float = -0.02
    # Optional comma-separated override of the AccaBonus sport keys. Empty =
    # keys discovered from the free /v4/sports list, filtered to Acca Boost-
    # eligible competitions, soccer excluded (accabonus/arbs.py). Set to a
    # single key (e.g. "tennis_atp") to cap quota burn at ~1 API request per
    # 24 h while testing live data.
    accabonus_sport_keys: str = ""
    # Coupon tracker odds source (behind the OddsProvider seam). "theoddsapi"
    # (default) prices coupon legs via TheOddsApiProvider; "bettingiscool" is the
    # planned Pinnacle-native alternative (PLAN v4 §V2 — not yet built, treated
    # as the mock until it exists). With no ODDS_API_KEY set, the mock provider
    # is used regardless.
    coupon_odds_source: str = "theoddsapi"
    # Cache TTL (seconds) for the coupon tracker's TheOddsApi pulls. One FULL
    # /v4/sports/{sport}/odds response per sport is fetched and reused across
    # every coupon leg and the 10-min refresh loop for this long. Independent of
    # odds_cache_ttl (which the hidden AccaBonus/Cards tools use).
    # Default 86400 = 24 h — the FREE-TIER-SAFE value for the 7 default soccer
    # keys (~420 credits/month; the ~20 h floor plus margin, see PLAN.md v5).
    # On a PAID tier, drop to 1800 (30 min) for a fresher refresh — .env only.
    coupon_odds_cache_ttl: int = 86400
    # Tool visibility flags. The suite currently surfaces ONLY the coupon
    # tracker; AccaBonus and Cards/Corners are hidden. Their code stays in the
    # repo — these flags gate whether their API routers are mounted (main.py)
    # and, mirrored in the frontend, whether their nav tabs/routes appear.
    # Flip to True (in .env) to bring a tool back. See PLAN.md v5.
    enable_accabonus: bool = False
    enable_cards: bool = False
    # Static API key for GET /state (machine consumers, e.g. dashboards/bots).
    # Sent as the X-API-Key header. Blank = endpoint disabled (503).
    state_api_key: str = ""
    # Session cookie signing key. If blank, a random key is generated at startup
    # (sessions reset on restart — fine for dev; set a fixed value in prod).
    secret_key: str = ""
    # Seed users, format "user1:pass1,user2:pass2". Parsed on startup; missing
    # users are created. Existing users are never modified.
    auth_users: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
