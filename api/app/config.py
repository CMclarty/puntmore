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
    # Minimum edge vs de-vigged Pinnacle to include in the arbs list.
    # 0.0 = only show events where Bet365 is at or above fair.
    # -0.05 = include near-arbs within 5% of fair value (default).
    arb_min_edge: float = -0.05
    cards_min_edge: float = -0.05
    # Optional comma-separated override of the tennis/AccaBonus sport keys.
    # Empty = the full SPORT_KEYS list in tennis/arbs.py. Set to a single key
    # (e.g. "tennis_atp") to cap quota burn at ~1 API request per 24 h while
    # testing live data.
    tennis_sport_keys: str = ""
    # Session cookie signing key. If blank, a random key is generated at startup
    # (sessions reset on restart — fine for dev; set a fixed value in prod).
    secret_key: str = ""
    # Seed users, format "user1:pass1,user2:pass2". Parsed on startup; missing
    # users are created. Existing users are never modified.
    auth_users: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
