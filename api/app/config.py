from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Odds provider — the-odds-api.com. Falls back to MockOddsProvider if absent.
    odds_api_key: str = ""
    # OCR provider keys — set one. GOOGLE_API_KEY takes priority; falls back to
    # ANTHROPIC_API_KEY; falls back to MockOCRProvider if neither is present.
    google_api_key: str = ""
    anthropic_api_key: str = ""
    discord_webhook_url: str = ""
    # Per-tool / per-edge Discord channels, e.g.
    # "coupons=URL paddy=URL paddy@0.08=URL 365=URL" — see
    # notifications/routes.py. Unrouted alerts use DISCORD_WEBHOOK_URL.
    discord_routes: str = ""
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
    # PADDY POWER comes from TheOddsApi's PER-EVENT endpoint; Pinnacle comes
    # from bettingiscool. That split is a cost decision, not a preference:
    # TheOddsApi bills (markets x regions) per event, Paddy Power is a `uk`
    # book and Pinnacle an `eu` one, so asking one vendor for both spans two
    # regions. Asking only for Paddy Power is unambiguously ONE region, and
    # bettingiscool is Pinnacle-native on a plan billing per DAY rather than
    # per month.
    #
    # NOTE: regions in TheOddsApi are where the BOOKMAKER is licensed, not
    # where the league is. Paddy Power prices La Liga and Serie A perfectly
    # well from the `uk` region; the league is chosen by sport key.
    cards_market_keys: str = "alternate_totals_corners,alternate_totals_cards"
    # PADDY POWER VIA ODDSPAPI (Sept 2026). TheOddsApi does not carry Paddy's
    # corners/cards (probe, 28 Sept: one book with corners, none with cards,
    # no Paddy). With a key set, Paddy's side comes from OddsPapi instead —
    # see cards/oddspapi.py. OddsPapi bills per REQUEST, and one
    # odds-by-tournaments request returns every fixture in the tournaments
    # named, so the cost is (sweeps/day x requests/sweep), independent of how
    # many games are on:
    #   refreshes x 3 requests (13 tournaments, 5 a request) x 2 books
    #   (Paddy + Pinnacle). Refreshes follow kickoffs (oddspapi_ttl_tiers):
    #   ~15k/month at the defaults. Dropping the fair book halves it.
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
    oddspapi_ttl_seconds: int = 1800   # superseded by oddspapi_ttl_tiers; unused
    # How stale a competition chunk's odds may get, by how soon its NEXT
    # fixture kicks off (read off the cached response itself, so choosing the
    # gear costs nothing — OddsPapi bills /fixtures too). "seconds:ttl" pairs,
    # tightest first; the last applies to anything further out. At the
    # defaults: 2 min inside 45 min, 5 min inside 2h, 15 min inside 6h, then
    # hourly — ~15k requests/month on the 13 competitions x 2 books.
    oddspapi_ttl_tiers: str = "2700:120,7200:300,21600:900,inf:3600"
    # A ceiling, not a target: ~146/day is the planned spend, so this only
    # bites on a runaway. Keep it below (monthly plan / 30).
    # Plan: 100,000/month (Oct 2026) = 3,333/day. The cap sits under that so
    # one runaway day cannot spend the month.
    oddspapi_daily_request_budget: int = 3000
    # The FAIR side from the same vendor (Sept 2026). Pinnacle on OddsPapi
    # carries a 5-line corners ladder and a 5-line "Bookings" (card count)
    # ladder — /api/cards/oddspapi-check, Nations League, 28 Sept — and it
    # sits on the SAME fixture id as Paddy's, so the join is exact rather
    # than bettingiscool's team-name match. Blank = bettingiscool as before.
    oddspapi_fair_book: str = "pinnacle"
    # Major UK/Euro competitions. The fixture-list endpoint is FREE, so listing
    # a key that is out of season or has no fixtures costs nothing.
    # An unknown or out-of-season key 404s, which is FREE and cached empty, so a
    # wrong key costs nothing — but it also returns nothing silently. Verify a
    # new key against /v4/sports (also free) rather than assuming the spelling.
    cards_sport_keys: str = (
        "soccer_epl,soccer_efl_champ,soccer_england_league1,"
        "soccer_spain_la_liga,soccer_italy_serie_a,"
        "soccer_germany_bundesliga,soccer_france_ligue_one,"
        "soccer_portugal_primeira_liga,"
        "soccer_uefa_champs_league,soccer_uefa_europa_league,"
        # Internationals (Sept 2026). Without these the sweep found nothing
        # at all in an international window, which the diagnostics reported
        # as "no fixture within 48h" — true, and entirely the key list's
        # doing. Club football mostly pauses in a window, so these replace
        # club spend rather than adding to it: a Nations League window is
        # ~54 fixtures x ~43 credits, against ~40 top-flight fixtures a
        # normal weekend. Qualifier keys cost nothing while out of season.
        # Friendlies deliberately left out: noisy games, often unpriced.
        "soccer_uefa_nations_league,soccer_uefa_euro_qualification,"
        "soccer_fifa_world_cup_qualifiers_europe"
    )
    # TIERED REFRESH. Corners/cards markets are posted near kickoff and barely
    # move until the last hour, so a flat 5-minute sweep over a wide window
    # spends most of the budget re-reading prices that have not changed — and
    # buys empty responses on fixtures whose markets are not up yet.
    #
    # Cost per fixture = sweeps x markets, so the WINDOW is the expensive dial,
    # not the interval. 4h at 30 min + 1h at 5 min = 18 sweeps against the 48 a
    # flat 5-minute 4-hour window would cost.
    # THREE tiers, because the window and the cadence are separate dials and
    # widening one without slowing the other is what blows a budget.
    #
    # Cost per fixture is sweeps x markets. At a 4h window that was
    # 6 slow + 12 fast = 18 sweeps = 36 credits. Widening to 48h at the same
    # 30-minute cadence would be ~106 sweeps = ~212 credits per fixture, or
    # roughly 83k/month across the major leagues — four times a 20k plan.
    #
    # So the far tier is deliberately coarse. Three days out the question is
    # only "has the market been posted yet", which does not need asking every
    # half hour. Corners/cards are rarely up more than a few hours before
    # kickoff, so most far-tier calls return nothing — and TheOddsApi bills
    # (markets RETURNED x regions), so an empty response should cost little or
    # nothing. That is unverified, which is exactly what the daily ceiling is
    # for.
    cards_window_hours: float = 48.0       # start watching this far out
    cards_far_window_hours: float = 4.0    # coarse polling beyond this
    cards_fast_window_hours: float = 1.0   # switch to the fast cadence here
    # Every 12h from two days out to four hours. Twice a day is ample for the
    # only question being asked out there — "is the market up yet" — and a
    # fixture still gets ~4 looks before it reaches the 30-minute tier.
    #
    # 8h was affordable at this window (92% of plan) and 12h is 85%. The
    # difference in usefulness is one extra look at a fixture whose market is
    # almost certainly not posted; the difference in risk is all the headroom.
    # Tighten it once x-requests-last has reported the REAL per-call cost —
    # if that comes back as 1 rather than 2, everything here halves.
    cards_far_interval_minutes: int = 720
    cards_slow_interval_minutes: int = 30
    cards_fast_interval_minutes: int = 5
    # HARD CEILING on per-event calls in one UTC day. The exact credit cost of
    # a per-event call is not yet confirmed (docs/provider-verification.md says
    # markets x regions; routers/accabonus.py says naming bookmakers bills as
    # one) — so the sweep measures the real rate from the response headers and
    # STOPS rather than discovering it when the quota is gone.
    # Stop when the vendor says this much of the monthly quota is left. This is
    # the REAL guard: TheOddsApi reports remaining quota on every response, so it
    # is authoritative where a locally-counted budget is a guess — it survives a
    # restart, it already reflects the true per-call cost, and it does not depend
    # on the (markets x regions) figure nobody has measured.
    #
    # The reserve exists so a runaway sweep cannot take the coupon tracker's
    # fallback and the goalscorer scans down with it.
    cards_quota_reserve: int = 1500

    # Secondary rate limiter: a per-UTC-day ceiling, so one bad day cannot spend
    # a month even before the reserve is reached. 650 x 30 = 19,500, which sits
    # UNDER a 20k plan rather than over it — 700/day was 21,000/month, i.e. the
    # guard permitted more than the budget it was guarding.
    cards_daily_call_budget: int = 650

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
    # Paddy lines never shown, as "market:line" pairs. Paddy SHOPS don't carry
    # every line its website does, and the team bets in shops (Callum, Oct
    # 2026: no 11.5 corners). Pinnacle's rung at the same line still feeds the
    # fit — only Paddy's price is dropped.
    paddy_excluded_lines: str = "corners:11.5"

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
    # Discord alerts for the 365 acca boost: a pick is posted when it first
    # clears this edge or its value tier changes. The check runs every
    # `accabonus_alert_minutes` and reads THROUGH the cache above, so it costs
    # no more than the cache TTL already allows.
    accabonus_discord_alerts: bool = True
    accabonus_alert_min_edge: float = 0.02
    accabonus_alert_minutes: int = 30
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
    # Minutes past a fixture's kickoff before its selection is cleared from the
    # coupon. A coupon's WINDOW stays live for days after an individual fixture
    # has played, so the kickoff is what decides whether a row is still a bet.
    # The grace covers a printed kickoff being a few minutes out.
    leg_kickoff_grace_minutes: int = 30
    # Static API key for GET /state (machine consumers, e.g. dashboards/bots).
    # Sent as the X-API-Key header. Blank = endpoint disabled (503).
    state_api_key: str = ""
    # Key the local Bookie Bashing script sends (X-API-Key) with fair saves
    # prices to POST /api/paddy/saves-fair. Blank = endpoint disabled (503).
    saves_ingest_key: str = ""
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
