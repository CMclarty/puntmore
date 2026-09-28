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
    # Largest coupon photo accepted, in MB. A phone photo is 2-6 MB, so this is
    # generous — it exists because the request body is held in memory and then
    # decoded and base64-encoded for OCR, so one oversized POST could exhaust a
    # 512 MB instance. Refusing beats resizing: OCR accuracy on a coupon's small
    # print depends on the full resolution.
    max_upload_mb: int = 25
    # Odds API in-memory cache TTL (seconds). Default 24 h keeps the free tier
    # (500 credits/month) safe. Only lower this on a paid Odds API tier —
    # 1 h would burn ~336 credits/day on a single sport.
    odds_cache_ttl: int = 86400
    # Minimum edge vs de-vigged Pinnacle to include in the feeds. Stored as an
    # edge fraction; the UI presents it as %EV (bet odds as a percentage of
    # fair: %EV = (1 + edge) × 100). Default -0.02 = the 98% EV preset.
    arb_min_edge: float = -0.02
    cards_min_edge: float = -0.02

    # --- Cards & corners (SPEC 1, docs/provider-verification.md) ------------
    # CARDS COME FROM ODDSPAPI (Sept 2026). TheOddsApi does not carry Paddy's
    # corners/cards (probe, 28 Sept: one book with corners, none with cards,
    # no Paddy) — see cards/oddspapi.py. OddsPapi bills per REQUEST, and one
    # odds-by-tournaments request returns every fixture in the tournaments
    # named, so the cost is (sweeps/day x requests/sweep), independent of how
    # many games are on:
    #   48 sweeps/day (30-min TTL) x 3 requests (13 tournaments, 5 a request)
    #   x 2 books (Paddy + Pinnacle) = 288/day + ~3 reference = ~8,800/month.
    # Halving the TTL doubles that; dropping the fair book halves it.
    oddspapi_api_key: str = ""
    # Competitions to sweep: an OddsPapi tournament id, or a competition NAME
    # matched exactly against OddsPapi's own list. Ids confirmed live by
    # /api/cards/oddspapi-check (28 Sept 2026): 17 Premier League, 18
    # Championship, 24 League One, 8 LaLiga, 23 Serie A, 35 Bundesliga, 34
    # Ligue 1, 238 Liga Portugal, 7 Champions League, 679 Europa League. The
    # Nations League is by NAME: the Sofascore id assumed for it (10783) is not
    # in OddsPapi's list. A name that matches nothing is reported in the
    # sweep's `unresolved_tournaments`, never silently dropped. 11 "FIFA World
    # Cup Qualification, UEFA" and 27 "UEFA EURO, Qualification" are by id
    # (read off the live tournament search) because their names hold commas.
    oddspapi_tournament_ids: str = (
        "17,18,24,8,23,35,34,238,7,679,UEFA Nations League,11,27"
    )
    oddspapi_tournaments_per_request: int = 5
    oddspapi_ttl_seconds: int = 1800
    # A ceiling, not a target: ~290/day is the planned spend, so this only
    # bites on a runaway. Keep it below (monthly plan / 30).
    oddspapi_daily_request_budget: int = 400
    # The FAIR side from the same vendor (Sept 2026). Pinnacle on OddsPapi
    # carries a 5-line corners ladder and a 5-line "Bookings" (card count)
    # ladder — /api/cards/oddspapi-check, Nations League, 28 Sept — and it
    # sits on the SAME fixture id as Paddy's, so the join is exact rather
    # than bettingiscool's team-name match. Blank = bettingiscool as before.
    oddspapi_fair_book: str = "pinnacle"
    # Cards: fixtures further than this from kickoff are not priced. OddsPapi
    # bills per REQUEST, not per fixture, so this dial costs nothing — it only
    # decides which of the fixtures already fetched are shown.
    cards_window_hours: float = 48.0
    # How often the cards sweep runs. Reads go through OddsPapi's cache
    # (`oddspapi_ttl_seconds`), so a tick inside the TTL costs nothing.
    cards_fast_interval_minutes: int = 5
    # AccaBonus stops pulling when TheOddsApi's own remaining-quota header
    # falls to this. Goes when AccaBonus moves to OddsPapi.
    cards_quota_reserve: int = 1500

    # Price every line PADDY POWER quotes off Pinnacle's fitted ladder, rather
    # than only the lines both books happen to pick. The two books rarely agree
    # on a line, so without this most of Paddy's board is invisible.
    #
    # The model can never beat Pinnacle — between their rungs it interpolates
    # their own opinion, outside them it decays toward a shape assumption. The
    # hard bounds, the band requirement and the extrapolation penalty
    # (pricing/counts) are what keep that honest; turn this off rather than
    # relaxing any of them.
    cards_use_ladder_model: bool = True

    # Discord alerts for the cards sweep. OFF while the feed is being proven —
    # a value tool that alerts before anyone has checked its prices trains you
    # to trust it. The coupon tracker's alerts are unaffected.
    cards_discord_alerts: bool = False
    # Optional comma-separated override of the AccaBonus sport keys. Empty =
    # keys discovered from the free /v4/sports list, filtered to Acca Boost-
    # eligible competitions, soccer excluded (accabonus/arbs.py). Set to a
    # single key (e.g. "tennis_atp") to cap quota burn at ~1 API request per
    # 24 h while testing live data.
    accabonus_sport_keys: str = ""

    # How long an AccaBonus h2h pull is reused. SEPARATE from odds_cache_ttl,
    # which is shared with other consumers and stays at 24h.
    #
    # COST: 1 credit per sport key per pull (bookmakers are named, so one
    # region-equivalent). At 24h that is ~1 credit/key/day; at 1h it is 24.
    # Discovery typically finds 5-15 live keys, so hourly is roughly
    # 120-360 credits/DAY — 3.6k-10.8k a month, on top of the cards sweep's
    # ~17k. Against a 20k plan those do not both fit at the top of that range.
    #
    # Two ways to keep it inside: pin `accabonus_sport_keys` to a short list
    # while testing, or raise this back up once the tool has been eyeballed.
    # The quota reserve is the backstop either way — AccaBonus stops before the
    # reserve for the same reason the cards sweep does.
    accabonus_cache_ttl: int = 3600
    # Coupon tracker odds source (behind the OddsProvider seam).
    # "bettingiscool" (default, user decision July 2026) prices coupon legs
    # Pinnacle-referenced via BettingIsCoolProvider. There is no fallback
    # source: without its key the mock is used, which prices nothing, and
    # /api/ready reports it as degraded. (The TheOddsApi fallback was removed
    # in Sept 2026 — it could price six of the nine coupon types not at all.)
    coupon_odds_source: str = "bettingiscool"
    bettingiscool_api_key: str = ""
    # Cache TTL (seconds) for the coupon tracker's odds pulls, reused across
    # every leg and the 10-min refresh loop. Independent of odds_cache_ttl
    # (which Goalscorers and AccaBonus use).
    #
    # Default 600 = 10 min, matching the refresh cadence, which is what
    # bettingiscool's quota supports: 500,000 tokens per UTC DAY, and a full
    # refresh of a 10-leg coupon costs ~250. Prices stay fresh.

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
    # Minutes past a fixture's kickoff before its selection is cleared from the
    # coupon. A coupon's WINDOW stays live for days after an individual fixture
    # has played, so the kickoff is what decides whether a row is still a bet.
    # The grace covers a printed kickoff being a few minutes out.
    leg_kickoff_grace_minutes: int = 30
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
