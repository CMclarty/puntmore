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

    # --- Cards & corners (SPEC 1, docs/provider-verification.md) ------------
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
    # A ceiling, not a target: ~146/day is the planned spend, so this only
    # bites on a runaway. Keep it below (monthly plan / 30).
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
    # Goalscorers is PARKED (5 Nov 2026): its odds vendor is gone and OddsPapi
    # is the only source. Off until rebuilt on OddsPapi (PLAN.md, Parked).
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
    # The Hub link (/api/2up/ws, hub/PROTOCOL.md). The client id whose bets may
    # carry source_scope "owner" (Bet Finder): such a bet is never sent to any
    # other client. And how often each connected client's pending bet lists are
    # polled from the 2-Up engine, in seconds.
    hub_owner_client_id: str = "owner"
    hub_poll_seconds: float = 5.0

    # --- 2-Up engine (app/twoup/, docs/2up-engine-spec.md) -------------------
    # Part of the 365 tool, behind the `accabonus` grant. Prices bet365's 2 Goals
    # Ahead Early Payout off OddsPapi (bet365 + Pinnacle 1X2), decides stakes per
    # client and queues bet lists in its ledger. It NEVER places a bet and never
    # connects to the Hub: the Hub endpoint sends what the ledger has queued.
    # Mounts /api/2up and runs the decision job. Off = neither.
    enable_twoup: bool = True
    # Global kill switch: no bet lists are produced or handed out for ANY
    # client while true. Per-client switches live in the ledger (POST
    # /api/2up/clients/{id}/kill). An .env change, no code change.
    twoup_kill_switch: bool = False
    # Clients and their sub-client accounts, created on startup if missing and
    # NEVER modified once they exist (bankroll, flags and balances are the
    # ledger's): "client_id[:owner]=acc1+acc2;other=acc3". Every client
    # created here starts in DRY-RUN with the kill switch off.
    twoup_clients: str = ""
    # Qualifying competitions (ids or names, as `oddspapi_tournament_ids`).
    # bet365's early payout runs on the major leagues and the European club
    # cups. The default is the first ten entries of the shared list ON
    # PURPOSE: chunked 5 a request they are the same two chunks the coupon
    # tracker and the Paddy tool already fetch from Pinnacle, so Pinnacle costs
    # nothing extra and bet365 adds 2 requests per refresh (tiered, cached by
    # the client like every other OddsPapi call).
    twoup_tournament_ids: str = "17,18,24,8,23,35,34,238,7,679"
    # How long one priced feed is reused by the decision job and GET /2up/feed.
    # The OddsPapi client's own tiered cache decides what is actually billed.
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
    # Fractional Kelly on the client's POOLED bankroll, then a hard ceiling on
    # any one selection's total stake as a fraction of that bankroll.
    twoup_kelly_fraction: float = 0.25
    twoup_max_selection_fraction: float = 0.02
    # Account preservation (spec, "Account-preservation rules"). Bets are
    # produced only inside this local window, and each bet's not_before /
    # not_after is clipped to it.
    twoup_window_start: str = "07:30"
    twoup_window_end: str = "22:00"
    twoup_timezone: str = "Europe/London"
    # Hard max bets per account per London day — every bet recorded counts,
    # failed ones included (a refused slip is still activity on the account).
    twoup_max_bets_per_account_day: int = 20
    # Dynamic stake ceiling per bet: never above the hard cap, never above
    # this fraction of the account's own balance. No floor.
    twoup_stake_hard_cap: float = 200.0
    twoup_stake_balance_fraction: float = 0.10
    # Stake growth: a stake may not exceed the mean of the account's trailing
    # stakes by more than this fraction, whatever the account has won. Until
    # the account has `twoup_growth_min_history` stakes, the initial cap holds.
    twoup_stake_growth_cap: float = 0.25
    twoup_trailing_stakes: int = 20
    twoup_growth_min_history: int = 5
    twoup_initial_stake_cap: float = 10.0
    # The decision job's cadence, in minutes. It reads the cached feed, so a
    # tighter cadence costs CPU, not OddsPapi requests.
    twoup_decision_minutes: int = 10

    # --- Bet Finder orchestrator (app/betfinder/) ----------------------------
    # Turns the Hub's `tip` frames (the `tips` table) into OWNER-ONLY 2-Up candidates.
    # It never fetches anything (no IMAP, no tipster pages): tips arrive over
    # the Hub link, prices come from the 2-Up feed's cache. Off = no job and
    # `extra_candidates` returns nothing; the routes still read.
    enable_betfinder: bool = True
    # Sources created on startup if missing, NEVER modified once they exist
    # (trust, active and stake mode are set by hand through the routes):
    # "name:trust=0.6,active=1,stake=advised,kind=hub;other:trust=0.4".
    # Keys are optional; defaults are trust 0.5, inactive, stake auto, kind hub.
    # A tip from a source not listed creates it INACTIVE at trust 0.5.
    betfinder_sources: str = ""
    # One advised unit in pounds: a tip's advised_stake_units x this is its
    # stake in `advised` mode, before the trust weight and the 2-Up caps.
    betfinder_unit_stake: float = 5.0
    # Advised units above this are not believed: the tip is quarantined.
    betfinder_max_units: float = 10.0
    # Edge floor for Bet Finder rows, in probability units, p_win - 1/price
    # with rescue 0 (the 2-Up floor is for the 2-up edge, which a tip does not
    # claim). 0 = never back a tip bet365 prices below Pinnacle's fair line.
    betfinder_min_edge: float = 0.0
    # How long after its tip_time an unmatched tip keeps being retried (the
    # fixture may not be in the 2-Up feed's horizon yet), and how far after
    # the tip a fixture may kick off and still be the one it means.
    betfinder_match_days: float = 3.0
    # A priced tip older than this is not offered to the decision: its price
    # is re-read every pass, so a stale one means the fixture left the feed.
    betfinder_max_price_age_minutes: int = 30
    # The pipeline's cadence, in minutes. It prices off the 2-Up feed's CACHE
    # only (never a refresh), and the 2-Up pass re-prices open tips off the
    # feed it has just read, so the Bet Finder adds no OddsPapi request.
    betfinder_minutes: int = 5
    # Rolling window for the per-source strike rate and ROI (settled tips).
    betfinder_stats_window: int = 50

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
