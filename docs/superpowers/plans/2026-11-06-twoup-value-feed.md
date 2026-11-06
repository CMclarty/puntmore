> Executed 6 Nov 2026; kept as the record of the build.

# 2-Up Value Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the 2-Up module on Render to a one-way HTTP feed of current value bets, with every row carrying when it was priced, served from a scheduled refresh that keeps the last good board on a transient failure.

**Architecture:** `render/api/app/twoup/` keeps `feed.py` and the pricing half of `model.py`; `decision.py`, `ledger.py`, `app/betfinder/`, `app/hub/` and their routes, tables, schemas, settings and tests are deleted. The feed gains `priced_at` per row (from a wall-clock fetch time the OddsPapi client records per chunk), a `served()` board refreshed by an APScheduler job, and a last-good fallback. The 2-Up tab becomes the Feed alone with "updated" and "priced" times. `hub/` is not changed except for a dated note in its docs.

**Tech Stack:** FastAPI, SQLAlchemy, APScheduler, pytest (`render/api`); React + Vite + vitest (`render/frontend`).

**Spec:** `docs/2up-engine-spec.md`, section "Decisions, 6 Nov 2026" (the register entry is `docs/decisions.md`, "The 2-Up engine and placement").

## Global Constraints

- Grants keep their names; the feed and the page stay behind `accabonus`; no new grant.
- `ENABLE_TWOUP` keeps gating the router and the job; `TWOUP_KILL_SWITCH` stays global.
- Dead tables (`twoup_*`, `tips`, `betfinder_sources`, `hub_*`) are left on the disk, not dropped (repo precedent: `tip_migration` left `hub_tips`). The ORM classes go; `Base.metadata.create_all` ignores tables it no longer knows.
- Settings: `Settings` has `extra="ignore"`, so a removed `twoup_*`/`betfinder_*`/`hub_*` variable still present in Render's `.env` is harmless.
- No new OddsPapi spend: every request still goes through the shared `OddsPapiClient` (`app/cards/oddspapi.py`) and its tiered cache.
- `hub/` code, tests and `mock_render` are untouched; only `hub/PROTOCOL.md` and `hub/README.md` get a dated note.
- Commit messages end with the two attribution lines this session uses (`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`, `Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB`). Work on branch `integration-2` in `/home/callum/Projects/puntmore-int2`.
- Test commands: API `cd render/api && /home/callum/Projects/puntmore/api/.venv/bin/python -m pytest tests/ -q`; frontend `cd render/frontend && npm run lint && npm test && npm run build`; hub `cd hub && ~/.venvs/puntmore/bin/python -m pytest -q` (must still pass, untouched).

## Review Focus

1. Kill switch on: `GET /api/2up/feed` must return no rows and say `status.killed: true`, never the board. (Task 6)
2. Every OddsPapi request failing after a good pass: the feed keeps serving the last good rows with their old `priced_at`, `status.stale: true` and the reason, never an empty board. (Task 6)
3. First request after boot, before the refresh job has run: `available: true`, no rows, `status.not_yet_refreshed: true`, not a 500 and not a fetch on the request path. (Task 6)
4. A machine key issued earlier with the removed `hub` grant: still loads, is refused with 403 on every route, never crashes `parse_permissions`. (Task 4)
5. The page receiving a row without `priced_at` or a body without `generated_at` (an older API mid-deploy): renders "—", never throws. (Task 7)

---

### Task 1: Remove staking and the decision pass

**Files:**
- Modify: `render/api/app/twoup/model.py` (`Params`, lines 45–92; delete `kelly_fraction` through `account_stake_cap`, `in_window`, lines 272–357)
- Delete: `render/api/app/twoup/decision.py`, `render/api/tests/test_twoup_decision.py`
- Modify: `render/api/app/scheduler.py:353-365` (`run_twoup`) and `:454-468` (the `twoup_decision` job)
- Modify: `render/api/app/config.py:199-224` (settings `twoup_kelly_fraction` … `twoup_decision_minutes`)
- Modify: `render/api/app/routers/twoup.py:23` (import), `:129-150` (`get_diagnostics`)
- Modify: `render/api/app/schemas.py:658-665` (`TwoUpDiagnosticsOut`)
- Modify: `render/api/tests/test_twoup_model.py` (delete `test_kelly_*`, `test_round_stake_*`, `test_stake_ceiling_*`, `test_growth_cap_*`, `test_betting_window_*`; edit `test_params_come_from_settings`)
- Modify: `render/api/tests/test_twoup_routes.py` (delete `test_the_scheduled_pass_does_nothing_outside_the_window`, `test_scheduler_adds_the_job_only_with_a_feed`; `test_ledger_kill_resume_and_diagnostics_routes` is replaced in Task 2)
- Modify: `render/api/tests/test_twoup_backtest.py` only if it imports a removed name (it imports `model` only; verify)

**Interfaces:**
- Produces: `model.Params(min_edge, min_price, max_price, min_lead_minutes)` with `from_settings()` reading only `twoup_min_edge`, `twoup_min_price`, `twoup_max_price`, `twoup_min_lead_minutes`; `passes_filters(c, params)` unchanged; `TwoUpDiagnosticsOut(feed, rescue_table, global_kill_switch, settings)`.

- [ ] **Step 1: Edit `test_params_come_from_settings` to assert exactly the four surviving fields, and delete the staking tests named above**

```python
def test_params_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "twoup_min_edge", 0.01)
    monkeypatch.setattr(settings, "twoup_min_lead_minutes", 30)
    p = Params.from_settings()
    assert (p.min_edge, p.min_price, p.max_price, p.min_lead_minutes) == (0.01, 1.25, 8.0, 30)
    assert not hasattr(p, "kelly_fraction")
```

- [ ] **Step 2: Run `pytest tests/test_twoup_model.py -q`; expected: the edited test FAILS on `hasattr`**
- [ ] **Step 3: Cut `Params` to the four fields and delete the staking functions from `model.py`; delete `decision.py`; delete `run_twoup` and the `twoup_decision` job from `scheduler.py`; delete the staking settings from `config.py`; drop the `decision, ledger` import and the `last_decision`, `in_window`, `clients` parts of `get_diagnostics` and `TwoUpDiagnosticsOut`**

`get_diagnostics` returns `TwoUpDiagnosticsOut(feed=..., rescue_table=..., global_kill_switch=settings.twoup_kill_switch, settings={the four twoup dials + twoup_tournament_ids, twoup_feed_ttl, twoup_horizon_hours})`. Keep `tools/twoup_backtest.py` working: it imports `model` only.

- [ ] **Step 4: Run `pytest tests/test_twoup_model.py tests/test_twoup_backtest.py -q`; expected: PASS. `pytest tests/ -q` will still fail in the ledger and Bet Finder tests, which Tasks 2–3 remove.**
- [ ] **Step 5: Commit: `2-Up: drop staking and the decision pass — staking lives in the client`**

### Task 2: Remove the ledger

**Files:**
- Delete: `render/api/app/twoup/ledger.py`, `render/api/tests/test_twoup_ledger.py`
- Modify: `render/api/app/models.py:433-529` (`TwoUpClient`, `TwoUpAccount`, `TwoUpBet`, `TwoUpBalance`)
- Modify: `render/api/app/schemas.py:495-555` (`TwoUpClientOut`, `TwoUpAccountOut`, `TwoUpBetOut`, `TwoUpBalanceOut`, `TwoUpLedgerOut`)
- Modify: `render/api/app/routers/twoup.py:32-38` (`_client_out`), `:71-127` (ledger, kill, resume routes)
- Modify: `render/api/app/main.py:75-80` (`ensure_clients` seeding)
- Modify: `render/api/app/config.py:173` (`twoup_clients`)
- Modify: `render/api/tests/test_twoup_routes.py` (replace `test_ledger_kill_resume_and_diagnostics_routes`; remove `_seed_owner` and the `decision, ledger` import)
- Modify: `render/api/tests/twoup_helpers.py` (`cand`: drop the `scope` parameter and `source_scope=`)

**Interfaces:**
- Produces: `/api/2up/*` is `GET /feed` and `GET /diagnostics` only.

- [ ] **Step 1: Replace the ledger test with**

```python
def test_diagnostics_route_reports_the_feed_and_the_dials():
    feed, _ = _feed()
    feed.candidates(NOW)
    app.state.twoup_feed = feed
    api = TestClient(app)
    diag = api.get("/2up/diagnostics").json()
    assert diag["rescue_table"]["loaded"] is True and diag["rescue_table"]["buckets"]
    assert diag["global_kill_switch"] is False
    assert diag["settings"]["twoup_min_edge"] == settings.twoup_min_edge
    assert "clients" not in diag and "last_decision" not in diag
    assert api.get("/2up/ledger/owner").status_code == 404
    assert api.post("/2up/clients/owner/kill").status_code in (404, 405)
```

- [ ] **Step 2: Run `pytest tests/test_twoup_routes.py -q`; expected: FAIL (the ledger route still answers 200 or the import of `ledger` still exists)**
- [ ] **Step 3: Delete the files, ORM classes, schemas, routes, seeding and setting listed above; `Candidate.source_scope` and `SourceScope` go from `model.py` with the `source` field kept**
- [ ] **Step 4: Run `pytest tests/test_twoup_routes.py tests/test_twoup_model.py -q`; expected: PASS**
- [ ] **Step 5: Commit: `2-Up: remove the ledger — no account on Render`**

### Task 3: Remove Bet Finder from Render

**Files:**
- Delete: `render/api/app/betfinder/` (whole package), `render/api/app/routers/betfinder.py`, `render/api/app/tip_migration.py`, `render/api/tests/test_betfinder.py`, `render/api/tests/test_tips_one_table.py`
- Modify: `render/api/app/models.py:530-600` (`BetFinderSource`, `Tip`)
- Modify: `render/api/app/schemas.py:557-657` (every `BetFinder*` and `Tip*` schema)
- Modify: `render/api/app/config.py:226-259` (`enable_betfinder`, `betfinder_*`)
- Modify: `render/api/app/scheduler.py:366-378` (`run_betfinder`), `:470-481` (the `betfinder` job)
- Modify: `render/api/app/main.py:49`, `:65-73` (tips migration), `:81-85` (source seeding), `:356-357` (router)

**Interfaces:**
- Produces: nothing; the app imports no `betfinder` name.

- [ ] **Step 1: Write `tests/test_removed_modules.py`**

```python
import importlib
import pytest

@pytest.mark.parametrize("name", ["app.betfinder", "app.tip_migration", "app.routers.betfinder",
                                  "app.twoup.decision", "app.twoup.ledger"])
def test_removed_modules_do_not_import(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(name)

def test_no_betfinder_or_ledger_routes():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert not any(p.startswith("/2up/betfinder") or p.startswith("/2up/ledger")
                   or p.startswith("/2up/clients") for p in paths)
```

- [ ] **Step 2: Run `pytest tests/test_removed_modules.py -q`; expected: FAIL (`app.betfinder` imports)**
- [ ] **Step 3: Delete and edit the files listed above**
- [ ] **Step 4: Run `pytest tests/ -q --ignore=tests/test_hub_ws.py`; expected: PASS**
- [ ] **Step 5: Commit: `2-Up: Bet Finder is not a Render instance`**

### Task 4: Remove the Hub endpoint from Render

**Files:**
- Delete: `render/api/app/hub/` (whole package, including `protocol.schema.json`), `render/api/app/routers/hub_ws.py`, `render/api/tests/test_hub_ws.py`
- Modify: `render/api/app/models.py:362-432` (`HubInboxSeen`, `HubEscalation`, `HubOutbox`, `HubSentBet`)
- Modify: `render/api/app/schemas.py:414-494` (every `Hub*` schema)
- Modify: `render/api/app/security.py:34` (`MACHINE_GRANTS = ("value", "state", "ingest")`)
- Modify: `render/api/app/apikeys.py` (any `hub` alias or help text)
- Modify: `render/api/app/config.py` (`hub_*` settings: grep `hub_` in the file)
- Modify: `render/api/app/main.py:47`, `:363-365`; `render/api/app/scheduler.py` (any `hub` sweep job: grep `hub` in the file)
- Modify: `render/api/tests/test_removed_modules.py` (add `app.hub`, `app.routers.hub_ws`)
- Test: `render/api/tests/test_api_clients.py` (add the two tests below)

**Interfaces:**
- Produces: `MACHINE_GRANTS == ("value", "state", "ingest")`; no `/2up/ws`, no `/2up/hub/*`.

- [ ] **Step 1: Add to the module-removal test the names `app.hub` and `app.routers.hub_ws`, and add to the security tests**

```python
def test_hub_is_no_longer_a_grant_the_cli_accepts():
    from app import apikeys
    with pytest.raises(ValueError, match="unknown tool 'hub'"):
        apikeys.parse_tools("hub")

def test_a_key_issued_with_the_dead_hub_grant_still_loads_and_is_refused():
    from app.security import has_permission, parse_permissions
    assert parse_permissions("hub") == {"hub"}
    assert has_permission("hub", "accabonus") is False
```

- [ ] **Step 2: Run both test files; expected: FAIL (`hub` is still accepted)**
- [ ] **Step 3: Delete and edit the files listed above. Grep `render/api/app` for `hub` afterwards: the only remaining hits may be comments naming the Hub box or the Paddy ingest routes.**
- [ ] **Step 4: Run `pytest tests/ -q`; expected: PASS. Run the hub suite (`cd hub && ~/.venvs/puntmore/bin/python -m pytest -q`); expected: PASS, unchanged.**
- [ ] **Step 5: Commit: `2-Up: no Hub endpoint — a Hub endpoint is per tool`**

### Task 5: Every row carries `priced_at`

**Files:**
- Modify: `render/api/app/cards/oddspapi.py:683-720` (`odds_by_tournaments`, `_odds_by_tournaments`)
- Modify: `render/api/app/twoup/model.py` (`Candidate`, `price_fixture`)
- Modify: `render/api/app/twoup/feed.py:150-200` (the chunk loop and `price_fixture` call)
- Modify: `render/api/app/schemas.py` (`TwoUpCandidateOut.priced_at`)
- Modify: `render/api/app/routers/twoup.py:44-69` (`get_feed`)
- Modify: `render/api/tests/twoup_helpers.py` (`FakeOddsPapi.fetched_at`)
- Test: `render/api/tests/test_oddspapi.py`, `render/api/tests/test_twoup_routes.py`

**Interfaces:**
- Produces: `OddsPapiClient.fetched_at(ids: list[int], bookmaker: str) -> datetime | None` (aware UTC; the wall-clock time the chunk was last fetched from OddsPapi, `None` if never); `Candidate.priced_at: datetime | None = None`; `price_fixture(..., priced_at: datetime | None = None)` sets it on both sides; `TwoUpCandidateOut.priced_at: Optional[datetime]`.

- [ ] **Step 1: Write the client test (same fakes the existing `OddsPapiClient` tests use)**

```python
def test_fetched_at_is_the_wall_clock_time_of_the_real_request(monkeypatch):
    client = _Fake()                       # tests/test_oddspapi.py:151, the subclass that fakes `_get`
    assert client.fetched_at([17], "bet365") is None
    client.odds_by_tournaments([17], bookmaker="bet365")
    first = client.fetched_at([17], "bet365")
    assert first is not None and first.tzinfo is not None and len(client.calls) == 1
    client.odds_by_tournaments([17], bookmaker="bet365")       # a cache hit: no new time
    assert client.fetched_at([17], "bet365") == first and len(client.calls) == 1
```

(`_Fake` overrides `get`, so a real request is one entry in `_Fake.calls`. The test lives in `tests/test_oddspapi.py`.)

and the feed test

```python
def test_rows_carry_the_time_their_prices_were_fetched():
    feed, client = _feed()
    rows = feed.candidates(NOW)
    assert rows and all(c.priced_at == client.fetched_at([17, 18], "bet365") for c in rows)
    body = TestClient(app).get("/2up/feed").json()   # after app.state.twoup_feed = feed
    assert body["candidates"][0]["priced_at"].startswith(NOW.strftime("%Y-%m-%d"))
```

- [ ] **Step 2: Run them; expected: FAIL (`fetched_at` missing)**
- [ ] **Step 3: Implement. In `_odds_by_tournaments`, when `_cached` performs a real `get` (a cache miss), record `self._fetched_at[key] = datetime.now(timezone.utc)` keyed exactly as the odds cache is; `fetched_at` looks the key up. In the feed, per chunk take the earlier of the two books' `fetched_at` (fall back to `now` if the client has no `fetched_at`, e.g. an old fake) and pass it to `price_fixture`. `FakeOddsPapi.odds_by_tournaments` records `self.fetched[(tuple(ids), bookmaker)] = NOW` and `fetched_at` reads it.**
- [ ] **Step 4: Run `pytest tests/ -q`; expected: PASS**
- [ ] **Step 5: Commit: `2-Up: every row says when its prices were fetched`**

### Task 6: A served board: scheduled refresh, last good on failure, kill switch

**Files:**
- Modify: `render/api/app/twoup/feed.py` (`TwoUpFeed.__init__`, `candidates`, `cached`, `status`, new `served`)
- Modify: `render/api/app/scheduler.py` (new job `twoup_refresh`)
- Modify: `render/api/app/routers/twoup.py:44-69` (`get_feed` reads `served`)
- Modify: `render/api/app/twoup/feed.py:227-245` (`value_rows` reads `served`)
- Modify: `render/api/app/main.py:520-526` (`/ready` block unchanged in shape; `status()` gains keys)
- Test: `render/api/tests/test_twoup_routes.py`

**Interfaces:**
- Produces: `TwoUpFeed.refresh(now: datetime | None = None) -> list[Candidate]` (always fetches; what the job calls); `TwoUpFeed.served() -> tuple[datetime | None, list[Candidate]]` (the board being served and when it was generated; `(None, [])` before the first refresh; never fetches); `status()` gains `stale: bool`, `served_at: str | None`, `not_yet_refreshed: bool`; `scheduler.refresh_twoup(feed) -> None`; job id `"twoup_refresh"`, `trigger="interval"`, `seconds=settings.twoup_feed_ttl`, `next_run_time=datetime.now()`, registered only when `settings.enable_twoup and twoup_feed is not None`. `candidates(now)` keeps its lazy behaviour for tests and `value()`, but the route and `value_rows` use `served()`.

- [ ] **Step 1: Replace `test_every_request_failing_is_degraded_not_stale` and add the route tests**

```python
def test_every_request_failing_keeps_the_last_good_board_and_says_stale():
    feed, client = _feed()
    good = feed.refresh(NOW)
    assert good

    def boom(ids, bookmaker="x"):
        raise RuntimeError("HTTP 500")
    client.odds_by_tournaments = boom
    feed.refresh(NOW + timedelta(minutes=5))
    at, rows = feed.served()
    assert rows == good and at == NOW
    st = feed.status()
    assert st["stale"] is True and st["degraded"] is True
    assert st["cannot_price"] == ["ALL — every OddsPapi odds request failed"]

def test_every_request_failing_with_no_good_board_serves_nothing():
    feed, client = _feed()
    client.odds_by_tournaments = lambda ids, bookmaker="x": (_ for _ in ()).throw(RuntimeError("HTTP 500"))
    feed.refresh(NOW)
    assert feed.served() == (None, []) and feed.status()["stale"] is False

def test_feed_route_before_the_first_refresh_is_empty_and_says_so():
    feed, client = _feed()
    app.state.twoup_feed = feed
    body = TestClient(app).get("/2up/feed").json()
    assert body["available"] is True and body["candidates"] == []
    assert body["status"]["not_yet_refreshed"] is True and client.calls == []

def test_feed_route_serves_the_board_without_fetching():
    feed, client = _feed()
    feed.refresh(NOW)
    app.state.twoup_feed = feed
    n = len(client.calls)
    body = TestClient(app).get("/2up/feed?value_only=true").json()
    assert body["generated_at"].startswith(NOW.strftime("%Y-%m-%dT%H:%M")) and len(client.calls) == n
    assert [c["selection"] for c in body["candidates"]] == ["Arsenal"]

def test_kill_switch_empties_the_feed(monkeypatch):
    feed, _ = _feed()
    feed.refresh(NOW)
    app.state.twoup_feed = feed
    monkeypatch.setattr(settings, "twoup_kill_switch", True)
    body = TestClient(app).get("/2up/feed").json()
    assert body["candidates"] == [] and body["status"]["killed"] is True

def test_scheduler_adds_the_refresh_job_only_with_a_feed():
    from app.scheduler import start_scheduler
    without = start_scheduler(odds_provider=None)
    try:
        assert without.get_job("twoup_refresh") is None
    finally:
        without.shutdown(wait=False)
    feed, _ = _feed()
    with_feed = start_scheduler(odds_provider=None, twoup_feed=feed)
    try:
        assert with_feed.get_job("twoup_refresh") is not None
    finally:
        with_feed.shutdown(wait=False)
```

Edit `test_feed_route_lists_priced_sides`, `test_value_feed_carries_2up_rows`, `test_ready_reports_the_2up_feed` and Task 5's `test_rows_carry_the_time_their_prices_were_fetched` to call `feed.refresh(NOW)` instead of `feed.candidates(NOW)` where they prime the feed.

- [ ] **Step 2: Run `pytest tests/test_twoup_routes.py -q`; expected: FAIL (`refresh`/`served` missing)**
- [ ] **Step 3: Implement `refresh`, `served`, the status keys, the job and the route. The last-good rule: a pass whose `lp["errors"]` is non-empty and `lp["priced_fixtures"] == 0` leaves the served board as it was and sets `stale`; any other pass replaces it (an honest empty board, e.g. no bet365 prices, is served as empty and `degraded`). `generated_at` in the response is the served board's time, or `now` when there is none.**
- [ ] **Step 4: Run `pytest tests/ -q`; expected: PASS**
- [ ] **Step 5: Commit: `2-Up: a served board — scheduled refresh, last good on failure`**

### Task 7: The 2-Up tab is the Feed alone, with times

**Files:**
- Modify: `render/frontend/src/pages/TwoUpPage.jsx` (delete `VIEWS`, the view state and `Ledger`, lines 18–46 and 106–end; edit `Feed`)
- Modify: `render/frontend/src/pages/TwoUpPage.module.css` (delete the ledger-only classes: `kill`, `killOn` and any class only `Ledger` used)
- Modify: `render/frontend/src/api.js:266-268` (delete `getTwoUpLedger`, `killTwoUpClient`, `resumeTwoUpClient`)
- Modify: `render/frontend/src/twoup.js` (delete `betLabel`, `betNote`, `signedGbp`, and `gbp` if nothing else imports it; add `timeLabel`)
- Modify: `render/frontend/src/twoup.test.js`
- Modify: `render/frontend/AGENTS.md` if it describes the Ledger view (grep `Ledger`)

**Interfaces:**
- Consumes: `GET /2up/feed?value_only=true` → `{generated_at, available, status: {stale, served_at, not_yet_refreshed, killed, cannot_price}, candidates: [{..., priced_at}]}`.
- Produces: `timeLabel(iso: string | null | undefined) -> string` giving `HH:MM` in the browser's zone, `'—'` for a missing or unparseable value.

- [ ] **Step 1: In `twoup.test.js`, delete the `betLabel`/`betNote`/`signedGbp` tests and add**

```js
import { timeLabel } from './twoup'
test('timeLabel is HH:MM or a dash', () => {
  expect(timeLabel('2026-11-06T14:05:00+00:00')).toMatch(/^\d{2}:\d{2}$/)
  expect(timeLabel(null)).toBe('—')
  expect(timeLabel(undefined)).toBe('—')
  expect(timeLabel('nope')).toBe('—')
})
```

- [ ] **Step 2: Run `npm test`; expected: FAIL (`timeLabel` is not exported)**
- [ ] **Step 3: Implement. The page: header `2-Up` with `updated {timeLabel(feed.generated_at)}` beside the refresh button, and a line in the error style when `status.stale` ("Prices from {timeLabel(status.served_at)} · OddsPapi not answering"), when `status.killed` ("Feed stopped by the kill switch") and when `status.not_yet_refreshed` ("First refresh pending"). Each card's third row gains `· priced {timeLabel(c.priced_at)}` and loses the `source_scope` tip suffix. Keep `useAutoRefresh`, `edgeTier`, `edgePct`, `kickoffLabel`.**
- [ ] **Step 4: Run `npm run lint && npm test && npm run build`; expected: all PASS, no unused-import warnings**
- [ ] **Step 5: Commit: `2-Up tab: the Feed alone, with updated and priced times`**

### Task 8: Docs follow the code

**Files:**
- Modify: `render/api/AGENTS.md:106-250` (the 365 section: rewrite "The 2-Up engine" for feed-only; delete "Bet Finder" and "Hub endpoint" sections at `:163-249` and `:732-end-of-section`; the API clients section loses the `hub` grant)
- Modify: `render/api/app/twoup/__init__.py` (docstring: feed and model only; no Hub)
- Modify: `docs/api.md` (the `/api/2up/*` entries: `feed` with `priced_at`, `generated_at`, `status` keys; `diagnostics`; delete ledger, clients, betfinder, hub, ws)
- Modify: `docs/architecture.md` (diagram: delete `BFO` and `HEP`; `T365` "2-Up value feed · live"; `LINK` note "no Render end since 6 Nov 2026 — the betting agent brings its own"; the components table and "Fixed by the spec" items 1, 3, 5 and the open decisions accordingly; the Render ↔ Hub contract table becomes a one-line pointer to the betting agent's spec)
- Modify: `docs/dataflow.md` (stages 6–11 collapse: 6 "Render stores the tip" gone; 7 is "The 2-Up feed prices fixtures → rows with `priced_at`, served on `/api/2up/feed`"; 8–11 are the client's, one line saying so; 12 updated; rule 2 loses `bet_id`; "Where to look" loses the tip and bet lines)
- Modify: `README.md:20-22` (Render runs the 365 tool's value feed; no Bet Finder orchestrator, no Hub endpoint; the Hub's link has no Render end until the betting agent)
- Modify: `PLAN.md:491-553` (item 11: a dated checked line for this work; the Bet Finder line marked removed 6 Nov 2026)
- Modify: `.env.example:85-89` (delete `HUB_*`), `:113-114` (`TWOUP_CLIENTS`), `:125-137` (staking dials), `:143-` (`ENABLE_BETFINDER`, `BETFINDER_*`)
- Modify: `hub/PROTOCOL.md` and `hub/README.md` (one dated paragraph at the top: "6 Nov 2026: Render's end of this protocol (`/api/2up/ws`) is removed; the 2-Up module is a one-way feed; the betting agent tool defines its own Hub endpoint. The Hub code below is unchanged and has no Render end until then.")
- Modify: `hub/AGENTS.md:3`, `scraper/AGENTS.md:31-45` (the words "Bet Finder" where they describe Render's orchestrator: replace with "the client's tip finding"; the Scraper's `generic` targets and the Hub inbox are not changed)

- [ ] **Step 1: Make the edits above. Then `grep -rn "betfinder\|Bet Finder\|2up/ws\|twoup_bets\|bet_list\|source_scope" docs README.md PLAN.md render/api/AGENTS.md render/frontend/AGENTS.md .env.example` and confirm every remaining hit is history (a dated "removed"/"superseded" line) or the betting agent's future.**
- [ ] **Step 2: Run `pytest tests/ -q` once more; expected: PASS**
- [ ] **Step 3: Commit: `docs: the 2-Up module is a one-way value feed`**

### Task 9: Verification

- [ ] **Step 1: `cd render/api && /home/callum/Projects/puntmore/api/.venv/bin/python -m pytest tests/ -q`; expected: all PASS, 0 skipped for a removed import**
- [ ] **Step 2: `cd render/frontend && npm run lint && npm test && npm run build`; expected: PASS**
- [ ] **Step 3: `cd hub && ~/.venvs/puntmore/bin/python -m pytest -q`; expected: PASS (untouched)**
- [ ] **Step 4: `grep -rn "twoup.ledger\|twoup.decision\|betfinder\|app.hub\|hub_ws\|TWOUP_CLIENTS\|getTwoUpLedger" render/ --include=*.py --include=*.js --include=*.jsx`; expected: no hits**
- [ ] **Step 5: `cd render/api && .venv/bin/python -c "from app.main import app; print(sorted(r.path for r in app.routes if r.path.startswith('/2up')))"`; expected: `['/2up/diagnostics', '/2up/feed']`**
- [ ] **Step 6: Push `integration-2` and note in PR #20's body that the 2-Up module is now feed-only per the 6 Nov decisions**
