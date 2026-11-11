# Hills on odds-api.net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hills (the William Hill board, BB Boost's target, legs and bet log) prices and
settles from odds-api.net, Puntmore's second odds provider beside OddsPapi; Hills shows
William Hill's 11.5 corners line and no saves (Callum, 11 Nov 2026).

**Architecture:** A new source class, `OddsApiNetHillsSource`, sits at
`app.state.hills_client`. It turns odds-api.net's per-event snapshots (William Hill and
Pinnacle in one call) into the events the unchanged counts pipeline reads. It refreshes its
boards in one background pass at a time when a page asks, and settles BB Boost legs from
`/events/{id}/results`. `hills/legs.py` and `hills/settle.py` call the source's own methods
when it has them, so the OddsPapi path is untouched while it lives. `main.py` picks the
vendor: `ODDSAPINET_API_KEY` set means odds-api.net.

**Tech Stack:** Python 3 / FastAPI, httpx (`MockTransport` in tests), SQLAlchemy, pytest;
React + Vitest for one frontend line.

**Spec:** `docs/superpowers/specs/2026-11-11-hills-odds-api-net-design.md`

## Global Constraints

- `docs/decisions.md` wins. The register edit in Task 6 is Callum's to approve; the pull
  request does not merge without it.
- No product code reads a vendor's de-vigged or fair fields. Snapshots are requested with
  `price_fields=odds` only. Fairs are de-vigged in-house by the unchanged `value.py` /
  `laddered.py`.
- Paddy, 2-Up and the coupon tracker are untouched. `cards/oddspapi.py` changes by one line
  (`vendor = "oddspapi"`). Paddy's feed and diagnostics output must not change.
- The API key never appears in the repo, a test, a fixture, a log line, an error string or a
  URL. It goes in the `X-API-Key` header only. Tests use `"k-test"`.
- Tests are hermetic: recorded fixtures in `render/api/tests/fixtures/odds_api_net/` (already
  committed with the design: `events.json`, `snapshot_1417142635.json`,
  `results_1595650036_final.json`, `results_2161612418_pending.json`), no network.
- Hard values: base URL `https://api.odds-api.net/v1`; request spacing 0.5 s; daily credit
  cap 8000 (`oddsapinet_daily_credit_budget`); window 168 h (`oddsapinet_window_hours`);
  listing cache 600 s; snapshot reuse `clamp(target_refresh_interval_seconds, 120, 3600)`;
  failed-event hold 600 s; at most 60 snapshots a pass; first-request wait 15 s; listing at
  most 3 pages.
- Ids: `oan:<vendor event id>` wherever Hills stores or serves an event id. A leg's
  `market_id` is `f"{key}:{line:g}"`, its `outcome_id` is `"Over"` or `"Under"`.
- `settled_by` for an odds-api.net settlement is `"odds-api.net"`; `last_sweep["source"]` is
  `"odds-api.net"`; `fair_source` on an event is `"odds-api.net"`, on the sweep
  `"odds-api.net:pinnacle"`.
- API changes are additive only (`docs/api.md`, `render/api/AGENTS.md`).
- Tests: `cd render/api && .venv/bin/python -m pytest tests/ -q`; frontend `cd render/frontend && npm test && npm run lint`.
- Commits end with the session's attribution lines. gitleaks runs on commit; never bypass it.

## Review Focus

1. Two page loads arrive together on a cold board, or `/hills/legs` lands during a pass:
   exactly one background pass runs, and the second request serves what is already held.
   Pinned by `test_one_pass_at_a_time` (Task 3).
2. A game kicks off between listing refreshes: it leaves the board at kick-off, not up to 10
   minutes later. Pinned by `test_a_game_leaves_the_board_at_kickoff` (Task 3).
3. A snapshot answers `complete: false`: its lines are used, and the event is named in
   `last_sweep.errors`. Pinned by `test_an_incomplete_snapshot_is_used_and_named` (Task 3).
4. A result where one side's record is not yet `final`: the match leg stays open and is never
   settled on half a count. Pinned by `test_a_match_leg_needs_both_sides_final` (Task 4).
5. A failed request raises from httpx: the key appears nowhere in logs, exception text or
   `last_sweep.errors`. Pinned by `test_the_key_is_never_in_an_error_or_a_log` (Task 1).

---

### Task 1: Settings and the odds-api.net HTTP client

**Files:**
- Modify: `render/api/app/config.py` (beside the `oddspapi_*` settings)
- Create: `render/api/app/cards/oddsapinet_client.py`
- Modify: `.env.example`, `render.yaml` (env list, after `ODDSPAPI_API_KEY`)
- Test: `render/api/tests/test_oddsapinet_client.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `settings.oddsapinet_api_key: str = ""`, `settings.oddsapinet_window_hours: float = 168.0`,
    `settings.oddsapinet_daily_credit_budget: int = 8000`
  - `BASE_URL = "https://api.odds-api.net/v1"`, `MIN_REQUEST_GAP = 0.5`
  - `class BudgetSpent(RuntimeError)`; `class RateLimited(RuntimeError)` with
    `retry_after: float | None`
  - `class OddsApiNetClient` with `vendor = "odds-api.net"`,
    `__init__(self, api_key: str, *, daily_budget: int = 8000, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic, today: Callable[[], date] = <UTC today>)`,
    attributes `requests_today: int`, `daily_budget: int`, methods `budget_left() -> int` and
    `get(path: str, **params) -> dict`.

- [ ] **Step 1: Write the failing tests** in `tests/test_oddsapinet_client.py`, each building the
  client on an `httpx.MockTransport` whose handler records requests:

```python
def test_the_key_goes_in_the_header_never_the_url():
    seen, c = _client(lambda req: httpx.Response(200, json={"items": []}))
    assert c.get("/events", sport="soccer", limit=1000) == {"items": []}
    assert seen[0].headers["X-API-Key"] == "k-test"
    assert str(seen[0].url) == "https://api.odds-api.net/v1/events?sport=soccer&limit=1000"
    assert "k-test" not in str(seen[0].url)

def test_every_request_is_counted_failures_too():
    # handler: first 500, then 200
    with pytest.raises(httpx.HTTPStatusError):
        c.get("/events")
    assert c.requests_today == 1
    c.get("/events")
    assert c.requests_today == 2

def test_a_spent_budget_sends_nothing():
    # daily_budget=2
    c.get("/a"); c.get("/b")
    with pytest.raises(BudgetSpent):
        c.get("/c")
    assert len(seen) == 2 and c.budget_left() == 0

def test_the_count_starts_again_each_utc_day():
    # today() returns date(2026, 11, 11) for two calls, then date(2026, 11, 12)
    assert c.requests_today == 1 after the first call on the 12th

def test_requests_are_spaced_half_a_second():
    # monotonic always 100.0; sleep records its argument
    c.get("/a"); c.get("/b")
    assert slept == [pytest.approx(0.5)]

def test_a_429_raises_rate_limited_and_is_not_retried():
    # handler: 429 with header Retry-After: 7
    with pytest.raises(RateLimited) as e:
        c.get("/events")
    assert e.value.retry_after == 7.0 and len(seen) == 1

def test_the_key_is_never_in_an_error_or_a_log(caplog):
    caplog.set_level(logging.DEBUG)
    # handler: 500
    with pytest.raises(httpx.HTTPStatusError) as e:
        c.get("/events/1/odds/snapshot", bookmakers="williamhill")
    assert "k-test" not in str(e.value) and "k-test" not in caplog.text
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_oddsapinet_client.py -q`
Expected: FAIL, `ModuleNotFoundError: app.cards.oddsapinet_client`.

- [ ] **Step 3: Implement `OddsApiNetClient`** in `app/cards/oddsapinet_client.py`. One
  `httpx.Client(base_url=BASE_URL, headers={"X-API-Key": key}, timeout=20, transport=transport)`.
  `get` holds one `threading.Lock` around the whole call: roll the day (UTC) → refuse with
  `BudgetSpent("daily credit budget spent")` when `requests_today >= daily_budget` → sleep out
  the 0.5 s gap → count the request → send → 429 raises `RateLimited` (Retry-After parsed as a
  float, else None) → other non-2xx `raise_for_status()` → return `.json()`. Log path and
  params only (never headers). Add the three settings to `config.py`, with a comment giving
  the plan's facts (250,000 credits/month, 120/min, 1 credit per call). Add
  `ODDSAPINET_API_KEY=` to `.env.example` (a comment: "Hills' William Hill and Pinnacle prices,
  odds-api.net; blank keeps Hills on OddsPapi") and `- key: ODDSAPINET_API_KEY` /
  `sync: false` to `render.yaml`, with a one-line comment.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_oddsapinet_client.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add render/api/app/config.py render/api/app/cards/oddsapinet_client.py \
  render/api/tests/test_oddsapinet_client.py .env.example render.yaml
git commit -m "Hills: an odds-api.net client (header key, credit count, daily cap, spacing)"
```

### Task 2: Mapping odds-api.net into the counts pipeline's events

**Files:**
- Create: `render/api/app/cards/oddsapinet.py` (the pure part; Task 3 adds the source class)
- Create: `render/api/tests/oan_fixtures.py` (loader, fake client, fixed clock)
- Test: `render/api/tests/test_oddsapinet_parse.py`

**Interfaces:**
- Consumes: `CORNERS, CARDS, GOALS, TEAM_CORNERS_HOME, TEAM_CORNERS_AWAY, TEAM_CARDS_HOME, TEAM_CARDS_AWAY, CORNERS_HANDICAP, CARDS_HANDICAP, WILLIAM_HILL, TOURNAMENT_SPORT_KEYS` from `app/cards/oddspapi.py`.
- Produces (in `app/cards/oddsapinet.py`):
  - `OAN_PREFIX = "oan:"`, `PINNACLE = "pinnacle"`, `VENDOR = "odds-api.net"`
  - `HILLS_LEAGUES: dict[str, int]` = `{"EPL": 17, "English FA Cup": 19, "EFL Cup": 21, "UEFA Champions League": 7, "English Championship": 18, "EFL Championship": 18, "League 1": 24, "La Liga": 8, "Serie A": 23, "Bundesliga": 35, "Ligue 1": 34, "Portuguese Primeira Liga": 238, "UEFA Europa League": 679, "UEFA Nations League": 23755, "World Cup Qualifiers Europe": 11}`
  - `@dataclass(frozen=True) class Listing: event_id: str; home: str; away: str; start: datetime; league: str; books: frozenset[str]` (`event_id` bare, no prefix)
  - `@dataclass class SnapshotLines: markets: dict[str, dict[str, list[dict]]]; as_of: dict[str, datetime]; stale_books: frozenset[str]; refresh_seconds: float | None; complete: bool`
  - `listed_events(body: dict, *, now: datetime, window_hours: float) -> list[Listing]` (Hills leagues, `now < start <= now + window`, deduped on `(home, away, start)`, kick-off order)
  - `fixture_of(listing: Listing) -> dict` → `{"id", "home", "away", "start", "tournament_id", "round": None}`
  - `parse_snapshot(body: dict) -> SnapshotLines`
  - `to_event(listing: Listing, lines: SnapshotLines) -> dict | None`
  - `priced_at(lines: SnapshotLines) -> datetime | None`
  - `snapshot_ttl(lines: SnapshotLines) -> float`
- Produces (in `tests/oan_fixtures.py`): `NOW = datetime(2026, 11, 11, 12, 40, tzinfo=timezone.utc)`,
  `load(name: str) -> dict`, `ARMENIA_EMPTY: dict`, `routes() -> dict[str, dict | Exception]`,
  `class FakeOan` (`vendor`, `requests_today`, `daily_budget = 8000`, `calls: list[tuple[str, dict]]`,
  `get(path, **params)` returning the route's dict, raising it when it is an exception,
  `RuntimeError(f"404 {path}")` when absent), `class Clock` (`now() -> datetime`,
  `mono() -> float`, `advance(seconds: float)`).
  `ARMENIA_EMPTY = {"event_id": "4229698994", "bookmaker_as_of_ts_ms": {"williamhill": 1794399000000}, "stale_bookmakers": [], "target_refresh_interval_seconds": 3599, "complete": True, "items": []}`.
  `routes()` maps `/events` → `events.json`, `/events/1417142635/odds/snapshot` → the
  snapshot, `/events/4229698994/odds/snapshot` → `ARMENIA_EMPTY`,
  `/events/1595650036/results` and `/events/2161612418/results` → the two results files.

Mapping rules (spec, "Market mapping"): `period == "full time"`, `is_available`, `odds > 1.0`;
books `williamhill` and `pinnacle` only; `metric` compared lower-case; totals read
`"<over|under> <n>"`, odd/even and quarter lines dropped; `total`+`corners|cards|standard` →
`CORNERS|CARDS|GOALS`; `team total`+`corners|cards`+`side` → the team keys; `handicap`+`corners|cards`
on Pinnacle only → `CORNERS_HANDICAP|CARDS_HANDICAP` as `{"name": "Home"|"Away", "point": home's handicap}`
(home item's line as given, away item's negated); everything else dropped. Event:
`{"id": "oan:<id>", "home_team", "away_team", "commence_time": "%Y-%m-%dT%H:%M:%SZ", "sport_key": TOURNAMENT_SPORT_KEYS.get(HILLS_LEAGUES.get(league), "soccer"), "fair_source": "odds-api.net", "bookmakers": [WH first, then Pinnacle when it has lines]}`;
`None` when William Hill has no lines. `snapshot_ttl` = `min(3600, max(120, refresh_seconds))`,
3600 when `refresh_seconds` is None. `priced_at` = the older of the William Hill and Pinnacle
`as_of` present.

- [ ] **Step 1: Write the failing tests** in `tests/test_oddsapinet_parse.py`:

```python
def test_listing_keeps_hills_competitions_in_the_window():
    got = listed_events(load("events.json"), now=NOW, window_hours=168)
    assert [l.event_id for l in got] == ["4229698994", "1417142635"]
    eng = got[1]
    assert (eng.home, eng.away, eng.league) == ("England", "Croatia", "UEFA Nations League")
    assert eng.start == datetime(2026, 11, 12, 19, 45, tzinfo=timezone.utc)
    assert "williamhill" in eng.books and "pinnacle" not in got[0].books

def test_listing_drops_started_games_and_games_beyond_the_window():
    assert [l.event_id for l in listed_events(load("events.json"),
            now=datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc), window_hours=168)] == ["1417142635"]
    assert [l.event_id for l in listed_events(load("events.json"), now=NOW, window_hours=30)] == ["4229698994"]

def test_a_game_listed_under_both_championship_names_is_kept_once():
    # two inline items, ids "1" ("English Championship") and "2" ("EFL Championship"), same teams and start_time
    assert [l.event_id for l in listed_events(body, now=NOW, window_hours=168)] == ["1"]

def test_fixture_of_maps_the_league_to_the_sofascore_id():
    eng = listed_events(load("events.json"), now=NOW, window_hours=168)[1]
    assert fixture_of(eng) == {"id": "oan:1417142635", "home": "England", "away": "Croatia",
                               "start": datetime(2026, 11, 12, 19, 45, tzinfo=timezone.utc),
                               "tournament_id": 23755, "round": None}
    # inline Listing with league "EPL" -> 17, "English FA Cup" -> 19, "EFL Cup" -> 21

def test_the_snapshot_maps_william_hill_and_pinnacle():
    lines = parse_snapshot(load("snapshot_1417142635.json"))
    wh, pin = lines.markets["williamhill"], lines.markets["pinnacle"]
    assert {k: len(v) for k, v in wh.items()} == {CORNERS: 20, CARDS: 12, GOALS: 12,
        TEAM_CORNERS_HOME: 20, TEAM_CORNERS_AWAY: 14, TEAM_CARDS_HOME: 6, TEAM_CARDS_AWAY: 10}
    assert {k: len(v) for k, v in pin.items()} == {CORNERS: 10, CARDS: 10, GOALS: 8,
        TEAM_CORNERS_HOME: 2, TEAM_CORNERS_AWAY: 2, TEAM_CARDS_HOME: 2, TEAM_CARDS_AWAY: 2,
        CORNERS_HANDICAP: 10, CARDS_HANDICAP: 6}
    assert {"name": "Over", "point": 9.5, "price": 1.8} in wh[CORNERS]
    assert {"name": "Under", "point": 10.0, "price": 1.64} in pin[CORNERS]
    assert {"name": "Home", "point": -2.5, "price": 1.51} in pin[CORNERS_HANDICAP]
    assert {"name": "Away", "point": -2.5, "price": 2.4} in pin[CORNERS_HANDICAP]
    assert {"name": "Away", "point": 0.5, "price": 1.53} in pin[CARDS_HANDICAP]
    assert not [o for o in pin[GOALS] if o["point"] in (2.25, 2.75, 3.25, 3.75, 4.25)]
    assert lines.as_of["williamhill"] == datetime(2026, 11, 11, 11, 26, 38, tzinfo=timezone.utc)
    assert lines.as_of["pinnacle"] == datetime(2026, 11, 11, 12, 36, 44, 578000, tzinfo=timezone.utc)
    assert (lines.refresh_seconds, lines.stale_books, lines.complete) == (3599, frozenset(), True)

def test_odd_even_halves_suspended_other_stats_and_other_books_are_dropped():
    # inline items: WH total corners "odd"; WH total corners "over 9.5" period "1st half";
    # WH total corners "over 8.5" is_available False; WH total shots "over 20.5";
    # WH player prop player saves "over 2.5"; betfair total STANDARD "over 2.5";
    # WH total corners "over 9.5" full time odds 1.8 (the one kept)
    assert parse_snapshot(body).markets == {"williamhill": {CORNERS: [{"name": "Over", "point": 9.5, "price": 1.8}]}}

def test_metric_case_is_ignored():
    # inline: pinnacle, total, metric "STANDARD", line "over 2.5", odds 1.47
    assert parse_snapshot(body).markets["pinnacle"][GOALS] == [{"name": "Over", "point": 2.5, "price": 1.47}]

def test_to_event_is_the_counts_model_shape():
    eng = listed_events(load("events.json"), now=NOW, window_hours=168)[1]
    ev = to_event(eng, parse_snapshot(load("snapshot_1417142635.json")))
    assert (ev["id"], ev["home_team"], ev["away_team"], ev["commence_time"], ev["sport_key"],
            ev["fair_source"]) == ("oan:1417142635", "England", "Croatia", "2026-11-12T19:45:00Z",
                                   "soccer_uefa_nations_league", "odds-api.net")
    assert [b["key"] for b in ev["bookmakers"]] == ["williamhill", "pinnacle"]

def test_without_william_hill_there_is_no_event_and_without_pinnacle_wh_rides_alone():
    arm = listed_events(load("events.json"), now=NOW, window_hours=168)[0]
    assert to_event(arm, parse_snapshot(ARMENIA_EMPTY)) is None
    # England's snapshot with every pinnacle item removed -> bookmakers keys == ["williamhill"]

def test_priced_at_is_the_older_book_and_ttl_follows_the_vendor():
    lines = parse_snapshot(load("snapshot_1417142635.json"))
    assert priced_at(lines) == datetime(2026, 11, 11, 11, 26, 38, tzinfo=timezone.utc)
    assert snapshot_ttl(lines) == 3599
    # refresh_seconds 60 -> 120; 7200 -> 3600; None -> 3600

def test_the_recorded_game_prices_through_the_unchanged_pipeline():
    eng = listed_events(load("events.json"), now=NOW, window_hours=168)[1]
    rows = feed.price_events([to_event(eng, parse_snapshot(load("snapshot_1417142635.json")))],
                             -0.5, "williamhill")
    run = laddered.LAST_RUNS["williamhill"]
    assert len(rows) == 80 and run["ladders_fitted"] == 3
    assert run["by_market"]["team_corners"]["split_disagreement"] == 0
    assert run["by_market"]["team_corners"]["split_sources"]["handicap"] == 2
    over95 = next(r for r in rows if r.market_key == CORNERS and r.line == 9.5 and r.side == "Over")
    assert over95.paddy_odds == 1.8 and over95.fair_odds == pytest.approx(2.05, abs=0.01)
    assert {r.event_id for r in rows} == {"oan:1417142635"}
    assert not [r for r in rows if "saves" in r.market_key]
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_oddsapinet_parse.py -q`
Expected: FAIL, `ImportError` on `app.cards.oddsapinet`.

- [ ] **Step 3: Implement** the pure functions and dataclasses in `app/cards/oddsapinet.py`,
  and `tests/oan_fixtures.py`. The module docstring records the vendor facts (spec, "What
  odds-api.net is") so the next agent does not re-probe them. `as_of` values come from
  `bookmaker_as_of_ts_ms` (ms → aware UTC datetime). `complete` defaults to True when absent.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_oddsapinet_parse.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add render/api/app/cards/oddsapinet.py render/api/tests/oan_fixtures.py \
  render/api/tests/test_oddsapinet_parse.py
git commit -m "Hills: map odds-api.net's William Hill and Pinnacle lines into the counts pipeline"
```

### Task 2b: Paddy's excluded lines apply to Paddy Power only

Callum, 11 Nov 2026: "add the 11.5 corner lines". `settings.paddy_excluded_lines`
(`corners:11.5`) is Paddy's shop rule; Hills (William Hill, on either vendor) prices every
half line its book quotes.

**Files:**
- Modify: `render/api/app/cards/laddered.py` (`_price_paddy_lines` and its callers: pass the
  `book` down; skip the exclusion unless `book == PADDY_POWER`)
- Test: `render/api/tests/` (the existing laddered/feed test module that covers exclusions;
  add there)

**Interfaces:** `_price_paddy_lines(..., book: str)` — the book `compute_laddered_value`
already receives. No public signature changes.

- [ ] **Step 1: Failing test** `test_william_hill_prices_the_11_5_corners_line`: the same
  event and outcomes priced with `book=PADDY_POWER` give no 11.5 corners row; with
  `book=WILLIAM_HILL` an 11.5 corners row is present (the row's line is 11.5).
- [ ] **Step 2:** Run it; FAIL. **Step 3:** Implement. **Step 4:** Full Render suite PASS
  (the existing Paddy exclusion tests still pass unchanged).
- [ ] **Step 5:** Commit `Hills: Paddy's excluded 11.5 corners line is Paddy's only`.

### Task 3: The source: board, background pass, freshness, diagnostics

**Files:**
- Modify: `render/api/app/cards/oddsapinet.py` (add the source class)
- Modify: `render/api/app/cards/feed.py` (`build_feed`'s `priced_at`; `diagnose`'s fair-side test and two verdicts)
- Test: `render/api/tests/test_oddsapinet_source.py`

**Interfaces:**
- Consumes: Task 1's `BudgetSpent`, `RateLimited`; Task 2's functions; `tests/oan_fixtures.py`.
- Produces: `class OddsApiNetHillsSource` with
  `__init__(self, client, *, window_hours: float | None = None, max_per_pass: int = 60, first_wait: float = 15.0, runner: Callable[[Callable[[], None]], None] | None = None, now: Callable[[], datetime] | None = None, monotonic: Callable[[], float] = time.monotonic)`;
  attributes `vendor = "odds-api.net"`, `book = "williamhill"`, `label = "William Hill"`,
  `_client`, `fixtures: list[dict]`, `boards: dict[str, SnapshotLines]` (keyed `oan:<id>`),
  `last_sweep: dict`, `priced_at: datetime | None`; methods `get_events() -> list[dict]`,
  `priced_at_for(event_id: str) -> datetime | None`.
  `runner=None` means start a daemon `threading.Thread(name="hills-oan")`.

Behaviour (spec, "Budget, caching and freshness"):
- Listing: `client.get("/events", sport="soccer", start_from=int(now), start_to=int(now) + int(window*3600), limit=1000)`, plus `cursor=` while `next_cursor` is set (3 pages at most), reused for 600 s. A failed listing keeps the previous one and is named in `errors`.
- Due: a listed event naming `williamhill` with no board, or a board older than `snapshot_ttl`, and not inside a 600 s failure hold. Due events go nearest kick-off first, at most `max_per_pass` a pass.
- Snapshot: `client.get(f"/events/{id}/odds/snapshot", bookmakers="williamhill,pinnacle", price_fields="odds", types="total,team total,handicap", periods="full time")`.
- One pass at a time (`threading.Lock` plus a `_refreshing` flag). `get_events` refreshes the listing, starts a pass when anything is due, waits up to `first_wait` for it only while no pass has ever finished, then builds the events from the boards of listed events with `now < start`. Every pass prunes boards whose event is no longer listed.
- Errors: a snapshot error is `f"{id}: {exc}"` and holds the event 600 s; `BudgetSpent` or `RateLimited` ends the pass, recorded as `str(exc)`; a `complete: false` snapshot is used and named `f"{id}: snapshot incomplete"`.
- `last_sweep` = `{"source": "odds-api.net", "book": "williamhill", "fair_source": "odds-api.net:pinnacle", "window_hours", "fixtures_in_window": listed events naming williamhill, "called": of those with a board, "carried_paddy_prices", "carried_pinnacle_prices", "pending": listed William Hill events still due (no fresh board) and not held, after the pass, "stale_williamhill", "failed", "errors", "requests_today", "request_budget", "next_kickoff": ISO of the first listed WH event or None}`.
- `priced_at_for(event_id)` returns Task 2's `priced_at` for a carried event, else None. `priced_at` is the oldest over carried events.
- `feed.build_feed`: each group's `priced_at` = `client.priced_at_for(event_id)` when the client has that method, else `client.priced_at` as now.
- `feed.diagnose`: a fair side came with the sweep when `sweep.get("fair_source")` is truthy (not only `oddspapi…`). New first check after "no sweep": `pending > 0` and `carried_paddy_prices == 0` gives `f"Still fetching {name}'s prices: {pending} match(es) to come. Open the page again in a minute."`. The "check the OddsPapi plan covers" sentence and `/oddspapi-check` hint appear only when `sweep["source"] == "oddspapi"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_oddsapinet_source.py`. Use
  `make(fake=None, **kw)`, which returns `(src, fake, clock)` with a `FakeOan(routes())`,
  `runner=lambda fn: fn()`, `now=clock.now`, `monotonic=clock.mono`, `window_hours=168`.

```python
SNAP = {"bookmakers": "williamhill,pinnacle", "price_fields": "odds",
        "types": "total,team total,handicap", "periods": "full time"}

def test_the_first_sweep_lists_fetches_and_builds_the_board():
    src, fake, _ = make()
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert fake.calls == [("/events", {"sport": "soccer", "start_from": 1794400800,
                                       "start_to": 1795005600, "limit": 1000}),
                          ("/events/4229698994/odds/snapshot", SNAP),
                          ("/events/1417142635/odds/snapshot", SNAP)]
    assert [f["id"] for f in src.fixtures] == ["oan:4229698994", "oan:1417142635"]
    s = src.last_sweep
    assert {k: s[k] for k in ("source", "book", "fair_source", "fixtures_in_window", "called",
            "carried_paddy_prices", "carried_pinnacle_prices", "pending", "stale_williamhill",
            "failed", "errors", "next_kickoff")} == {
        "source": "odds-api.net", "book": "williamhill", "fair_source": "odds-api.net:pinnacle",
        "fixtures_in_window": 2, "called": 2, "carried_paddy_prices": 1,
        "carried_pinnacle_prices": 1, "pending": 0, "stale_williamhill": 0, "failed": 0,
        "errors": [], "next_kickoff": "2026-11-12T17:00:00Z"}

def test_a_board_is_reused_until_the_vendor_would_have_refreshed():
    src, fake, clock = make(); src.get_events(); src.get_events()
    assert len(fake.calls) == 3
    clock.advance(601); src.get_events()
    assert [p for p, _ in fake.calls[3:]] == ["/events"]
    clock.advance(3000); src.get_events()          # 3601 s: both boards due (ttl 3599)
    assert [p for p, _ in fake.calls[4:]] == ["/events", "/events/4229698994/odds/snapshot",
                                               "/events/1417142635/odds/snapshot"]

def test_only_games_william_hill_prices_are_fetched():
    # routes["/events"] = events.json plus a Nations League item: event_id "999", "Wales" v
    # "Iceland", start_time 1794515400, bookmakers ["pinnacle"]
    assert "oan:999" in [f["id"] for f in src.fixtures]
    assert "/events/999/odds/snapshot" not in [p for p, _ in fake.calls]

def test_a_board_for_a_game_no_longer_listed_is_dropped():
    # after the first sweep, routes["/events"] keeps only England; clock.advance(601)
    assert set(src.boards) == {"oan:1417142635"}

def test_a_game_leaves_the_board_at_kickoff():
    # first sweep at NOW; clock.advance(111600) to 2026-11-12T19:40Z, get_events (listing refetched);
    # then clock.advance(360) to 19:46Z, inside the 600 s listing cache
    assert src.get_events() == []

def test_the_pass_is_capped_and_nearest_kickoff_goes_first():
    src, fake, _ = make(max_per_pass=1)
    assert src.get_events() == []
    assert [p for p, _ in fake.calls] == ["/events", "/events/4229698994/odds/snapshot"]
    assert src.last_sweep["pending"] == 1
    assert feed.diagnose(src)["verdict"].startswith("Still fetching William Hill's prices: 1 match(es)")

def test_one_pass_at_a_time():
    started = []
    src, fake, _ = make(runner=started.append, first_wait=0)
    assert src.get_events() == [] and src.get_events() == []
    assert len(started) == 1
    started[0]()
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]

def test_a_failed_snapshot_is_named_and_held():
    # routes England snapshot -> RuntimeError("boom")
    src.get_events()
    assert (src.last_sweep["failed"], src.last_sweep["errors"]) == (1, ["1417142635: boom"])
    src.get_events()                               # held: not asked again
    clock.advance(601); src.get_events()           # asked again
    assert [p for p, _ in fake.calls].count("/events/1417142635/odds/snapshot") == 2

def test_a_spent_budget_ends_the_pass():
    # routes Armenia snapshot -> BudgetSpent("daily credit budget spent")
    src.get_events()
    assert "/events/1417142635/odds/snapshot" not in [p for p, _ in fake.calls]
    assert src.last_sweep["errors"] == ["daily credit budget spent"]
    assert src.last_sweep["pending"] == 2          # neither game has a board; neither is held

def test_an_incomplete_snapshot_is_used_and_named():
    # England snapshot with "complete": False
    assert [e["id"] for e in src.get_events()] == ["oan:1417142635"]
    assert "1417142635: snapshot incomplete" in src.last_sweep["errors"]

def test_priced_at_is_the_oldest_price_behind_the_board():
    src, _, _ = make(); src.get_events()
    eng_wh = datetime(2026, 11, 11, 11, 26, 38, tzinfo=timezone.utc)
    assert src.priced_at == eng_wh and src.priced_at_for("oan:1417142635") == eng_wh
    assert src.priced_at_for("oan:4229698994") is None
    item = feed.build_feed(src, -0.5)
    assert [(i.event_id, i.priced_at, len(i.bets)) for i in item] == [("oan:1417142635", eng_wh, 80)]

def test_the_hills_routes_serve_the_board():
    src, _, _ = make(); app.state.hills_client = src
    rows = TestClient(app).get("/hills/feed?min_edge=-0.5").json()
    assert [(r["event_id"], r["match"], r["priced_at"]) for r in rows] == [
        ("oan:1417142635", "England vs Croatia", "2026-11-11T11:26:38Z")]
    why = TestClient(app).get("/hills/diagnostics").json()
    assert why["verdict"] == "80 value row(s) found." and why["sweep"]["source"] == "odds-api.net"

def test_diagnose_takes_a_fair_side_from_any_vendor():
    stub = SimpleNamespace(book="williamhill", last_sweep={"source": "odds-api.net",
        "fair_source": "odds-api.net:pinnacle", "fixtures_in_window": 2, "called": 2,
        "carried_paddy_prices": 1, "carried_pinnacle_prices": 0, "errors": []})
    v = feed.diagnose(stub)["verdict"]
    assert "Pinnacle (pinnacle)" in v and "ODDSPAPI_FAIR_BOOK" not in v
    stub.last_sweep.update(carried_paddy_prices=0)
    assert "oddspapi-check" not in feed.diagnose(stub)["verdict"]
```

  `tests/test_hills.py` and the Paddy diagnostics tests stay as they are and must still
  pass. `app.state.hills_client` is reset to None in a teardown fixture.

- [ ] **Step 2: Run them to see them fail**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_oddsapinet_source.py -q`
Expected: FAIL, `ImportError: OddsApiNetHillsSource`.

- [ ] **Step 3: Implement** `OddsApiNetHillsSource` in `app/cards/oddsapinet.py` and the two
  `feed.py` changes. The pass function (what `runner` is handed) refreshes due events under
  the rules above and clears `_refreshing` in a `finally`.

- [ ] **Step 4: Run the tests to see them pass, then the whole suite**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_oddsapinet_source.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: 13 passed; then the full suite passes.

- [ ] **Step 5: Commit**

```bash
git add render/api/app/cards/oddsapinet.py render/api/app/cards/feed.py \
  render/api/tests/test_oddsapinet_source.py
git commit -m "Hills: the odds-api.net source (background pass, honest age, vendor-neutral diagnostics)"
```

### Task 4: BB Boost legs and settling through the source

**Files:**
- Modify: `render/api/app/cards/oddsapinet.py` (leg ids, results, three source methods)
- Modify: `render/api/app/hills/legs.py`, `render/api/app/hills/settle.py`, `render/api/app/routers/hills.py:95`
- Create: `render/frontend/src/hills.js`, `render/frontend/src/hills.test.js`
- Modify: `render/frontend/src/pages/HillsBuilder.jsx` (the `· auto` line and its header comment)
- Test: `render/api/tests/test_hills_oan.py`

**Interfaces:**
- Consumes: Task 3's source; `legs.OFFERED`, `legs._label`; `settle.bet_result`, `settle.due_fixtures`, `settle.FINISHED_AFTER`, `RECHECK_SECONDS`, `_LAST_CHECK`.
- Produces:
  - `leg_id(key: str, line: float) -> str`; `parse_leg_id(market_id: str) -> tuple[str, float] | None`
  - `results_counts(body: dict) -> dict[tuple[str, str], int] | None`: `{(metric, side): score}`
    for `period == "full time"` and `status == "final"` records. None unless
    `status == "available"` and `result.event_status == "finished"`.
  - `leg_result(counts: dict, market_id: str, outcome_id: str) -> str | None`: `"won"` or
    `"lost"`. None for an unknown key, an integer line, or a missing or non-final record. A
    match key needs both sides' records.
  - On the source: `hills_legs(fixture_id: str, home: str, away: str) -> list[dict]` (same
    dict shape and order as `legs.legs_for`; fetches the event's snapshot in the request when
    its board is missing or due; `[]` for a non-`oan:` id with no request);
    `owns_fixture(fixture_id: str) -> bool`;
    `leg_results(fixture_id: str, legs: list[tuple[str, str]]) -> dict[tuple[str, str], str]`.
  - `legs.legs_for(source, fixture_id, home, away)`: `source.hills_legs(...)` when the
    source has it, else the current body.
  - `settle.check(db: Session, source, now: datetime | None = None) -> int`: with
    `leg_results` on the source, only fixtures it `owns_fixture`, `settled_by = source.vendor`;
    otherwise the current OddsPapi path on `source._client` with `settled_by = "oddspapi"`.
    `settle.apply(db, fixture_id, results, settled_by: str = "oddspapi")`.
  - Router: `settle.check(db, _source(request))`.
  - `hills.js`: `export const isAutoSettled = settledBy => settledBy === 'oddspapi' || settledBy === 'odds-api.net'`.

Key → metric for settling: corners keys → `corners`, cards keys → `cards`, `totals_goals` →
`standard`; team keys take their side (`home`/`away`), match keys add both sides.

- [ ] **Step 1: Write the failing tests** in `tests/test_hills_oan.py` (its own in-memory DB
  fixture, copied from `test_hills_target.py`; target and bets written straight to the DB,
  with kick-offs relative to the real clock so the tests do not age):

```python
def test_leg_ids_round_trip():
    assert leg_id(CORNERS, 9.5) == "alternate_totals_corners:9.5"
    assert parse_leg_id("alternate_totals_corners:9.5") == (CORNERS, 9.5)
    assert parse_leg_id("10803") is None and parse_leg_id("bogus:x") is None

def test_results_are_read_per_side_full_time_final():
    c = results_counts(load("results_1595650036_final.json"))
    assert {k: c[k] for k in [("corners", "home"), ("corners", "away"), ("cards", "home"),
            ("cards", "away"), ("standard", "home"), ("standard", "away")]} == {
        ("corners", "home"): 3, ("corners", "away"): 3, ("cards", "home"): 2,
        ("cards", "away"): 4, ("standard", "home"): 1, ("standard", "away"): 2}
    assert results_counts(load("results_2161612418_pending.json")) is None
    assert leg_result(c, "alternate_totals_corners:9.5", "Under") == "won"
    assert leg_result(c, "alternate_totals_cards:3.5", "Over") == "won"
    assert leg_result(c, "team_cards_home:2.5", "Over") == "lost"
    assert leg_result(c, "totals_goals:2.5", "Over") == "won"
    assert leg_result(c, "team_corners_away:3.5", "Under") == "won"
    assert leg_result(c, "totals_saves:4.5", "Over") is None
    assert leg_result(c, "alternate_totals_corners:10.0", "Over") is None

def test_a_match_leg_needs_both_sides_final():
    body = load("results_1595650036_final.json")
    # set the away corners record's "status" to "live"
    c = results_counts(body)
    assert leg_result(c, "alternate_totals_corners:9.5", "Under") is None
    assert leg_result(c, "team_corners_home:2.5", "Over") == "won"

def test_legs_on_an_odds_api_net_target():
    # source swept as in Task 3; HillsTarget id 1: fixture "oan:1417142635", England v
    # Croatia, kickoff = real now + 2 days
    legs = client.get("/hills/legs").json()["legs"]
    assert len(legs) == 94
    assert legs[0] == {"market_id": "alternate_totals_corners:5.5", "outcome_id": "Over",
                       "key": CORNERS, "market": "corners", "side": "Over", "line": 5.5,
                       "label": "Over 5.5 corners"}
    labels = {l["label"] for l in legs}
    assert {"England Over 6.5 corners", "Croatia Under 0.5 cards", "Over 2.5 goals"} <= labels

def test_the_target_board_is_fetched_when_missing():
    src, fake, _ = make()                          # never swept
    assert len(src.hills_legs("oan:1417142635", "England", "Croatia")) == 94
    assert [p for p, _ in fake.calls] == ["/events/1417142635/odds/snapshot"]
    assert src.hills_legs("oan:4229698994", "Armenia", "Cyprus") == []
    assert src.hills_legs("a", "Arsenal", "Chelsea") == [] and len(fake.calls) == 2

def test_only_william_hills_lines_can_be_logged():
    ok = client.post("/hills/bets", json={"legs": [{"market_id": "alternate_totals_corners:9.5",
                                                    "outcome_id": "Over"}]})
    assert ok.status_code == 200 and ok.json()["bet"]["text"] == "Over 9.5 corners"
    bad = {"legs": [{"market_id": "alternate_totals_corners:10.0", "outcome_id": "Over"}]}
    assert client.post("/hills/bets", json=bad).status_code == 422

def test_an_odds_api_net_bet_settles_from_results():
    # HillsBet rows on "oan:1595650036", kickoff = real now - 3 h:
    #   [corners 9.5 Under]; [cards 3.5 Over, team_cards_home 2.5 Over]; [goals 2.5 Over] + note "Saka anytime"
    settle._LAST_CHECK.clear()
    got = {b["text"]: b for b in client.get("/hills/bets?fixture_id=oan:1595650036").json()["bets"]}
    assert (got["Under 9.5 corners"]["result"], got["Under 9.5 corners"]["settled_by"]) == ("won", "odds-api.net")
    two = got["Over 3.5 cards + Kawasaki Frontale Over 2.5 cards"]
    assert two["result"] == "lost" and [l["result"] for l in two["legs"]] == ["won", "lost"]
    assert got["Over 2.5 goals + Saka anytime"]["result"] == "open"
    assert fake.calls.count(("/events/1595650036/results", {})) == 1
    client.get("/hills/bets?fixture_id=oan:1595650036")
    assert fake.calls.count(("/events/1595650036/results", {})) == 1     # within 30 minutes

def test_pending_results_and_oddspapi_bets_stay_open():
    # one due bet on "oan:2161612418" (pending), one due bet on OddsPapi id "a"
    assert all(b["result"] == "open" for b in client.get("/hills/bets?fixture_id=oan:2161612418").json()["bets"])
    assert all(b["result"] == "open" for b in client.get("/hills/bets?fixture_id=a").json()["bets"])
    assert not [p for p, _ in fake.calls if "/events/a/" in p]
```

  The labels in the settling test come from the bet's stored label, so write the rows with
  labels built by `legs._label` (home "Kawasaki Frontale", away "Kashiwa Reysol").
  `tests/test_hills_target.py`'s OddsPapi settling test must pass unchanged. Its source
  carries `_client`, so the legacy path still runs.

  In `render/frontend/src/hills.test.js`:

```js
import { isAutoSettled } from './hills'
test('both vendors settle automatically; a person does not', () => {
  expect(isAutoSettled('odds-api.net')).toBe(true)
  expect(isAutoSettled('oddspapi')).toBe(true)
  expect(isAutoSettled('callum')).toBe(false)
  expect(isAutoSettled(null)).toBe(false)
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_hills_oan.py -q` and `cd render/frontend && npm test -- src/hills.test.js`
Expected: FAIL (`ImportError: leg_id`; `Failed to resolve import "./hills"`).

- [ ] **Step 3: Implement** the functions and methods above, the dispatch in `legs.py` and
  `settle.py`, the router line, `hills.js`, and in `HillsBuilder.jsx` replace
  `b.settled_by === 'oddspapi'` with `isAutoSettled(b.settled_by)`. The header comment says
  the legs settle themselves from the vendor Hills is on (odds-api.net; OddsPapi without its
  key), for bets logged on that vendor and 90-minute results only; anything else is set by hand.
  Update `settle.py`'s docstring to name both vendors.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_hills_oan.py tests/test_hills_target.py tests/test_hills.py -q` and `cd render/frontend && npm test && npm run lint`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add render/api/app/cards/oddsapinet.py render/api/app/hills/legs.py render/api/app/hills/settle.py \
  render/api/app/routers/hills.py render/api/tests/test_hills_oan.py \
  render/frontend/src/hills.js render/frontend/src/hills.test.js render/frontend/src/pages/HillsBuilder.jsx
git commit -m "Hills: BB Boost legs and settling from odds-api.net"
```

### Task 5: Choosing the vendor, `/api/ready`, the OddsPapi check

**Files:**
- Create: `render/api/app/hills/vendor.py`
- Modify: `render/api/app/main.py:131-137` (Hills' source), the readiness handler (`providers`)
- Modify: `render/api/app/cards/oddspapi.py` (`OddsPapiPaddySource.vendor = "oddspapi"`)
- Modify: `render/api/app/routers/cards.py` (`oddspapi_check`'s non-OddsPapi answer)
- Test: `render/api/tests/test_hills_vendor.py`

**Interfaces:**
- Consumes: Task 1's client and settings; Task 3's source; `OddsPapiPaddySource`, `WILLIAM_HILL`, `hills.target.EXTRA_TOURNAMENTS`.
- Produces:
  - `build_hills_source(oddsapinet_key: str, oddspapi_client) -> object | None`: the
    odds-api.net source on `OddsApiNetClient(key, daily_budget=settings.oddsapinet_daily_credit_budget)`
    when the key is set; else `OddsPapiPaddySource(oddspapi_client, book=WILLIAM_HILL, extra_tournaments=EXTRA_TOURNAMENTS)`
    when there is an OddsPapi client; else None. main.py sets
    `app.state.hills_client = build_hills_source(settings.oddsapinet_api_key, oddspapi_client)`
    after the OddsPapi block, and logs which vendor Hills is on.
  - `/api/ready` → `providers["hills_detail"] = {"provider": src.vendor or None, "degraded": bool, "requests_today": src._client.requests_today or None}`,
    where `degraded` = no source, or `last_sweep["failed"]` with no `carried_paddy_prices`.
  - `oddspapi_check` on a source that is not `OddsPapiPaddySource` but has `vendor == "odds-api.net"`
    answers `{"error": "Hills prices from odds-api.net, not OddsPapi: see /hills/diagnostics."}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_hills_vendor.py`:

```python
def test_the_odds_api_net_key_chooses_odds_api_net():
    src = build_hills_source("k-test", None)
    assert isinstance(src, OddsApiNetHillsSource)
    assert src._client.vendor == "odds-api.net" and src._client.daily_budget == 8000

def test_without_it_hills_stays_on_oddspapi():
    src = build_hills_source("", OddsPapiClient("x"))
    assert isinstance(src, OddsPapiPaddySource)
    assert (src.book, src.extra_tournaments, src.vendor) == ("williamhill", (19, 21), "oddspapi")

def test_with_neither_there_is_no_source():
    assert build_hills_source("", None) is None

def test_ready_names_the_hills_vendor():
    src, _, _ = make(); src.get_events(); app.state.hills_client = src
    assert TestClient(app).get("/api/ready").json()["providers"]["hills_detail"] == {
        "provider": "odds-api.net", "degraded": False, "requests_today": 3}
    app.state.hills_client = None
    assert TestClient(app).get("/api/ready").json()["providers"]["hills_detail"] == {
        "provider": None, "degraded": True, "requests_today": None}

def test_the_oddspapi_check_on_hills_says_where_hills_prices_from():
    src, _, _ = make(); app.state.hills_client = src
    assert TestClient(app).get("/hills/oddspapi-check").json() == {
        "error": "Hills prices from odds-api.net, not OddsPapi: see /hills/diagnostics."}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd render/api && .venv/bin/python -m pytest tests/test_hills_vendor.py -q`
Expected: FAIL, `ModuleNotFoundError: app.hills.vendor`.

- [ ] **Step 3: Implement** `hills/vendor.py`, the main.py wiring (replacing the inline
  OddsPapi Hills source), the readiness entry, the `vendor` attribute and the check's answer.

- [ ] **Step 4: Run the tests to see them pass, then everything**

Run: `cd render/api && .venv/bin/python -m pytest tests/ -q`
Expected: all pass, `test_openapi.py` included.

- [ ] **Step 5: Commit**

```bash
git add render/api/app/hills/vendor.py render/api/app/main.py render/api/app/cards/oddspapi.py \
  render/api/app/routers/cards.py render/api/tests/test_hills_vendor.py
git commit -m "Hills: ODDSAPINET_API_KEY puts Hills on odds-api.net; /api/ready names it"
```

### Task 6: Docs and the register edit

**Files:**
- Modify: `docs/decisions.md` ("Where the logic lives", the "OddsPapi is the only odds source" bullet)
- Modify: `render/api/AGENTS.md` ("Odds sources"; a new section after "Paddy (`app/cards/`) — OddsPapi")
- Modify: `docs/architecture.md` (diagram: an odds-api.net node → Hills; "Fixed" 6)
- Modify: `docs/dataflow.md` ("Where to look when something is lost")
- Modify: `CLAUDE.md` (the Hills line under "The tools")

**Interfaces:** none (docs only).

- [ ] **Step 1: Register.** Replace the bullet with the spec's proposed wording ("The
  register" section) **only after Callum has approved it in this conversation or on the pull
  request**. Until then, put the wording in the PR description as "awaiting Callum" and do not
  merge.
- [ ] **Step 2: `render/api/AGENTS.md`.** In "Odds sources": OddsPapi prices Paddy, 2-Up and
  the coupon tracker; odds-api.net prices Hills (`cards/oddsapinet.py`, `ODDSAPINET_API_KEY`).
  New section "Hills (`cards/oddsapinet.py`) — odds-api.net" covering: billing (1 credit
  per call, failures too; 250,000/month; 120/min; daily cap 8000), the per-event snapshot
  and its filters, freshness (60 s target inside 6 h, observed 1.5 min; 1 h+ further out),
  the background pass and its limits, the market mapping table, Pinnacle-only fairs and no
  fallback, `oan:` ids and leg ids, settling from `results` (no saves, cards by the vendor's
  count), "no saves on Hills" and "the 11.5 corners line shows on Hills". The register bullet
  "OddsPapi is the only odds source" is replaced, dated 11 Nov 2026, with Callum's words: "update
  the planning documents with the 2nd providor" and "Oddspapi is a subscription" — OddsPapi
  prices Paddy, 2-Up and the coupon tracker; odds-api.net prices Hills. `PLAN.md` records the
  second provider where it lists the odds sources or open work. Every line is a fact the tests pin or the spec records.
- [ ] **Step 3: `docs/architecture.md`, `docs/dataflow.md`, `CLAUDE.md`.** Architecture: a
  node `OAN["odds-api.net<br/>Hills' prices"] -- "prices (HTTPS)" --> PUB`; the OddsPapi
  node's label becomes "Paddy, 2-Up, coupons"; Fixed 6 reads "OddsPapi prices Paddy, 2-Up
  and the coupon tracker; odds-api.net prices Hills." Dataflow: the bullet "A match missing
  from Hills: `GET /api/hills/diagnostics` (`sweep.pending`, `stale_williamhill`, `errors`),
  then each match's `priced_at` on `/api/hills/feed`." CLAUDE.md: "**Hills** (`/hills`,
  `hills` grant): William Hill boosted bet builders; priced from odds-api.net
  (`ODDSAPINET_API_KEY`), OddsPapi without it."
- [ ] **Step 4: Check the names.** Run
  `grep -rn "only odds source" docs/ render/api/AGENTS.md CLAUDE.md README.md`.
  Expected: no hit that contradicts the edited register line.
- [ ] **Step 5: Commit**

```bash
git add docs/decisions.md render/api/AGENTS.md docs/architecture.md docs/dataflow.md CLAUDE.md
git commit -m "Hills on odds-api.net: register, AGENTS, architecture and dataflow"
```

### Task 7: Live verification (controller runs it; no product code)

**Files:** none committed. Scratch work goes in the scratchpad. Budget: at most 150 credits.

- [ ] **Step 1: Run the API locally on the real key.** In a shell, export
  `ODDSAPINET_API_KEY` from the vendor-keys file (never echo it), set
  `DATABASE_URL=sqlite:///<scratchpad>/live.db` and `ENABLE_CARDS=true`, run
  `cd render/api && .venv/bin/python -m app.apikeys create live-check --tools hills`, then
  `.venv/bin/uvicorn app.main:app --port 8011`. The boot log must say Hills is on
  odds-api.net.
- [ ] **Step 2: The board.** With the printed key as `Authorization: Bearer`, call
  `GET /api/hills/feed?min_edge=-0.5` twice, 60 s apart, then `GET /api/hills/diagnostics`.
  Expected: the first call answers within ~16 s. By the second, `sweep.pending == 0` and
  `fixtures_in_window` matches the Hills-league William Hill games listed for the next 7 days
  (Thursday 12 Nov's Nations League games, Saturday 14 Nov's League 1 and Nations League
  games). England v Croatia shows rows with `event_id` `oan:1417142635`. Its `priced_at`
  equals William Hill's `bookmaker_as_of_ts_ms` from one direct snapshot of it, within a
  minute. `sweep.errors` is empty or names real vendor failures.
- [ ] **Step 3: Spot-check three prices.** For England v Croatia, compare the feed's odds
  for Over 9.5 corners, Over 3.5 cards and Over 2.5 goals with a direct snapshot. Expected:
  equal.
- [ ] **Step 4: Fixtures, legs and ready.** `GET /api/hills/fixtures`: no Premier League
  games until the vendor lists the 21 Nov round, so `fixtures` may be empty (expected in the
  international break). In a Python shell on the same env, call
  `app.state.hills_client.hills_legs("oan:1417142635", "England", "Croatia")`: about 94 legs.
  `GET /api/ready`: `providers.hills_detail.provider == "odds-api.net"`, `degraded` false.
- [ ] **Step 5: Settling (after 22:00 UTC on 12 Nov).** In the shell,
  `leg_results("oan:1417142635", [("alternate_totals_corners:9.5", "Over")])` returns `won` or
  `lost`, consistent with the published corner count.
- [ ] **Step 6: Spend.** Read `/usage` once. Report the credits this check used, and
  `requests_today` from `/api/ready`. Then stop the server and delete the scratch database
  and API key.
