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
    # First-goalscorer value alerts (bet365 vs de-vigged Pinnacle). Surfaced in
    # the UI when on. The scheduler builds a daily watchlist of major fixtures
    # and scans each at kickoff − 45 min (Pinnacle only posts the market near KO
    # for majors), storing rows and alerting Discord on new value.
    enable_goalscorers: bool = True
    # Private P&L bet tracker. Mounted when on, but gated by the "pnl" tool
    # grant — only users whose AUTH_USERS permission is "*" (or lists "pnl")
    # see it, so it stays a private per-user ledger.
    enable_pnl: bool = True
    # Comma-separated sport keys the watchlist covers. Default = the big-five
    # leagues + Champions League, where both books price first goalscorer.
    goalscorer_sport_keys: str = (
        "soccer_epl,soccer_spain_la_liga,soccer_italy_serie_a,"
        "soccer_germany_bundesliga,soccer_france_ligue_one,soccer_uefa_champs_league"
    )
    # Minimum edge vs de-vigged Pinnacle for a player to be stored/alerted.
    # 0.0 = bet365 at or above fair. Provisional — not backtest-calibrated.
    goalscorer_min_edge: float = 0.0
    # Confidence floor for matching a bet365 player name to a Pinnacle one.
    # Biased for precision (a false match invents fake value). Provisional —
    # calibrate against real bet365/Pinnacle name pairs before lowering.
    goalscorer_name_match_floor: float = 0.8
    # Minutes before kickoff to run the first scan.
    goalscorer_lead_minutes: int = 45
    # In-play re-scan: interval (minutes) and how long after KO to keep scanning.
    # in-play interval of 0 disables the in-play loop (pre-KO scan only).
    goalscorer_inplay_interval: int = 0
    goalscorer_inplay_window: int = 40
    # Static API key for GET /state (machine consumers, e.g. dashboards/bots).
    # Sent as the X-API-Key header. Blank = endpoint disabled (503).
    state_api_key: str = ""
    # Session cookie signing key. If blank, a random key is generated at startup
    # (sessions reset on restart — fine for dev; set a fixed value in prod).
    secret_key: str = ""
    # Drop the session cookie's Secure flag so it works over http://localhost.
    # NEVER set this in production — it lets the browser send the session cookie
    # over plain HTTP.
    session_cookie_insecure: bool = False
    # Failed logins allowed per username+IP before lockout, and the lockout
    # window in seconds. In-process counters: fine on one instance (SQLite pins
    # us to one anyway); a multi-instance deploy would need a shared store.
    login_max_attempts: int = 10
    login_lockout_seconds: int = 900
    # Seed users, format "user:pass[:perms],...". Parsed on startup; missing
    # users are created, existing users are never modified. The optional third
    # field is the tool grant: "*" = all tools (present and future), or tool
    # names joined by "+" (e.g. "coupons+accabonus"). Omitted = "*". Seeing a
    # tool means full use of it. Because ":" separates fields, a password cannot
    # contain ":". Example: "callum:s3cret-pw-here:*, dave:otherpw:coupons".
    auth_users: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
