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
    # Coupon tracker odds source (behind the OddsProvider seam).
    # "bettingiscool" (default, user decision July 2026) prices coupon legs
    # Pinnacle-referenced via BettingIsCoolProvider. "theoddsapi" is retained
    # as a working fallback but is no longer the intended source.
    #
    # BettingIsCoolProvider's transport is NOT implemented (the vendor's request
    # /response schema isn't known yet), so with the default source selected and
    # no key present, main.py falls back to TheOddsApi if an ODDS_API_KEY is
    # available, else the mock. That keeps a deployed instance pricing real legs
    # instead of silently serving mock data.
    coupon_odds_source: str = "bettingiscool"
    bettingiscool_api_key: str = ""
    # Cache TTL (seconds) for the coupon tracker's odds pulls, reused across
    # every leg and the 10-min refresh loop. Independent of odds_cache_ttl
    # (which the hidden AccaBonus/Cards tools use).
    #
    # Default 600 = 10 min, matching the refresh cadence, which is what
    # bettingiscool's quota supports: 500,000 tokens per UTC DAY, and a full
    # refresh of a 10-leg coupon costs ~250. Prices stay fresh.
    #
    # ⚠️ If you set COUPON_ODDS_SOURCE=theoddsapi, RAISE this to 86400. That
    # source is billed 500 credits per MONTH and a 10-min cache would exhaust
    # the month in well under a day. main.py warns loudly if it sees that
    # combination.
    coupon_odds_cache_ttl: int = 600
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
