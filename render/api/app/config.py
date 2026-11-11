from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
    # A coupon and its photo are deleted this many days after upload (PLAN.md
    # item 4): a coupon's window is a week at most, so after 8 days nothing on
    # it can still be bet. Rows and image files go together, never one alone.
    coupon_cleanup_days: int = 8
    # Largest coupon photo accepted, in MB. A phone photo is 2-6 MB, so this is
    # generous — it exists because the request body is held in memory and then
    # decoded and base64-encoded for OCR, so one oversized POST could exhaust a
    # 512 MB instance. Refusing beats resizing: OCR accuracy on a coupon's small
    # print depends on the full resolution.
    max_upload_mb: int = 25
    # Minimum edge vs de-vigged Pinnacle to include in the Paddy feed. Stored as
    # an edge fraction; the UI presents it as %EV (bet odds as a percentage of
    # fair: %EV = (1 + edge) × 100). Default -0.02 = the 98% EV preset.
    cards_min_edge: float = -0.02

    # --- Paddy (the `cards` settings) ----------------------------------------
    # PADDY POWER VIA ODDSPAPI (Sept 2026). No other vendor carries Paddy's
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
    # How far ahead OddsPapi fixtures are priced. NOT a cost dial:
    # odds-by-tournaments returns every fixture in the competitions named
    # whatever the window, so trimming it to 48h only threw away games already
    # paid for (Oct 2026: 57 of 185 listed kept, so a Friday-morning sweep
    # missed Sunday). A week covers a midweek round and the weekend after it.
    oddspapi_window_hours: float = 168.0
    # How stale a competition chunk's odds may get, by how soon its NEXT
    # fixture kicks off (read off the cached response itself, so choosing the
    # gear costs nothing — OddsPapi bills /fixtures too). "seconds:ttl" pairs,
    # tightest first; the last applies to anything further out. At the
    # defaults: 2 min inside 45 min, 5 min inside 2h, 15 min inside 6h, then
    # hourly — ~15k requests/month on the 13 competitions x 2 books.
    oddspapi_ttl_tiers: str = "2700:120,7200:300,21600:900,inf:3600"
    # A ceiling, not a target, so this only bites on a runaway. Keep it below
    # (monthly plan / 30). ~146/day was the planned spend before the 2-Up
    # feed. Its `twoup_refresh` job runs 24/7 every `twoup_feed_ttl` (5 min)
    # and asks bet365 for two chunks a pass, billed only when the tiered cache
    # above has expired: ~48/day while neither chunk has a kick-off within 6h,
    # rising towards a ceiling of ~576/day (both chunks re-billed every pass)
    # through a run of kick-offs. Its Pinnacle chunks are the coupon
    # tracker's and Paddy's, so mostly cache hits; when nothing else keeps
    # them warm they bill at the same tiered rate. Not measured yet: read
    # `feed.last_pass.requests_today` on /api/2up/diagnostics before
    # trusting a figure.
    # Plan: 100,000/month (Oct 2026) = 3,333/day. The cap sits under that so
    # one runaway day cannot spend the month.
    oddspapi_daily_request_budget: int = 3000
    # The FAIR side from the same vendor (Sept 2026). Pinnacle on OddsPapi
    # carries a 5-line corners ladder and a 5-line "Bookings" (card count)
    # ladder — /api/cards/oddspapi-check, Nations League, 28 Sept — and it
    # sits on the SAME fixture id as Paddy's, so the join is exact rather
    # than a team-name match. Blank = no fair side at all (the feed stays empty
    # and /paddy/diagnostics says so).
    oddspapi_fair_book: str = "pinnacle"
    # Paddy lines never shown, as "market:line" pairs. Paddy SHOPS don't carry
    # every line its website does, and the team bets in shops (Callum, Oct
    # 2026: no 11.5 corners). Pinnacle's rung at the same line still feeds the
    # fit — only Paddy's price is dropped.
    paddy_excluded_lines: str = "corners:11.5"

    # --- Hills via odds-api.net (Nov 2026) -----------------------------------
    # William Hill and Pinnacle prices for Hills (cards/oddsapinet_client.py).
    # The plan: 250,000 credits/month, 120 requests/min, 1 credit per call
    # (failures included). Blank key keeps Hills on OddsPapi.
    oddsapinet_api_key: str = ""
    # How far ahead fixtures are priced, as `oddspapi_window_hours`.
    oddsapinet_window_hours: float = 168.0
    # A ceiling, not a target: the plan's 250,000 a month over 31 days
    # (Callum, 11 Nov 2026), so no month can overspend. Every request counts.
    oddsapinet_daily_credit_budget: int = 8000
    # A warmish board (Callum, 11 Nov 2026: "could we get a warmish board, with
    # slow pre-loads"): the `hills_warm` job refreshes boards with nobody on
    # the page, every `_every_min` minutes (0 turns it off). A board is warmed
    # when it is over `_far_s` old on a game more than `_near_hours` from
    # kick-off, over `_near_s` old inside that, or missing. It fetches nothing
    # once fewer than `_reserve` credits are left today, so the page always
    # has them. About 1,000-2,500 credits a day of the 8,000.
    oddsapinet_warm_every_min: int = 10
    oddsapinet_warm_far_s: int = 7200
    oddsapinet_warm_near_s: int = 900
    oddsapinet_warm_near_hours: float = 6.0
    oddsapinet_warm_reserve: int = 2000
    # The page reuses a board on a game inside `oddsapinet_warm_near_hours`
    # for this long rather than the vendor's 60 s (clamped to 120): about 12
    # snapshots an hour per near game with a tab open, not 30.
    oddsapinet_page_near_ttl_seconds: int = 300
    # The last credits of the day are for BB Boost's legs and settling only:
    # the page's pass and the warm job send nothing once this many or fewer
    # are left (the warm's reserve sits above it). Legs and results may spend
    # down to zero.
    oddsapinet_settle_floor: int = 300

    # Discord alerts for the cards sweep. OFF while the feed is being proven —
    # a value tool that alerts before anyone has checked its prices trains you
    # to trust it. The coupon tracker's alerts are unaffected.
    cards_discord_alerts: bool = False
    # Coupon tracker odds source (behind the OddsProvider seam).
    # "oddspapi" (default, Oct 2026) prices coupon legs Pinnacle-referenced via
    # OddsPapiCouponProvider (the provider-agnostic GridCouponProvider over an
    # OddsPapi board). It is the ONLY source (5 Nov 2026); without an
    # ODDSPAPI_API_KEY the tracker runs on the mock and main.py says so.
    coupon_odds_source: str = "oddspapi"
    # Cache TTL (seconds) for the coupon tracker's odds pulls, reused across
    # every leg and the 10-min refresh loop. Independent of odds_cache_ttl
    # (there is no second cache now).
    #
    # Default 600 = 10 min, matching the refresh cadence. OddsPapi is billed per
    # request, but the whole coupon board is a handful of bulk requests per
    # refresh (one per tournament chunk), well within the daily budget.
    #
    coupon_odds_cache_ttl: int = 600
    # Tool visibility flags: whether a tool's API router is mounted (main.py)
    # and, mirrored in the frontend, whether its nav tab/route appears. The
    # acca boost returns as a tab of the 365 tool on OddsPapi (PLAN item 11).
    enable_cards: bool = False
    # First-goalscorer value alerts (bet365 vs de-vigged Pinnacle). Surfaced in
    # the UI when on. The scheduler builds a daily watchlist of major fixtures
    # and scans each at kickoff − 45 min (Pinnacle only posts the market near KO
    # for majors), storing rows and alerting Discord on new value.
    # Goalscorers is PARKED (5 Nov 2026): its odds vendor is gone. Off until
    # rebuilt on OddsPapi (PLAN.md, Parked).
    enable_goalscorers: bool = False
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

    # --- 2-Up (app/twoup/, docs/2up-engine-spec.md) --------------------------
    # Part of the 365 tool, behind the `accabonus` grant. Prices bet365's 2 Goals
    # Ahead Early Payout off OddsPapi (bet365 + Pinnacle 1X2) and serves the value
    # bets as a one-way feed, read by the Bet Placer. It NEVER places a bet and
    # keeps no account.
    # Mounts /api/2up. Off = no router.
    enable_twoup: bool = True
    # --- Finder (app/finder/, docs/finder-spec.md) ----------------------------
    # Callum's tips board on `me`, behind the `finder` grant.
    # Keeps, serves and expires the rows HubAI posts from the Hub; never loads
    # a page, never prices a tip, never places a bet. Off = no router, no jobs,
    # no `finder` tool in the nav.
    enable_finder: bool = True
    # Global kill switch: GET /api/2up/feed returns no rows (status.killed)
    # and the `2up` source of /api/value returns none (errors["2up"] says
    # killed); /api/2up/diagnostics reports it. An .env change, no code
    # change. The refresh job keeps pricing, so lifting it serves a fresh board.
    twoup_kill_switch: bool = False
    # Qualifying competitions (ids or names, as `oddspapi_tournament_ids`).
    # bet365's early payout runs on the major leagues and the European club
    # cups. The default is the first ten entries of the shared list ON
    # PURPOSE: chunked 5 a request they are the same two chunks the coupon
    # tracker and the Paddy tool already fetch from Pinnacle, so Pinnacle costs
    # nothing extra and bet365 adds 2 requests per refresh (tiered, cached by
    # the client like every other OddsPapi call).
    twoup_tournament_ids: str = "17,18,24,8,23,35,34,238,7,679"
    # The `twoup_refresh` job's interval (seconds): how often the served board
    # is re-priced. The OddsPapi client's own tiered cache decides what is
    # actually billed.
    twoup_feed_ttl: int = 300
    # Which fixtures are candidates: kicking off within this many hours and at
    # least this many minutes from now (a bet must have time to be placed).
    twoup_horizon_hours: float = 24.0
    twoup_min_lead_minutes: int = 15
    # Edge floor, in PROBABILITY units: P(win) + P(rescue) - 1/price. 0.005 =
    # half a percentage point of payout probability over the price's.
    twoup_min_edge: float = 0.005
    # bet365 price band considered. Very short prices tie up stake for pennies;
    # long ones go two up too rarely for the rescue to matter.
    twoup_min_price: float = 1.25
    twoup_max_price: float = 8.0
    # Where the rescue-rate table lives. Blank = app/twoup/rescue_table.json.
    twoup_rescue_table_path: str = ""

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

    # The host Me (Callum's personal tools) is served on (app/site.py), e.g.
    # "me.puntmore.com". Blank (the default) = no Me: every tool, Finder and the
    # personal tools included, is served and signed in to on the main domain,
    # and nothing redirects to Me. Set it only once the host resolves to this
    # service: its DNS record and Render custom domain exist (a set value whose
    # host does not resolve sends the personal tools to a DNS error).
    me_host: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
