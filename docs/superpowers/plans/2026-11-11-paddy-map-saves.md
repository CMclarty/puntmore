# The Paddy map and Paddy's goalkeeper saves: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One kept-open Paddy Power page goes round the project's leagues on a schedule. It reads each match's per-goalkeeper "N or more saves" prices, builds a map of Paddy's site as it goes, and the prices reach Render's store through the Hub.

**Architecture:**
- **Limits.** Paddy's limits are Callum's new ones, set in code (`sites.py`, `schedule.Pacer`, `guard.py`).
- **Map.** `scraper/ppmap.py` keeps the map at `<data>/maps/paddypower.json`, fed from every Paddy payload and every Paddy page's links.
- **Rotation.** A target with a `rotate` block is driven by `scraper/rotate.py`: a pure state machine that says the next move (open, competition, match, back). The runner makes each move through the guard and the pacer.
- **Reading and delivery.** `scraper/parsers/pp_saves.py` reads the match just opened. Each match's rows go out as one `scraper_prices` file to the new route `/api/paddy/saves-prices`.
- **Render.** Render stores the rows per match and shows them on a read route. The saves card joins a per-goalkeeper fair to Paddy's price on match, team and line.

**Tech Stack:** Python 3 (Scraper: Playwright runner, pytest; Hub: JSON Schema 2020-12, pytest; Render: FastAPI, Pydantic, pytest).

**Spec:** `docs/superpowers/specs/2026-11-11-scraper-jobs-paddy-saves-design.md`, Part M, together with the controller's addition of 11 Nov 2026 (Task 1). This plan runs after `2026-11-11-scraper-jobs.md` on the same branch and uses its `days`, `until`, `schedule.inactive`, `Runner._say`, `joblog` and `targets` CLI.

## Spec corrections

1. **Paddy's limits change (controller, 11 Nov 2026).** This supersedes the spec's "The limit is Callum's and is not changed here", the constraint "nothing edits `scraper/sites.py`'s limits", and the arithmetic of "about 7 matches an hour". Callum's words were "1 click per 2 seconds maximum, inactive between midnight and 8am", plus four fixed half-hour breaks. Task 1 sets them; the register line is the controller's to write.
   - A 50-match pass is about 100 moves, so 2 s apart it takes at least about 3.5 minutes.
   - In practice each move also includes its own click pauses and the wait for the page's feed, so a pass takes a few minutes more.
2. **"At most one move per tick."** The tick comes every ~15 s, so one move per tick would make a pass about 25 minutes, longer than its 15. The rotation therefore makes moves one at a time within a 45 s slice of each tick (Planner's call 3). Each move is still one state-machine step through the guard and the pacer. Passes never overlap.
3. **"The Hub's posting code is unchanged."** The posting code is unchanged, but the Hub's copy of `PRICE_ROUTES` (`hub/puntmore_hub/inbox.py`) must gain the route: `hub/tests/test_inbox.py` pins it to the schema's enum.
4. **"Visible through the ingest's read route."** No read route exists today. This plan adds `GET /api/paddy/saves-prices` on the same `ingest` grant.
5. **"The OddsPapi lookup for saves (`totals_saves`) … removed."** `totals_saves` (`cards/oddspapi.SAVES`) is also read by Hills' OddsPapi legs (`hills/legs.py`) and by `/paddy/oddspapi-check`. So only the saves card's use of it and the comment that claims it go. The catalogue classification stays.
6. **From the captures (they win):**
   - **Events that are not matches.** The `events` attachments carry non-matches beside the matches: the outright "English Premier League" (`pp-epl-most_list/00004`), and "Football Boosts", "Manager Specials", "Transfer Specials" and "UEFA Nations League A." (`pp-map-recon/00030`, `00033`, `00040`, `00041`). The map keeps only names with `" v "`.
   - **Saves lines.** An event page carries 8 full-match saves markets, 3+ to 10+ (lines 2.5–9.5). Each market has both goalkeepers as its two runners. The page's own layout (`pub/v7`, `00014`) names 1+ to 12+ and the `…_IN_THE_FIRST_HALF` types, but with no prices, so they never reach `pp_saves`.
   - **Specials.** The specials are runners of one market typed `#WHATODDSPADDY_-_INPLAY` ("Arsenal v Hull Stats Specials").
   - **EXTERNAL links.** The Premier League's and Champions League's EXTERNAL link URLs are `https://www.paddypower.com/english-premier-league` and `…/uefa-champions-league`, with no `/football/`. Their `linkIdentifier` has no `competitionId`, only a title. `00017`'s competitions omit both leagues; `00022` has them (10932509, 228).
   - **Link titles.** A NAV_NODE link's title can differ from the competition's name: competition 141 is "US MLS", and its link is "US Major League Football".
   - **Match links.** A match link's href ends `-<eventId>`, for example `…/english-premier-league/arsenal-v-hull-36235029`.
   - **Kickoff.** `openDate` reads `"2026-11-07T15:00:00.000Z"`.

## Planner's calls

1. **Paddy's spacing.** `Site` gains `flat_gap: bool = False` and `breaks: tuple[str, ...] = ()`.
   - For a flat-gap site, `Pacer.spaced` sets the next move at exactly `load_gap_s` (no ×1.0–1.8 spread). `Pacer.may_load` caps the any-site gap at `load_gap_s` for that site's own moves.
   - A Paddy move still sets the 20–45 s any-site gap for the other sites, so their spacing is unchanged. Only Paddy is `flat_gap`.
2. **Quiet hours and breaks.** `sites.QUIET_HOURS = (0, 8)` becomes `Guard`'s default. The breaks are `Site.breaks`, handed to `Guard(breaks=…)` by `__main__._guard`. A `Guard` built without `breaks` (most tests) has none. `Runner._paused` reports `"break"`, so a break closes Paddy's pages exactly as quiet hours do.
3. **Moves within a tick.** `Runner._tick_rotate` asks for and makes moves one at a time until the pass is over, the guard says no, or `ROTATE_SLICE_S = 45` s of wall time have gone. While only the pacer says wait (at most 2 s for Paddy), it waits on the page (`page.wait_for_timeout`).
4. **The state machine is pure.** `rotate.Rotation` (`next_move` / `moved`) touches no page. The runner executes each `Move`. The machine is tested directly; the runner is tested against a fake Paddy page (`scraper/tests/fake_paddy.py`), built on `test_pacing`'s `_Nothing` and `test_capture`'s `_Resp`.
5. **Only the match just opened.** `pp_saves.read(capture, event_id, since)` takes the newest `event-page` payload that arrived after the click (`since`) and names that `eventId`.
6. **Rotation tabs and parsing.** The generic per-tab `_parse` loop skips rotation tabs. The rotation sends each match's rows once, through `Runner._handle_rows`, which is split out of `_parse` and shared with it.
7. **Finding links.**
   - A match's link is the `a[href]` whose path ends `-<eventId>`.
   - A competition's link is tried in order: the list-page URL the map learnt by being there; Paddy's EXTERNAL link URL for that competition's exact name; the link text Paddy's football page gives it (`layout.links` title); its name.
8. **What feeds the map.** It is fed from every Paddy tab each tick, with the payloads since the last feed and the DOM links whenever the page's URL has changed. It is saved only when it changed.
9. **A `rotate` target** must be on site `paddypower`, use parser `pp_saves` and mode `keep`, and cannot also have `matches`.
10. **Pass timing.** Passes start `every_min` after the previous pass started, with no jitter added. A pass that overruns is followed by the next as soon as it ends. A failed open of the football page ends the pass.
11. **Render's ceiling for Paddy's saves price** is `lt=1001`. The capture has 126.0 for Arsenal's 8+, 9+ and 10+ saves; Paddy's other models use `lt=100`.
12. **Per-goalkeeper value rows.** They use `market_key` `"saves_home"` / `"saves_away"` and side `"Over"`. They are Paddy's only: `price_events` adds saves rows only for Paddy Power, as it already does for "most".
13. **The schema's saves row shape** applies only to the route `/api/paddy/saves-prices` (`if`/`then`). The other routes' rows still pass through unchanged.

## Global Constraints

- Work only in `/home/callum/Projects/puntmore-jobs`, branch `scraper-jobs`. Never switch branches, never push, never use `git stash`, never touch another checkout.
- Nothing edits `docs/decisions.md`, the live `/opt/puntmore-scraper/**` (read-only), the live `targets.toml` or `bb-saves`. `scraper/sites.py`'s limits change in Task 1 only, to exactly the values given there.
- No test or script loads a Paddy Power or Bookie Bashing page. Browser behaviour is tested against the stand-ins in `scraper/tests/test_pacing.py` and the new `scraper/tests/fake_paddy.py`.
- Every rotation move goes through `guard.can_load` and the pacer first, and is recorded as a load when the page's URL or document changed (the rule in `_open_market_tabs`). No randomised timing, input simulation, fingerprint or user-agent work is added anywhere. Moves use the existing `browser.click` and `browser.wait_for_feed`.
- **Fixtures** are trimmed copies of the capture bodies named in each step, committed under `scraper/tests/fixtures/`.
  - Never commit a request URL from any `index.jsonl` (they carry Paddy's `_ak` app key). Tests use plain feed URLs such as `https://apisms.paddypower.com/smspp/event-page/v5`.
  - Never commit anything else from `/opt`.
- Row shape, exactly: `{event_id: str, home: str, away: str, kickoff: str (Paddy's openDate), team: "home" | "away", line: float (N - 0.5), over: float (decimal, 4 dp)}`.
- Route, exactly: `/api/paddy/saves-prices`. Render keeps the rows 36 h, replaced per match on each post.
- Frontend: nothing is added (no Console page for the map).
- TDD: write the failing test, run it to see it fail, write the code, run it to see it pass, commit. Small commits. Docs change in the same task as the code they describe.
- **Hub tests:** `cd /home/callum/Projects/puntmore-jobs/hub && ~/.venvs/puntmore/bin/python -m pytest -q`.
- **Scraper tests**, from the repo root: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`.
  - 10 failures are known, from the missing headless browser: 8 in `test_e2e_fakesite.py` and 2 errors in `test_bb_saves.py`. "Green" means no failure outside those 10.
  - Baselines are whatever Plan J left.
- **Render tests:** `cd /home/callum/Projects/puntmore-jobs/render/api && .venv/bin/python -m pytest tests/ -q`. Baseline at `707d0aa`: **1054 passed, 1 skipped**.
  - This worktree has no `render/api/.venv`. Create it once with the two commands in `CLAUDE.md` ("Fresh clone"): `uv venv render/api/.venv`, then `uv pip install -r render/api/requirements.txt -r render/api/requirements-dev.txt --python render/api/.venv/bin/python3`.
- Before the first commit, if `git config core.hooksPath` does not print `.githooks`, run `git config core.hooksPath .githooks`.
- Every commit message ends with exactly:
  ```
  Co-Authored-By: Claude <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
  ```

## Review Focus

1. **The guard runs out mid-pass** (the hourly or daily budget is spent, or a break or quiet hours begins). The rotation must stop where it is without moving and carry on from there when allowed. A break or quiet hours closes the page; then the pass starts again from the football page. Tests in Tasks 1 and 4.
2. **A price far from evens.** Paddy quotes 126.0 for a keeper's 9+ saves. Render must store it, not refuse it with a 422 that drops the whole file. Test in Task 5.
3. **Two event pages in the ring.** The parser must read the match just opened, not an older event page still in the capture ring. Test in Task 2.
4. **A runner name that is neither side's** ("Gunners Goalkeeper") must be dropped and logged, never guessed. Test in Task 2.
5. **A post with fewer lines than before** must leave only the new lines for that match, not a mix of old and new. Test in Task 5.

---

### Task 1: Paddy's limits

**Files:**
- Modify: `scraper/sites.py` (`QUIET_HOURS`; `Site.flat_gap`, `Site.breaks`; Paddy's values; docstring)
- Modify: `scraper/schedule.py` (`Pacer.may_load`, `Pacer.spaced`, `Pacer.last_any_load`)
- Modify: `scraper/guard.py` (default `quiet=QUIET_HOURS`; `breaks` parameter; `in_break`; `can_load` says `"break"`)
- Modify: `scraper/__main__.py` (`_guard` passes the breaks)
- Modify: `scraper/runner.py` (`_paused` returns `"break"`)
- Modify: `scraper/AGENTS.md` (lines quoting "quiet hours 01-07" and the per-site load gaps), `scraper/README.md` (budgets: Paddy 1800/h, 27000/day; no Paddy traffic 00:00–08:00 or in the four breaks; Paddy's moves at least 2 s apart, flat), `hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md` ("Paddy 15/h" in Hard rules)
- Test: `scraper/tests/test_guard_config.py`, `scraper/tests/test_pacing.py`, `scraper/tests/test_schedule.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `sites.QUIET_HOURS = (0, 8)`.
  - `Site.flat_gap: bool = False` and `Site.breaks: tuple[str, ...] = ()` ("HH:MM-HH:MM", local, end exclusive).
  - `SITES["paddypower"]` is `Site(per_hour=1800, per_day=27000, quiet_hours=True, needs_login=False, block_means_cooloff=True, load_gap_s=2.0, flat_gap=True, market_list=_PP_MARKET_LIST, breaks=("12:30-13:00", "15:00-15:30", "17:30-18:00", "20:00-20:30"))`. `bookiebashing` and `generic` are unchanged.
  - `Guard(path, limits, quiet=QUIET_HOURS, cooloff_hours=24.0, breaks: dict[str, tuple[str, ...]] | None = None)`.
  - `Guard.in_break(site, now=None) -> bool`. It reuses `schedule.in_window` for each break.
  - `Guard.can_load` returns `(False, "break")` after the quiet-hours check, whatever `quiet_applies` is (only Paddy has breaks).
  - `Pacer.last_any_load: float`.
  - `Pacer.may_load(site, now)`: for a `flat_gap` site, the any-site gap is `min(next_any_load, last_any_load + load_gap_s)`.
  - `Pacer.spaced(site, now)`: a `flat_gap` site's next is `now + load_gap_s`.
  - `CLICK_EVERY_S`, `open_gap_min`, the block rule and the other sites are unchanged.

- [ ] **Step 1: Write the failing tests.** In `scraper/tests/test_guard_config.py`:

```python
from scraper import schedule
from scraper.sites import QUIET_HOURS, SITES


def test_paddys_limits_are_code():
    pp, bb = SITES["paddypower"], SITES["bookiebashing"]
    assert (pp.per_hour, pp.per_day, pp.load_gap_s, pp.flat_gap) == (1800, 27000, 2.0, True)
    assert pp.breaks == ("12:30-13:00", "15:00-15:30", "17:30-18:00", "20:00-20:30")
    assert QUIET_HOURS == (0, 8)
    assert (bb.per_hour, bb.per_day, bb.load_gap_s, bb.flat_gap, bb.breaks) == (8, 120, 60.0, False, ())


def test_paddy_moves_are_two_seconds_apart_flat():
    p, t0 = schedule.Pacer(SITES), 1_000_000.0
    p.spaced("paddypower", t0)
    assert not p.may_load("paddypower", t0 + 1.9) and p.may_load("paddypower", t0 + 2.0)
    assert not p.may_load("bookiebashing", t0 + 19.9)          # other sites keep the any-site gap
    p.spaced("bookiebashing", t0 + 10)
    assert not p.may_load("paddypower", t0 + 11.9) and p.may_load("paddypower", t0 + 12.0)


def test_paddy_is_closed_at_night_and_on_its_breaks(tmp_path):
    g = Guard(tmp_path / "g.sqlite3", {n: (r.per_hour, r.per_day) for n, r in SITES.items()},
              breaks={n: r.breaks for n, r in SITES.items()})
    at = lambda h, m: time.mktime((2026, 11, 13, h, m, 0, 0, 0, -1))
    assert g.can_load("paddypower", at(7, 59)) == (False, "quiet hours")
    assert g.can_load("paddypower", at(8, 0))[0]
    assert g.can_load("paddypower", at(23, 59))[0]
    assert g.can_load("paddypower", at(0, 0)) == (False, "quiet hours")
    assert g.can_load("paddypower", at(12, 29))[0]
    assert g.can_load("paddypower", at(12, 30)) == (False, "break")
    assert g.can_load("paddypower", at(12, 59)) == (False, "break")
    assert g.can_load("paddypower", at(13, 0))[0]
    for h, m in ((3, 0), (7, 59), (12, 30), (20, 15)):
        assert g.can_load("bookiebashing", at(h, m), quiet_applies=False)[0]
```

In `test_quiet_hours`, build the guard without `quiet=` and pin the new hours: `at(0)` and `at(7)` are quiet; `at(8)` and `at(23)` are not. `_g` keeps passing `quiet=(1, 7)` only where a test sets `g.quiet` itself.

In `scraper/tests/test_schedule.py` (add `from scraper.sites import SITES`):

```python
def test_a_break_closes_paddys_pages_and_says_so(tmp_path):
    from scraper.tests.test_pacing import _league
    r = _runner(tmp_path, [_league("epl")])
    r.guard.breaks = {n: s.breaks for n, s in SITES.items()}
    at = lambda h, m: datetime(2026, 11, 13, h, m).timestamp()
    r.tick(at(12, 29))
    assert "epl:list" in r.tabs
    r.tick(at(12, 30))
    assert not r.tabs
    st = r.status(at(12, 30))["targets"][0]
    assert (st["state"], st["detail"]) == ("paused", "break")
    r.tick(at(13, 0))
    assert "epl:list" in r.tabs
```

In `scraper/tests/test_pacing.py::test_startup_is_staggered_and_loads_are_spaced`, replace `min(gaps) >= 20` and the `<= 15` hourly loop with:

```python
    pairs = list(zip(loads, loads[1:]))
    assert all(b[1] - a[1] >= 20 for a, b in pairs if "bookiebashing" in (a[0], b[0]))
    assert all(b[1] - a[1] >= SITES["paddypower"].load_gap_s for a, b in pairs)
```

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests/test_guard_config.py scraper/tests/test_schedule.py scraper/tests/test_pacing.py -q`
Expected: FAIL. `QUIET_HOURS` is not defined, and Paddy is still 15/h.

- [ ] **Step 3: Implement the Interfaces.** `__main__._guard` builds `Guard(..., breaks={name: r.breaks for name, r in SITES.items()})`. `Runner._paused` checks `self.guard.in_break(site, now)` after quiet hours and returns `"break"`.

- [ ] **Step 4: Run the Scraper suite.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`
Expected: no failure outside the 10 known ones. `test_e2e_fakesite.py` runs only in CI. Read its Paddy assertions (load counts in `test_keeps_match_tabs_open_and_captures`, `test_block_closes_site_and_cools_off`) and check by hand that they still hold with a 2 s flat gap and 1800/h; fix any assertion that pinned the old numbers.

- [ ] **Step 5: The docs.** Update the `sites.py` docstring and the files in the Files list. Then check that no old Paddy limit is left:
Run: `grep -rn "15 loads\|15/h\|Paddy 90 s\|01:00\|01-07\|01–07" scraper hub README.md docs --include=*.md --include=*.py | grep -v "docs/superpowers/\|docs/decisions.md"`
Expected: no line about Paddy. Bookie Bashing's own "120/day" is not in this pattern. Then commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add scraper/sites.py scraper/schedule.py scraper/guard.py scraper/__main__.py scraper/runner.py \
  scraper/AGENTS.md scraper/README.md hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md \
  scraper/tests/test_guard_config.py scraper/tests/test_pacing.py scraper/tests/test_schedule.py scraper/tests/test_e2e_fakesite.py
git commit -F - <<'EOF'
Scraper: Paddy's limits are Callum's of 11 Nov 2026

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 2: `pp_saves` and its route

**Files:**
- Create: `scraper/parsers/pp_saves.py`, `scraper/tests/test_pp_saves.py`
- Create: `scraper/tests/fixtures/pp_saves/event-arsenal-v-hull.json`, `scraper/tests/fixtures/pp_saves/event-man-city-v-fulham.json`
- Modify: `scraper/parsers/__init__.py` (`PARSERS["pp_saves"]`)
- Modify: `scraper/sinks.py` and `hub/puntmore_hub/inbox.py` (`PRICE_ROUTES` gains the route)
- Modify: `hub/schemas/protocol.schema.json` (route enum; `$defs/paddy_saves_row`; `if`/`then` in `$defs/scraper_prices`)
- Modify: `hub/PROTOCOL.md` (the `scraper_prices` row: three routes; a line with the saves row shape), `scraper/AGENTS.md` and `scraper/README.md` (the parser list gains `pp_saves`)
- Test: `hub/tests/test_protocol.py`, `hub/tests/test_inbox.py`, `scraper/tests/test_sinks_schema.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `pp_saves.SAVES_TYPE = re.compile(r"^GOALKEEPER_TO_MAKE_(\d+)_OR_MORE_SAVES$")` and `pp_saves.EVENT_PAGE = re.compile(r"/smspp/event-page/v\d+")`.
  - `pp_saves.rows_from_event_page(body: dict, log=print) -> list[dict]`. It reads only markets whose type matches `SAVES_TYPE`, with `marketStatus == "OPEN"`, and runners with `runnerStatus == "ACTIVE"` and a price.
    - `team` comes from the runner's name: `f"{home} Goalkeeper"` is home and `f"{away} Goalkeeper"` is away, with home and away from the event's own `"Home v Away"` name.
    - Any other runner name is dropped with `log(f"{event_id} {name}: runner {runner!r} is neither side's goalkeeper; dropped")`.
    - Rows are in the shape in Global Constraints, ordered by `line`, home before away.
  - `pp_saves.read(capture, event_id: str, since: float = 0.0) -> list[dict]`: the rows from the newest `EVENT_PAGE` payload in `capture.recent` with `ts >= since` whose `attachments.events` holds `event_id`, or `[]`.
  - `pp_saves.pp_saves(page, capture) -> list[dict]`: the rows of the newest event-page payload in `capture.recent`. It is registered in `PARSERS`.
  - `PRICE_ROUTES == ("/api/paddy/saves-fair", "/api/paddy/most-prices", "/api/paddy/saves-prices")`, the same in `scraper/sinks.py`, `hub/puntmore_hub/inbox.py` and the schema's route enum.
  - `$defs/paddy_saves_row`:
    - required `event_id` (string, minLength 1), `home` and `away` (string, minLength 1), `kickoff` (string), `team` (enum `home`, `away`), `line` (number, exclusiveMinimum 0), `over` (number, exclusiveMinimum 1);
    - `additionalProperties: false`;
    - applied to `rows.items` only when `route` is `/api/paddy/saves-prices`.

- [ ] **Step 1: Cut the two fixtures.** They are read from `/opt` (read-only) and written to the fixture dir. What is kept:
  - `layout.tabs` (id and title only);
  - `attachments.events` and `attachments.competitions`;
  - the markets typed `GOALKEEPER_TO_MAKE_*`, `MATCH_ODDS` or `#WHATODDSPADDY_-_INPLAY`, each cut to `marketId, eventId, marketName, marketType, marketStatus` and its runners' `selectionId, runnerName, runnerStatus, winRunnerOdds`.

```bash
cd /home/callum/Projects/puntmore-jobs
S=/opt/puntmore-scraper/data/samples; F=scraper/tests/fixtures/pp_saves; mkdir -p $F
cut_event() { ~/.venvs/puntmore/bin/python -I - "$1" "$2" <<'EOF'
import json, sys
b = json.load(open(sys.argv[1])); a = b["attachments"]
keep = lambda m: m["marketType"].startswith("GOALKEEPER_TO_MAKE_") or m["marketType"] in ("MATCH_ODDS", "#WHATODDSPADDY_-_INPLAY")
run = lambda r: {k: r[k] for k in ("selectionId", "runnerName", "runnerStatus", "winRunnerOdds") if k in r}
mkt = lambda m: {**{k: m[k] for k in ("marketId", "eventId", "marketName", "marketType", "marketStatus")},
                 "runners": [run(r) for r in m["runners"]]}
out = {"layout": {"tabs": {k: {"id": v["id"], "title": v["title"]} for k, v in b["layout"]["tabs"].items()}},
       "attachments": {"events": a["events"], "competitions": a["competitions"],
                       "markets": {k: mkt(m) for k, m in a["markets"].items() if keep(m)}}}
json.dump(out, open(sys.argv[2], "w"), indent=1, sort_keys=True)
EOF
}
cut_event "$S/20261107-123420/pp-epl-most_m_https_www.paddypower.com_football_english-premier-league_arsenal-v-hull-36235029/00003-http.txt" $F/event-arsenal-v-hull.json
cut_event "$S/20261111-135929/pp-epl-most_m_https_www.paddypower.com_football_english-premier-league_man-city-v-fulham-36293092/00003-http.txt" $F/event-man-city-v-fulham.json
grep -c "_ak" $F/*.json
```

Expected: the last command prints `0` for both files. Arsenal v Hull has 8 saves markets, `MATCH_ODDS` and one specials market; Man City v Fulham has only `MATCH_ODDS`.

- [ ] **Step 2: Write the failing tests** in `scraper/tests/test_pp_saves.py`:

```python
"""Paddy's per-goalkeeper "N or more saves", read from the 7 Nov 2026 Arsenal v Hull event page and
the 11 Nov 2026 Man City v Fulham one (no saves yet), trimmed into fixtures/pp_saves/."""
import collections
import copy
import json
import pathlib

from scraper.parsers import PARSERS
from scraper.parsers.pp_saves import pp_saves, read, rows_from_event_page

FIX = pathlib.Path(__file__).parent / "fixtures" / "pp_saves"
EP = "https://apisms.paddypower.com/smspp/event-page/v5"
ARS = json.loads((FIX / "event-arsenal-v-hull.json").read_text())
CITY = json.loads((FIX / "event-man-city-v-fulham.json").read_text())
T0 = 1_794_000_000.0


class _Cap:
    def __init__(self, payloads):
        self.recent = collections.deque(payloads)


def _market(body, n):
    return next(m for m in body["attachments"]["markets"].values()
                if m["marketType"] == f"GOALKEEPER_TO_MAKE_{n}_OR_MORE_SAVES")


def test_every_full_match_line_for_both_keepers():
    rows = rows_from_event_page(ARS)
    assert {(r["team"], r["line"]) for r in rows} == {(s, n - 0.5) for s in ("home", "away") for n in range(3, 11)}
    assert len(rows) == 16 and PARSERS["pp_saves"] is pp_saves
    five = {r["team"]: r for r in rows if r["line"] == 4.5}
    assert five["home"] == {"event_id": "36235029", "home": "Arsenal", "away": "Hull",
                            "kickoff": "2026-11-07T15:00:00.000Z", "team": "home", "line": 4.5, "over": 11.0}
    assert five["away"]["over"] == 1.6667
    assert {r["line"]: r["over"] for r in rows if r["team"] == "home"}[9.5] == 126.0
    assert [r["line"] for r in rows] == sorted(r["line"] for r in rows)


def test_first_half_specials_and_match_odds_are_left_out():
    b = copy.deepcopy(ARS)
    fh = copy.deepcopy(_market(b, 3))
    fh.update(marketId="fh", marketType="GOALKEEPER_TO_MAKE_3_OR_MORE_SAVES_IN_THE_FIRST_HALF")
    b["attachments"]["markets"]["fh"] = fh
    assert rows_from_event_page(b) == rows_from_event_page(ARS)
    assert any(m["marketType"] == "#WHATODDSPADDY_-_INPLAY" for m in ARS["attachments"]["markets"].values())


def test_only_open_markets_and_active_runners():
    b = copy.deepcopy(ARS)
    _market(b, 5)["marketStatus"] = "SUSPENDED"
    next(r for r in _market(b, 6)["runners"] if r["runnerName"] == "Arsenal Goalkeeper")["runnerStatus"] = "REMOVED"
    got = {(r["team"], r["line"]) for r in rows_from_event_page(b)}
    assert ("home", 4.5) not in got and ("away", 4.5) not in got
    assert ("home", 5.5) not in got and ("away", 5.5) in got and len(got) == 13


def test_a_keeper_that_matches_neither_side_is_dropped_and_said():
    b = copy.deepcopy(ARS)
    next(r for r in _market(b, 3)["runners"] if r["runnerName"] == "Arsenal Goalkeeper")["runnerName"] = "Gunners Goalkeeper"
    said = []
    rows = rows_from_event_page(b, log=said.append)
    assert len(rows) == 15 and ("home", 2.5) not in {(r["team"], r["line"]) for r in rows}
    assert said == ["36235029 Arsenal v Hull: runner 'Gunners Goalkeeper' is neither side's goalkeeper; dropped"]


def test_a_match_with_no_saves_markets_is_no_rows():
    assert rows_from_event_page(CITY) == []


def test_only_the_match_just_opened_is_read():
    cap = _Cap([(T0, "http", EP, json.dumps(ARS)), (T0 + 5, "http", EP, json.dumps(CITY))])
    assert pp_saves(None, cap) == []                                  # the newest page: City, no saves
    assert read(cap, "36235029", since=T0) == rows_from_event_page(ARS)
    assert read(cap, "36235029", since=T0 + 1) == []                  # its page came before the click
    assert read(cap, "36293092", since=T0 + 1) == []
    assert read(_Cap([]), "36235029") == []
```

In `hub/tests/test_protocol.py`:

```python
PP_SAVES_ROW = {"event_id": "36235029", "home": "Arsenal", "away": "Hull", "kickoff": "2026-11-07T15:00:00.000Z",
                "team": "home", "line": 4.5, "over": 11.0}


def test_a_saves_prices_drop_carries_goalkeeper_rows():
    route = "/api/paddy/saves-prices"
    validator.validate(_prices(route=route, rows=[PP_SAVES_ROW]))
    for bad in ({**PP_SAVES_ROW, "team": "both"}, {**PP_SAVES_ROW, "over": 1.0},
                {k: v for k, v in PP_SAVES_ROW.items() if k != "event_id"}, {**PP_SAVES_ROW, "under": 1.5}, MOST_ROW):
        with pytest.raises(ValidationError):
            validator.validate(_prices(route=route, rows=[bad]))
    validator.validate(_prices(rows=[{"anything": "the other routes still pass rows through"}]))
```

Rename `test_a_prices_drop_names_one_of_the_two_paddy_routes` to `…_the_three_paddy_routes`, and add `validator.validate(_prices(route="/api/paddy/saves-prices", rows=[PP_SAVES_ROW]))`.

In `hub/tests/test_inbox.py`:

```python
def test_paddys_saves_rows_go_to_the_saves_prices_route(pcfg, store):
    row = {"event_id": "36235029", "home": "Arsenal", "away": "Hull", "kickoff": "2026-11-07T15:00:00.000Z",
           "team": "away", "line": 4.5, "over": 1.6667}
    _price_drop(pcfg, time.time(), route="/api/paddy/saves-prices", rows=[row])
    render = _Render(200)
    inbox.ingest(store, pcfg, notify=False, opener=render)
    assert render.sent == [("https://puntmore.example/api/paddy/saves-prices", "Bearer k-ingest", {"prices": [row]})]
```

In `scraper/tests/test_sinks_schema.py`:

```python
def test_a_saves_prices_drop_is_a_valid_envelope(tmp_path):
    t = config._target({"name": "pp-saves", "site": "paddypower", "url": "https://www.paddypower.com/football",
                        "parser": "pp_saves", "post": "/api/paddy/saves-prices"})
    row = {"event_id": "36235029", "home": "Arsenal", "away": "Hull", "kickoff": "2026-11-07T15:00:00.000Z",
           "team": "home", "line": 4.5, "over": 11.0}
    assert deliver_prices(tmp_path, t, [row], time.time())
    SCHEMA.validate(_only(tmp_path / "hub" / "prices"))
```

- [ ] **Step 3: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests/test_pp_saves.py scraper/tests/test_sinks_schema.py -q && (cd hub && ~/.venvs/puntmore/bin/python -m pytest tests/test_protocol.py tests/test_inbox.py -q)`
Expected: FAIL with `ModuleNotFoundError: scraper.parsers.pp_saves`; the Hub tests fail on the route enum.

- [ ] **Step 4: Write `pp_saves.py`**, with a module docstring in `pp_most`'s style (what the capture showed). Then change the two `PRICE_ROUTES`, the schema and `PARSERS`, as in Interfaces. `over` is `round(decimalOdds, 4)` as a float.

- [ ] **Step 5: Run all three suites' affected parts, then the whole Scraper and Hub suites.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q; (cd hub && ~/.venvs/puntmore/bin/python -m pytest -q)`
Expected: no Scraper failure outside the 10 known ones; Hub all passed.

- [ ] **Step 6: The docs** in the Files list, then commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add scraper/parsers/pp_saves.py scraper/parsers/__init__.py scraper/tests/test_pp_saves.py scraper/tests/fixtures/pp_saves \
  scraper/sinks.py hub/puntmore_hub/inbox.py hub/schemas/protocol.schema.json hub/PROTOCOL.md scraper/AGENTS.md scraper/README.md \
  hub/tests/test_protocol.py hub/tests/test_inbox.py scraper/tests/test_sinks_schema.py
git commit -F - <<'EOF'
Scraper: pp_saves reads Paddy's per-goalkeeper saves; the saves-prices route

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 3: The Paddy map

**Files:**
- Create: `scraper/ppmap.py`, `scraper/tests/test_ppmap.py`
- Create: `scraper/tests/fixtures/ppmap/football-links.json`, `football-matches.json`, `competition-epl.json`, `competition-epl-outrights.json`
- Modify: `scraper/browser.py` (`links`)
- Modify: `scraper/runner.py` (`Runner.pmap`; `Tab.map_fed`, `Tab.map_links_url`; `_feed_map` called in `tick` after the parse loop)
- Modify: `scraper/__main__.py` (`map [--json]`, handled before `config.load()` like `targets`; docstring)
- Modify: `scraper/AGENTS.md` (module map: `ppmap.py`), `scraper/README.md` ("The Paddy map": file, what feeds it, `python -m scraper map`)

**Interfaces:**
- Consumes: `pp_saves.SAVES_TYPE` (Task 2), for the saves market types seen.
- Produces (`scraper/ppmap.py`):
  - `MAP_FILE = "maps/paddypower.json"` (under the data dir), `SAVES_RECHECK_S = 3600`, `KEEP_PAST_S = 86400`.
  - `FEEDS = re.compile(r"/smspp/(content-managed-page|competition-page|event-page)/v\d+")`.
  - `HOME_FEED`, `LIST_FEED` and `EVENT_FEED`: the three single-kind regexes.
  - `PROJECT_LEAGUES: dict[str, str]`, in this order:

    | id | name |
    |---|---|
    | 10932509 | English Premier League |
    | 7129730 | English Sky Bet Championship |
    | 35 | English Sky Bet League 1 |
    | 117 | Spanish La Liga |
    | 81 | Italian Serie A |
    | 59 | German Bundesliga |
    | 55 | French Ligue 1 |
    | 228 | UEFA Champions League |
    | 2005 | UEFA Europa League |
    | 12209069 | UEFA Nations League A |
    | 12288805 | UEFA Nations League B |
    | 12288803 | UEFA Nations League C |
    | 12246908 | UEFA Nations League D |

  - `NOT_SEEN = ("Portugal Primeira Liga", "World Cup qualifiers (Europe)", "Euro qualifiers")`.
  - `class PaddyMap` with attributes `competitions: dict[str, dict]`, `events: dict[str, dict]` and `links: dict[str, str]`.
    - `competitions` maps an id to `{name, link_text, url, seen}`.
    - `events` maps an eventId to `{name, home, away, competition_id, kickoff, url, tabs, saves_types, has_saves, saves_checked, seen}`. `kickoff` is the `openDate` string; `has_saves` is None until an event page is seen.
    - `links` maps an EXTERNAL link title to its url.
    - The file on disk is `{"competitions": …, "events": …, "links": …}`.
  - `PaddyMap.load(path) -> PaddyMap`: a missing or unreadable file gives an empty map.
  - `PaddyMap.save_if_changed(path) -> bool`: atomic (temp file + rename); False when nothing changed since the last save or load.
  - `PaddyMap.feed_payload(url: str, text: str, page_url: str | None, now: float) -> None`, for a payload whose URL matches `FEEDS`:
    - It learns competitions from `attachments.competitions`, NAV_NODE titles by `competitionId` into `link_text`, and EXTERNAL titles into `links`.
    - It learns matches from `attachments.events` (only names with `" v "`).
    - On a `competition-page` with one competition, that competition's `url` becomes `page_url`, unless `page_url`'s path ends `-<digits>`.
    - On an `event-page` with one event, it records `tabs` (titles), `saves_types`, `has_saves` and `saves_checked = now`, and sets `url = page_url` when its path ends `-<eventId>`.
    - Anything unreadable is ignored.
  - `PaddyMap.feed_links(links: list[tuple[str, str]], now: float) -> None`: a known event's `url` is set from an href whose path ends `-<eventId>`.
  - `PaddyMap.link_for(cid: str) -> dict`: `{"href": <url, else links[name], else None>, "text": <link_text, else name, else PROJECT_LEAGUES[cid]>}`.
  - `PaddyMap.to_visit(cid: str, now: float, horizon_s: float) -> list[str]`: event ids in that competition kicking off within `[now, now + horizon_s]`, nearest first (ties by id). It leaves out any with `has_saves is False` checked less than `SAVES_RECHECK_S` ago.
  - `PaddyMap.prune(now) -> None`: drops events whose kickoff is more than `KEEP_PAST_S` past.
  - `PaddyMap.report(now: float) -> dict`: `{"competitions": [{"id", "name", "url", "seen", "matches", "with_saves"} for each of PROJECT_LEAGUES, in order], "not_found": list(NOT_SEEN), "events": <count>}`. `matches` counts events still ahead; `with_saves` counts those with `has_saves`.
- Produces (`scraper/browser.py`): `links(page) -> list[tuple[str, str]]`, the `(e.href, innerText trimmed)` of every `a[href]`. It reads the DOM (no request), returns `[]` on any error, and skips any entry that is not a pair.
- Produces (runner):
  - `Runner.pmap: PaddyMap`, loaded at start from `data_dir / MAP_FILE`.
  - `Runner._feed_map(now: float, tabs=None) -> None`. Each Paddy tab feeds its `capture.since(tab.map_fed)` payloads and then advances `map_fed`. When `tab.page.url != tab.map_links_url`, it also feeds `browser.links(tab.page)`. At the end it calls `prune` and `save_if_changed`.
- CLI: `python -m scraper map [--json]` prints `report()`. Without `--json`, there is one line per project league, `f"{name:30} {id:>9}  {url or 'list page not visited yet'}  {matches} match(es), {with_saves} with saves"`, then `"not found on Paddy yet: " + ", ".join(NOT_SEEN)`.

- [ ] **Step 1: Cut the four fixtures.** What is kept from each:
  - `football-links.json`, from `20261111-135929/pp-map-recon/00017-http.txt`: `layout.links` and `attachments.competitions`.
  - `football-matches.json`, from `20261111-135929/pp-map-recon/00022-http.txt`: `attachments.competitions` (all 120), plus `attachments.events` whose `competitionId` is 10932509 or 228.
  - `competition-epl.json`, from `20261107-123420/pp-epl-most_list/00003-http.txt`: `attachments.events` and `attachments.competitions`.
  - `competition-epl-outrights.json`, from `20261107-123420/pp-epl-most_list/00004-http.txt`: `attachments.events` and `attachments.competitions`.

```bash
cd /home/callum/Projects/puntmore-jobs
S=/opt/puntmore-scraper/data/samples; F=scraper/tests/fixtures/ppmap; mkdir -p $F
~/.venvs/puntmore/bin/python -I - "$S" "$F" <<'EOF'
import json, sys
S, F = sys.argv[1], sys.argv[2]
ld = lambda p: json.load(open(f"{S}/{p}"))
dump = lambda name, body: json.dump(body, open(f"{F}/{name}", "w"), indent=1, sort_keys=True)
b = ld("20261111-135929/pp-map-recon/00017-http.txt")
dump("football-links.json", {"layout": {"links": b["layout"]["links"]}, "attachments": {"competitions": b["attachments"]["competitions"]}})
b = ld("20261111-135929/pp-map-recon/00022-http.txt")["attachments"]
dump("football-matches.json", {"attachments": {"competitions": b["competitions"],
     "events": {k: e for k, e in b["events"].items() if e["competitionId"] in (10932509, 228)}}})
for n, name in (("00003", "competition-epl.json"), ("00004", "competition-epl-outrights.json")):
    a = ld(f"20261107-123420/pp-epl-most_list/{n}-http.txt")["attachments"]
    dump(name, {"attachments": {"events": a["events"], "competitions": a["competitions"]}})
EOF
grep -c "_ak" $F/*.json
```

Expected: `0` for each file.

- [ ] **Step 2: Write the failing tests** in `scraper/tests/test_ppmap.py`:

```python
"""The Paddy map, fed from the real payloads (fixtures/ppmap/, fixtures/pp_saves/)."""
import json
import pathlib
from datetime import datetime, timezone

from scraper import ppmap
from scraper.ppmap import NOT_SEEN, PROJECT_LEAGUES, PaddyMap

FIX = pathlib.Path(__file__).parent / "fixtures"
CMP = "https://apisms.paddypower.com/smspp/content-managed-page/v7"
CP = "https://apisms.paddypower.com/smspp/competition-page/v3"
EP = "https://apisms.paddypower.com/smspp/event-page/v5"
FOOTBALL = "https://www.paddypower.com/football"
EPL_LIST = "https://www.paddypower.com/football/english-premier-league"
ARS_URL = EPL_LIST + "/arsenal-v-hull-36235029"
T = datetime(2026, 11, 7, 10, tzinfo=timezone.utc).timestamp()      # a Saturday, before the 12:30 kick-off


def _f(name):
    return (FIX / name).read_text()


def _epl():
    m = PaddyMap()
    m.feed_payload(CMP, _f("ppmap/football-links.json"), FOOTBALL, T)
    m.feed_payload(CMP, _f("ppmap/football-matches.json"), FOOTBALL, T)
    m.feed_payload(CP, _f("ppmap/competition-epl.json"), EPL_LIST, T)
    return m


def test_the_football_page_names_competitions_and_their_links():
    m = PaddyMap()
    m.feed_payload(CMP, _f("ppmap/football-links.json"), FOOTBALL, T)
    assert m.competitions["35"]["name"] == "English Sky Bet League 1" and m.competitions["35"]["url"] is None
    assert (m.competitions["141"]["name"], m.competitions["141"]["link_text"]) == ("US MLS", "US Major League Football")
    assert m.links["English Premier League"] == "https://www.paddypower.com/english-premier-league"
    m.feed_payload(CMP, _f("ppmap/football-matches.json"), FOOTBALL, T)
    assert m.competitions["10932509"]["url"] is None
    assert m.link_for("10932509") == {"href": "https://www.paddypower.com/english-premier-league",
                                      "text": "English Premier League"}
    assert m.link_for("35") == {"href": None, "text": "English Sky Bet League 1"}
    assert m.link_for("2005") == {"href": None, "text": "UEFA Europa League"}


def test_only_matches_become_events():
    m = _epl()
    e = m.events["36225147"]
    assert (e["home"], e["away"], e["competition_id"], e["kickoff"]) == \
        ("Liverpool", "Man Utd", "10932509", "2026-11-22T16:30:00.000Z")
    m.feed_payload(CP, _f("ppmap/competition-epl-outrights.json"), EPL_LIST, T)
    assert all(" v " in e["name"] for e in m.events.values())


def test_a_list_page_url_is_learnt_only_by_being_there():
    assert _epl().competitions["10932509"]["url"] == EPL_LIST
    m = PaddyMap()
    m.feed_payload(CP, _f("ppmap/competition-epl.json"), ARS_URL, T)
    assert m.competitions["10932509"]["url"] is None


def test_an_event_page_records_its_tabs_and_saves():
    m = _epl()
    m.feed_payload(EP, _f("pp_saves/event-arsenal-v-hull.json"), ARS_URL, T)
    e = m.events["36235029"]
    assert e["url"] == ARS_URL and e["has_saves"] is True and e["saves_checked"] == T and len(e["saves_types"]) == 8
    assert sorted(e["tabs"]) == sorted(["Goals", "Over/Under", "Shots", "Bet Builder", "Corners", "Cards & Fouls",
                                        "Other Markets", "Popular"])
    m.feed_payload(EP, _f("pp_saves/event-man-city-v-fulham.json"),
                   EPL_LIST + "/man-city-v-fulham-36293092", T)
    assert m.events["36293092"]["has_saves"] is False


def test_links_on_a_page_give_match_urls():
    m = _epl()
    m.feed_links([(EPL_LIST + "/leeds-v-tottenham-36234722", "Leeds v Tottenham"),
                  ("https://www.paddypower.com/promotions", "Promotions")], T)
    assert m.events["36234722"]["url"] == EPL_LIST + "/leeds-v-tottenham-36234722"


def test_to_visit_is_nearest_first_inside_the_horizon():
    m = _epl()
    ids = m.to_visit("10932509", T, 48 * 3600)
    assert ids[:3] == ["36234722", "36235029", "36235204"] and len(ids) == 9 and "36225147" not in ids
    m.events["36234722"].update(has_saves=False, saves_checked=T)
    assert "36234722" not in m.to_visit("10932509", T + 3599, 48 * 3600)
    assert "36234722" in m.to_visit("10932509", T + 3600, 48 * 3600)


def test_saved_atomically_only_when_changed_and_old_events_drop(tmp_path):
    p = tmp_path / ppmap.MAP_FILE
    m = _epl()
    assert m.save_if_changed(p) is True and m.save_if_changed(p) is False
    assert [f.name for f in p.parent.iterdir()] == ["paddypower.json"]
    assert PaddyMap.load(p).events == m.events
    m.prune(T + 30 * 86400)
    assert m.events == {}
    p.write_text("{not json")
    assert PaddyMap.load(p).events == {}


def test_the_report_names_the_project_leagues_and_the_three_not_seen():
    m = _epl()
    m.feed_payload(EP, _f("pp_saves/event-arsenal-v-hull.json"), ARS_URL, T)
    rep = m.report(T)
    assert [c["id"] for c in rep["competitions"]] == list(PROJECT_LEAGUES)
    epl = rep["competitions"][0]
    assert (epl["url"], epl["matches"], epl["with_saves"]) == (EPL_LIST, 19, 1)     # 10 from the list page + 9 more from 00022
    assert rep["not_found"] == list(NOT_SEEN)


def test_the_runner_feeds_the_map_from_any_paddy_tab(tmp_path):
    from scraper.tests.test_pacing import _league, _runner
    r = _runner(tmp_path, [_league("epl")])
    r.tick(T)
    tab = r.tabs["epl:list"]
    tab.page.url = EPL_LIST
    tab.capture._keep("http", CP, _f("ppmap/competition-epl.json"))
    r.tick(T + 15)
    assert r.pmap.competitions["10932509"]["url"] == EPL_LIST
    assert json.loads((tmp_path / ppmap.MAP_FILE).read_text())["competitions"]["10932509"]["url"] == EPL_LIST


def test_cli_map_json(tmp_path, monkeypatch, capsys):
    from scraper.__main__ import main
    _epl().save_if_changed(tmp_path / ppmap.MAP_FILE)
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    main(["map", "--json"])
    assert json.loads(capsys.readouterr().out)["competitions"][0]["url"] == EPL_LIST
```

- [ ] **Step 3: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests/test_ppmap.py -q`
Expected: FAIL with `ImportError: cannot import name 'ppmap'`.

- [ ] **Step 4: Write `ppmap.py`, `browser.links`, the runner's feed and the CLI** (Interfaces). `_feed_map` runs in every tick after the parse loop and never raises: a broken payload is skipped.

- [ ] **Step 5: Run the Scraper suite.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`
Expected: no failure outside the 10 known ones.

- [ ] **Step 6: The docs** in the Files list, then commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add scraper/ppmap.py scraper/browser.py scraper/runner.py scraper/__main__.py scraper/AGENTS.md scraper/README.md \
  scraper/tests/test_ppmap.py scraper/tests/fixtures/ppmap
git commit -F - <<'EOF'
Scraper: a map of Paddy Power, built from what its pages send

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 4: The rotation: one page round the leagues

**Files:**
- Create: `scraper/rotate.py`, `scraper/tests/test_rotate.py`, `scraper/tests/fake_paddy.py`
- Modify: `scraper/config.py` (`Rotate`; `Target.rotate`, added after `until`; `_target`)
- Modify: `scraper/targets.py` (`ROTATE_FIELDS`; type checks; `_ROTATE_NUMERIC = ("horizon_hours",)`; `render` writes `[target.rotate]`; `to_mapping` lists `competitions`)
- Modify: `scraper/__main__.py` (`--rotate JSON` on `add` and `set`, beside `--matches`; `check` prints it)
- Modify: `scraper/schedule.py` (`Pacer.wait_s`)
- Modify: `scraper/browser.py` (`link_locator`)
- Modify: `scraper/runner.py` (`clock`; `rotations`; `_tick_rotate`, `_rotate_move`, `_move`, `_handle_rows`; the parse loop skips rotation tabs; `_wants`; `_outside_window` and `apply_targets` forget a rotation)
- Modify: `scraper/targets.example.toml` (a disabled `pp-saves` example), `scraper/README.md` ("A rotation job"), `scraper/AGENTS.md` (module map: `rotate.py`; rule: one page, every move through the guard), `hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md` (`--rotate`, `python -m scraper map`), `docs/dataflow.md` (stages 1–2: the Paddy saves job), `docs/architecture.md` (where it lists the Scraper's target kinds)

**Interfaces:**
- Consumes:
  - Task 1's `Pacer` and `Guard`;
  - Task 2's `pp_saves.read` and `"/api/paddy/saves-prices"`;
  - Task 3's `PaddyMap.link_for`, `to_visit`, `competitions[cid]["url"]`, `events[eid]["url"]`, `feed_*`, `LIST_FEED`, `EVENT_FEED`, `PROJECT_LEAGUES`, `browser.links` and `Runner._feed_map`;
  - Plan J's `schedule.inactive`, `Runner._say`, `Runner._outside_window` and the `targets` CLI.
- Produces:
  - `config.Rotate(competitions: tuple[str, ...] = (), horizon_hours: float = 48.0)`. `horizon_hours` is clamped to 1..168; competition ids may be given as numbers or digit strings and are stored as digit strings.
  - `config._target` refuses:
    - `"target {name}: a target has matches or rotate, not both"`;
    - `"target {name}: rotate is for site paddypower"`;
    - `"target {name}: a rotate target reads with parser pp_saves"`;
    - `"target {name}: rotate.competitions must be Paddy competition ids"`.
  - `targets.py`: `"rotate"` passes through `validate` like `"matches"`. A clamped `horizon_hours` is refused, as other clamps are.
  - `schedule.Pacer.wait_s(site, now) -> float`: seconds until `may_load` would say yes (0.0 when it does).
  - `browser.link_locator(page, *, href=None, suffix=None, text=None) -> tuple[locator, str] | None`.
    - It returns the first `a[href]` (in `links(page)` order) whose href equals `href` (ignoring a trailing `/` and any `#…`), or whose URL path ends with `suffix`, or whose text equals `text` (ignoring case).
    - The locator is `page.locator("a[href]").nth(i)`, paired with its href.
  - `rotate.ROTATE_SLICE_S = 45.0`.
  - `rotate.Move(kind: str, competition_id: str | None = None, event_id: str | None = None)`: frozen. `kind` is one of `"open"`, `"competition"`, `"match"`, `"back"`, `"done"`.
  - `rotate.Rotation(target, pmap)`, with:
    - `stage: str` (`"idle" | "home" | "list" | "match"`) and `next_pass_at: float`;
    - `due(now) -> bool`;
    - `next_move(page_url: str | None, now: float) -> Move | None`;
    - `moved(move: Move, ok: bool, now: float, *, rows: int = 0, saves: bool = False) -> None`;
    - `pass_line(now) -> str`: `f"pass done: {visited} match(es) visited, {with_saves} with saves, {rows} row(s) written, {minutes:.1f} min"`.
  - `Runner.clock = time.time` (a seam for tests) and `Runner.rotations: dict[str, Rotation]`.
  - `Runner._wants(t, now)` for a rotate target: True when it has no rotation yet or its rotation is `due(now)`.
  - `Runner._tick_rotate(t, now) -> None`, `Runner._rotate_move(t, rot, move, now) -> tuple[bool, int, bool] | None` (None = the guard or pacer said wait; else `(ok, rows, saves)`), `Runner._handle_rows(t, url: str, rows: list[dict], now: float) -> None`.
  - `Runner._move(t, tab, act, feed, now) -> tuple[bool, bool] | None`:
    1. None if `guard.can_load` or the pacer says no.
    2. Otherwise `pacer.spaced`, then `act()` (a `browser.click` or `page.go_back(wait_until="domcontentloaded", timeout=30_000)`), then `browser.wait_for_feed(page, cap, clicked_at, feed)` when `feed` is given.
    3. `guard.record_load` when `page.url` or `cap.documents` changed.
    4. Then `browser.blocked(page)`, which leads to `_on_blocked` on a block.
    5. Returns `(changed, fed)`: whether the page's URL changed, and whether `feed` arrived.
  - URLs are compared without a trailing `/` and without `#…`, here and in the state machine.
- The state machine, `Rotation.next_move` / `moved`:

  | stage | condition | `next_move` | after `moved(ok=True)` | after `moved(ok=False)` |
  |---|---|---|---|---|
  | idle | `now < next_pass_at` | None | — | — |
  | idle | due | starts a pass: the scope is `rotate.competitions` or `PROJECT_LEAGUES`, in that order; the stage becomes home; then as home | | |
  | home | `page_url != t.url` | `open` | home | the pass ends (`done`) |
  | home | competitions left | `competition` (the next one) | list | home; that competition is skipped |
  | home | none left | `done`, once; then idle with `next_pass_at = max(started + every_min*60, now)` | — | — |
  | list | `page_url` is not that competition's map `url` | `open`; the stage becomes home | | |
  | list | `pmap.to_visit(...)` has an id not tried in this pass | `match` (it is marked tried) | match; visited += 1, rows and with_saves added | list |
  | list | none left | `back` | home | home |
  | match | — | `back` | list | list |

- The runner's side of each move:
  - **open:** checks the pacer and guard first (as `_tick_fetch` does), then `self._load(t, t.name, t.url, now)`. None after that is a failure.
  - **competition:** `pmap.link_for(cid)`, then `link_locator(href=…)`, then `link_locator(text=…)`. With no link it does `_say(t.name, f"no link for {text} on {page.url}; links on the page: {', '.join(texts[:15])}")` and reports `ok=False`. Otherwise it calls `_move` with `LIST_FEED`, and `ok` is `changed and fed`.
  - **match:** `link_locator(href=event url)`, else `suffix=f"-{eid}"`. With no link it says the same "no link for …" line (with the event's name) and reports `ok=False`. Otherwise it notes `since = time.time()`, calls `_move` with `EVENT_FEED`, and `ok` is `changed`: a page that opened without a payload is still gone back from. Then `rows = pp_saves.read(tab.capture, eid, since=since)`, then `_feed_map`, then `_handle_rows` when there are rows. `saves` is the map's `has_saves is True`.
  - **back:** `_move` with no feed; `ok` is `changed`.
  - **done:** `_say(t.name, rot.pass_line(now))`.
  - After every move, `_feed_map(now, [tab])`.

- [ ] **Step 1: Write `scraper/tests/fake_paddy.py`.** It is a Paddy tab with no network. `FakePaddy(_Nothing)` (from `test_pacing`) takes `pages: dict[str, tuple[list[tuple[str, str]], list[tuple[str, str]]]]`: a URL maps to (its links as (href, text), its payloads as (feed URL, body text)).
  - `goto(url, **kw)` and `follow(href)` arrive at a page: they set `url`, append to `history`, and pass each payload to the `"response"` handler registered with `on()`, as `test_capture._Resp(self, feed_url, body)`. `goto` returns an object with `status = 200`.
  - `go_back(**kw)` pops `history` and sets `url`, with no payload.
  - `eval_on_selector_all(sel, js)` returns `[[href, text], …]` for the current page.
  - `locator(sel)` returns an object whose `nth(i)` has `.href` (the i-th link), and whose `count()` is 0, so `browser.blocked` sees no challenge.

  Also `class Clock`: each call returns the next time, starting at `t0` and stepping by `step`.

- [ ] **Step 2: Write the failing tests** in `scraper/tests/test_rotate.py`:

```python
"""The rotation: the pure state machine on the real map fixtures, then the runner on a fake Paddy page."""
import json

import pytest

from scraper import browser, config, joblog
from scraper.rotate import Move, Rotation
from scraper.tests.fake_paddy import Clock, FakePaddy
from scraper.tests.test_pacing import _loads, _runner
from scraper.tests.test_ppmap import CMP, CP, EP, EPL_LIST, FOOTBALL, T, _epl, _f

EPL_EXT = "https://www.paddypower.com/english-premier-league"
ARS_URL = EPL_EXT + "/arsenal-v-hull-36235029"
LEEDS_URL = EPL_EXT + "/leeds-v-tottenham-36234722"


def _target(**kw):
    return config._target({"name": "pp-saves", "site": "paddypower", "url": FOOTBALL, "every_min": 15,
                           "parser": "pp_saves", "post": "/api/paddy/saves-prices",
                           "rotate": {"competitions": ["10932509"]}, **kw})


def _walk(rot, t0, step=0.0):
    """One pass on a page that always arrives; Arsenal is the only match with saves."""
    moves, url, i = [], None, 0
    while True:
        now = t0 + step * i
        mv = rot.next_move(url, now)
        moves.append(mv)
        if mv is None or mv.kind == "done":
            return moves, now
        url = {"open": FOOTBALL, "competition": EPL_LIST,
               "match": f"{EPL_LIST}/x-{mv.event_id}"}.get(mv.kind) or (EPL_LIST if url.endswith(tuple("0123456789")) else FOOTBALL)
        ars = mv.event_id == "36235029"
        rot.moved(mv, True, now, rows=16 if ars else 0, saves=ars)
        i += 1


def test_a_pass_goes_home_list_match_back_nearest_first():
    rot = Rotation(_target(), _epl())
    moves, _ = _walk(rot, T)
    assert moves[:4] == [Move("open"), Move("competition", competition_id="10932509"),
                         Move("match", event_id="36234722"), Move("back")]
    matches = [m.event_id for m in moves if m and m.kind == "match"]
    assert len(matches) == 9 and matches[:3] == ["36234722", "36235029", "36235204"]
    assert moves[-2:] == [Move("back"), Move("done")]
    assert rot.pass_line(T) == "pass done: 9 match(es) visited, 1 with saves, 16 row(s) written, 0.0 min"
    assert rot.next_move(FOOTBALL, T + 899) is None
    assert rot.next_move(FOOTBALL, T + 900) == Move("competition", competition_id="10932509")


def test_a_pass_that_overruns_is_followed_at_once_and_never_overlaps():
    rot = Rotation(_target(), _epl())
    _, ended = _walk(rot, T, step=60.0)                    # ~22 moves a minute apart: longer than 15 min
    assert ended > T + 900 and rot.next_pass_at == ended


def test_a_recent_match_without_saves_is_left_for_an_hour():
    m = _epl()
    m.events["36234722"].update(has_saves=False, saves_checked=T - 60)
    moves, _ = _walk(Rotation(_target(), m), T)
    assert "36234722" not in [x.event_id for x in moves if x]


def test_a_page_that_is_not_where_the_rotation_left_it_is_reopened():
    rot = Rotation(_target(), _epl())
    rot.moved(rot.next_move(None, T), True, T)                                  # open
    rot.moved(rot.next_move(FOOTBALL, T), True, T)                              # competition
    assert rot.next_move("https://www.paddypower.com/promotions", T) == Move("open")


def test_a_rotate_target_is_paddys_own_and_reads_with_pp_saves():
    for bad, why in (({"site": "bookiebashing", "url": "https://www.bookiebashing.net/x"}, "rotate is for site paddypower"),
                     ({"parser": "pp_most"}, "reads with parser pp_saves"),
                     ({"matches": {}}, "matches or rotate, not both"),
                     ({"rotate": {"competitions": ["EPL"]}}, "must be Paddy competition ids")):
        with pytest.raises(ValueError, match=why):
            _target(**bad)
    assert _target(rotate={"competitions": [10932509]}).rotate.competitions == ("10932509",)


@pytest.fixture
def paddy(tmp_path, monkeypatch):
    """The runner on a fake Paddy: the football page links to the Premier League, whose list page
    links to two of its nine matches; only Arsenal v Hull's page brings an event payload."""
    page = FakePaddy({
        FOOTBALL: ([(EPL_EXT, "English Premier League")],
                   [(CMP, _f("ppmap/football-links.json")), (CMP, _f("ppmap/football-matches.json"))]),
        EPL_EXT: ([(ARS_URL, "Arsenal v Hull"), (LEEDS_URL, "Leeds v Tottenham")], [(CP, _f("ppmap/competition-epl.json"))]),
        ARS_URL: ([], [(EP, _f("pp_saves/event-arsenal-v-hull.json"))]),
        LEEDS_URL: ([], []),
    })
    monkeypatch.setattr(browser, "click", lambda pg, loc: pg.follow(loc.href))
    monkeypatch.setattr(browser, "wait_for_feed", lambda pg, cap, since, feed, timeout_s=12.0:
                        any(feed.search(u) for _, _, u, _ in cap.since(since)))
    r = _runner(tmp_path, [_target(days=("sat",))])
    r.ctx.new_page = lambda: page
    r.clock = Clock(T, 2.5)
    return r, page


def _run(r, t0):
    for i in range(40):
        r.tick(t0 + 60 * i)                                 # ticks further apart than a 45 s slice
        if any(x["msg"].startswith("pass done") for x in joblog.read(r.s.data_dir, "pp-saves")):
            return


def test_one_pass_through_the_runner(paddy, tmp_path):
    r, page = paddy
    _run(r, T)
    files = sorted((tmp_path / "hub" / "prices").glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text())["body"]
    assert body["route"] == "/api/paddy/saves-prices" and len(body["rows"]) == 16
    assert {row["event_id"] for row in body["rows"]} == {"36235029"}
    said = [x["msg"] for x in joblog.read(tmp_path, "pp-saves", n=200)]
    assert "pass done: 2 match(es) visited, 1 with saves, 16 row(s) written" in said[-1]
    assert sum(1 for s in said if s.startswith("no link for")) == 7
    ts = [t for _site, t in _loads(r)]
    assert len(ts) == 7 and all(b - a >= 2.0 for a, b in zip(ts, ts[1:]))
    assert r.pmap.competitions["10932509"]["url"] == EPL_EXT and r.pmap.events["36235029"]["has_saves"] is True
    assert page.url == FOOTBALL


def test_a_spent_budget_stops_the_rotation_where_it_is(paddy):
    r, _ = paddy
    for _ in range(1800):
        r.guard.record_load("paddypower", "u", T)
    r.tick(T + 1)
    assert "pp-saves" not in r.tabs and len(_loads(r)) == 1800


def test_off_its_days_the_page_closes_and_the_pass_starts_again(paddy):
    r, _ = paddy
    r.tick(T)
    assert "pp-saves" in r.tabs
    r.tick(T + 86400)                                       # Sunday
    assert "pp-saves" not in r.tabs and "pp-saves" not in r.rotations
```

In `scraper/tests/test_targets_lib.py`, add a CLI round trip:

```python
def test_cli_rotate_round_trip(cli, path):
    code, out, _ = cli("add", "--name", "pp-saves", "--site", "paddypower", "--url", "https://www.paddypower.com/football",
                       "--parser", "pp_saves", "--post", "/api/paddy/saves-prices", "--every-min", "15",
                       "--days", "fri,sat", "--rotate", '{"horizon_hours": 48}', "--json")
    d = json.loads(out)
    assert code == 0 and d["rotate"] == {"competitions": [], "horizon_hours": 48.0}
    assert "[target.rotate]" in path.read_text() and T.get(path, "pp-saves").rotate.horizon_hours == 48.0
    code, out, _ = cli("set", "pp-saves", "--rotate", '{"horizon_hours": 500}', "--json")
    assert code == 2 and "horizon_hours" in json.loads(out)["error"]
```

- [ ] **Step 3: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests/test_rotate.py scraper/tests/test_targets_lib.py -q`
Expected: FAIL with `ModuleNotFoundError: scraper.rotate`.

- [ ] **Step 4: Add `Rotate` to config and targets, the CLI flag, `Pacer.wait_s` and `browser.link_locator`** (Interfaces).

- [ ] **Step 5: Write `rotate.py`** from the transition table.

- [ ] **Step 6: Wire the runner** (Interfaces; Planner's calls 3 and 6).
  - `tick` dispatches `t.rotate` to `_tick_rotate` beside `t.matches`.
  - `_tick_rotate` loops while `self.clock() - start < ROTATE_SLICE_S`, with `step_now = now + (self.clock() - start)`.
    - When the guard says no, it stops the slice.
    - When only the pacer says wait, it calls `tab.page.wait_for_timeout(int(1000 * pacer.wait_s(site, step_now)))` and asks again.
  - `_parse` becomes "read rows, then `_handle_rows`". The loop over tabs skips any whose target has `rotate`.

- [ ] **Step 7: Run the Scraper suite.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`
Expected: no failure outside the 10 known ones.

- [ ] **Step 8: The docs** in the Files list. The example in `targets.example.toml` is `enabled = false`, so `test_example_targets_parse` still loads it. The README example is the Live proof's `targets add` line. The pass time is stated as "a few minutes for 50 matches (at least ~3.5 min at 2 s a move)". Commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add scraper/rotate.py scraper/config.py scraper/targets.py scraper/__main__.py scraper/schedule.py scraper/browser.py \
  scraper/runner.py scraper/targets.example.toml scraper/README.md scraper/AGENTS.md \
  hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md docs/dataflow.md docs/architecture.md \
  scraper/tests/test_rotate.py scraper/tests/fake_paddy.py scraper/tests/test_targets_lib.py
git commit -F - <<'EOF'
Scraper: one Paddy page goes round the leagues for goalkeeper saves

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 5: Render stores Paddy's saves prices

**Files:**
- Modify: `render/api/app/cards/saves.py` (`PaddySaves`, `PADDY`, `store_paddy`, `paddy_prices`)
- Modify: `render/api/app/routers/saves_ingest.py` (`PaddySavesIn`, `PaddySavesBatchIn`, `POST /saves-prices`, `GET /saves-prices`; module docstring)
- Modify: `render/api/app/schemas.py` (`PaddySavesOut`, `PaddySavesListOut`)
- Modify: `docs/api.md` (the Paddy ingest row: `POST`/`GET /paddy/saves-prices`), `docs/architecture.md` (the "prices: POST …" edge and the "Paddy prices up" row), `docs/dataflow.md` (stage 2: Paddy's saves prices off a Paddy page), `README.md` (line 53: where Paddy's saves prices come from)
- Test: Create `render/api/tests/test_paddy_saves_prices.py`

**Interfaces:**
- Consumes: the row shape from Global Constraints; the route from Task 2.
- Produces:
  - `saves.PaddySaves(event_id: str, home: str, away: str, kickoff: datetime, team: str, line: float, over: float)`: a dataclass.
  - `saves.PADDY = TTLCache(ttl=FAIR_TTL, maxsize=FAIR_MAXSIZE)`, keyed by match `(home.lower(), away.lower(), kickoff.date())`, holding that match's list.
  - `saves.store_paddy(entries: list[PaddySaves]) -> None`: each match in the batch has its list replaced whole.
  - `saves.paddy_prices() -> list[PaddySaves]`: every stored row.
  - `PaddySavesIn`: `event_id` (str, 1–20), `home` and `away` (str, 1–80), `kickoff` (str), `team` (`Literal["home", "away"]`), `line` (`Field(gt=0, lt=40)`), `over` (`Field(gt=1, lt=1001)`). `PaddySavesBatchIn(prices: list[PaddySavesIn] = Field(max_length=2000))`.
  - `POST /saves-prices` takes `IngestOut` and the `require_ingest_key` dependency. It rejects `"kickoff unreadable"` and `"line must be a half line (N.5)"`.
  - `GET /saves-prices` returns `PaddySavesListOut(count: int, prices: list[PaddySavesOut])` under the same dependency. The prices are sorted by kickoff, home, team (home first) and line; `kickoff` is ISO 8601 with `+00:00`.

- [ ] **Step 1: Write the failing tests** in `render/api/tests/test_paddy_saves_prices.py`:

```python
"""Paddy's per-goalkeeper saves prices, read off Paddy's pages by the Scraper and posted by the Hub."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cards import saves
from app.config import settings
from app.routers import saves_ingest

H = {"X-API-Key": "k-123"}
ROW = {"event_id": "36235029", "home": "Arsenal", "away": "Hull", "kickoff": "2026-11-07T15:00:00.000Z",
       "team": "home", "line": 4.5, "over": 11.0}
LEEDS = {**ROW, "event_id": "36234722", "home": "Leeds", "away": "Tottenham", "kickoff": "2026-11-07T12:30:00.000Z"}


@pytest.fixture
def client(monkeypatch):
    saves.PADDY.clear()
    monkeypatch.setattr(settings, "saves_ingest_key", "k-123")
    app = FastAPI()
    app.include_router(saves_ingest.router, prefix="/paddy")
    yield TestClient(app)
    saves.PADDY.clear()


def test_both_routes_need_the_ingest_key(client):
    assert client.post("/paddy/saves-prices", json={"prices": []}).status_code == 401
    assert client.get("/paddy/saves-prices").status_code == 401


def test_paddys_rows_are_stored_and_read_back(client):
    rows = [ROW, {**ROW, "team": "away", "over": 1.6667}, {**ROW, "line": 8.5, "over": 126.0}]
    assert client.post("/paddy/saves-prices", headers=H, json={"prices": rows}).json() == {"stored": 3, "rejected": []}
    got = client.get("/paddy/saves-prices", headers=H).json()
    assert got["count"] == 3
    assert got["prices"][0] == {**ROW, "kickoff": "2026-11-07T15:00:00+00:00"}
    assert [(p["team"], p["line"]) for p in got["prices"]] == [("home", 4.5), ("home", 8.5), ("away", 4.5)]


def test_each_post_replaces_that_matchs_rows_and_no_other(client):
    client.post("/paddy/saves-prices", headers=H, json={"prices": [ROW, {**ROW, "line": 5.5}, LEEDS]})
    client.post("/paddy/saves-prices", headers=H, json={"prices": [{**ROW, "line": 9.5, "over": 3.0}]})
    got = client.get("/paddy/saves-prices", headers=H).json()["prices"]
    assert sorted((p["home"], p["line"]) for p in got) == [("Arsenal", 9.5), ("Leeds", 4.5)]


def test_bad_rows_are_refused_with_a_reason(client):
    r = client.post("/paddy/saves-prices", headers=H, json={"prices": [
        {**ROW, "kickoff": "soon"}, {**ROW, "line": 5.0}, ROW]})
    assert r.json()["stored"] == 1
    assert [x["reason"] for x in r.json()["rejected"]] == ["kickoff unreadable", "line must be a half line (N.5)"]
    for bad in ({**ROW, "team": "both"}, {**ROW, "over": 1.0}, {**ROW, "over": 1001.0}):
        assert client.post("/paddy/saves-prices", headers=H, json={"prices": [bad]}).status_code == 422
```

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs/render/api && .venv/bin/python -m pytest tests/test_paddy_saves_prices.py -q`
Expected: FAIL with `AttributeError: module 'app.cards.saves' has no attribute 'PADDY'`.

- [ ] **Step 3: Implement the Interfaces.** Use `is_half_line` (already imported in `saves.py`) for the line check.

- [ ] **Step 4: Run the Render suite.**
Run: `cd /home/callum/Projects/puntmore-jobs/render/api && .venv/bin/python -m pytest tests/ -q`
Expected: 1054 + 4 passed, 1 skipped.

- [ ] **Step 5: The docs** in the Files list, then commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add render/api/app/cards/saves.py render/api/app/routers/saves_ingest.py render/api/app/schemas.py \
  render/api/tests/test_paddy_saves_prices.py docs/api.md docs/architecture.md docs/dataflow.md README.md
git commit -F - <<'EOF'
Render: store Paddy's per-goalkeeper saves prices

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 6: Render joins a goalkeeper's fair to Paddy's price

**Files:**
- Modify: `render/api/app/cards/saves.py` (`FairSaves.team`; the store key; `saves_value`; module docstring; drop the `oddspapi` import)
- Modify: `render/api/app/routers/saves_ingest.py` (`FairSavesIn.team`)
- Modify: `render/api/app/cards/feed.py` (`price_events` adds saves rows for Paddy Power only; `describe` for `saves_home`/`saves_away`)
- Modify: `render/api/app/cards/oddspapi.py` (the comment "Saves ride along on Paddy's side…" is replaced by "saves totals are fetched for Hills' legs and the diagnostics only; the Paddy saves card does not read them")
- Modify: `render/api/AGENTS.md` (the "Goalkeeper SAVES" paragraph: Paddy's price from Paddy's pages via `/saves-prices`, per goalkeeper; a fair with `team` is per goalkeeper; a match-total fair joins nothing; the card stays empty until a per-goalkeeper fair arrives), `docs/api.md` (`/paddy/saves-fair` takes an optional `team`)
- Test: `render/api/tests/test_saves.py` (rewritten around the new join; the `complete` and key tests stay)

**Interfaces:**
- Consumes: Task 5's `PaddySaves`, `PADDY` and `paddy_prices()`.
- Produces:
  - `FairSaves(home, away, kickoff, line, over, under, team: str | None = None)`. The store key gains `team`.
  - `FairSavesIn.team: Literal["home", "away"] | None = None`.
  - `saves.saves_value(events: list[dict], min_edge: float, fairs=None, paddy=None) -> list[CardValueRow]`. `fairs` defaults to `STORE.values()` and `paddy` to `paddy_prices()`. The `book` parameter is gone.
    - For each event, the Paddy rows and the fairs that `team_similarity` (≥ `MIN_TEAM_SIMILARITY` both sides) and the kickoff (within `KICKOFF_TOLERANCE_HOURS`) place on it are joined on `(team, line)`. Only fairs with a `team` take part.
    - A join makes one row: `market_key=f"saves_{team}"`, `side="Over"`, `line`, `paddy_odds=round(paddy.over, 3)`, `pinnacle_odds=fair_odds=round(fair.over, 3)`, `pinnacle_margin=0.0`, `edge=round(paddy.over / fair.over - 1, 4)`, `basis="saves"` and `market_label=_market_label(market_key, "Over", line)`. It is kept when `edge >= min_edge`.
  - `feed.price_events(events, min_edge, book=PADDY_POWER)` adds `saves_value(events, min_edge)` only when `book == PADDY_POWER`.
  - `feed.describe(bet)` gives `f"{team} GK Over {bet.line:.1f} saves"` for `saves_home`/`saves_away`, where `team` is the home or away team.

- [ ] **Step 1: Write the failing tests.** Rewrite `render/api/tests/test_saves.py`, keeping `test_a_missing_side_is_the_complement_and_a_vigged_pair_is_refused`, the `client` fixture and the two ingest-key tests:

```python
KO = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)


def _event(home="Arsenal", away="Hull", ko="2026-11-07T15:00:00Z"):
    return {"id": "fx1", "home_team": home, "away_team": away, "commence_time": ko, "sport_key": "soccer_epl",
            "bookmakers": [{"key": "paddypower", "markets": []}]}


def _paddy(team="home", line=4.5, over=11.0, home="Arsenal", away="Hull", ko=KO):
    return saves.PaddySaves("36235029", home, away, ko, team, line, over)


def _fair(team="home", line=4.5, over=9.0, home="Arsenal FC", away="Hull", ko=KO):
    return saves.FairSaves(home, away, ko, line, over, 1 / (1 - 1 / over), team=team)


def test_a_keepers_paddy_price_is_valued_against_that_keepers_fair():
    rows = saves.saves_value([_event()], 0.0, fairs=[_fair()], paddy=[_paddy()])
    assert [(r.market_key, r.side, r.line, r.paddy_odds, r.fair_odds, r.edge) for r in rows] == \
        [("saves_home", "Over", 4.5, 11.0, 9.0, round(11.0 / 9.0 - 1, 4))]
    assert rows[0].basis == "saves" and rows[0].pinnacle_margin == 0.0


def test_a_match_total_fair_joins_nothing():
    total = saves.FairSaves("Arsenal", "Hull", KO, 4.5, 9.0, 1.125)
    assert saves.saves_value([_event()], -1.0, fairs=[total], paddy=[_paddy()]) == []


def test_the_other_keeper_another_line_day_or_match_joins_nothing():
    for fair, paddy in ((_fair(team="away"), _paddy()), (_fair(line=5.5), _paddy()),
                        (_fair(), _paddy(ko=KO + timedelta(days=7))), (_fair(home="Everton"), _paddy())):
        assert saves.saves_value([_event()], -1.0, fairs=[fair], paddy=[paddy]) == []


def test_saves_rows_are_paddys_only_and_reach_price_events():
    from app.cards.feed import price_events
    saves.store([_fair()])
    saves.store_paddy([_paddy()])
    assert [r.market_key for r in price_events([_event()], 0.0)] == ["saves_home"]
    assert price_events([_event()], 0.0, book="williamhill") == []


def test_the_saves_card_reads_nothing_from_oddspapi():
    import inspect
    src = inspect.getsource(saves)
    assert "oddspapi" not in src.lower() and "totals_saves" not in src


def test_the_saves_fair_takes_a_team(client):
    r = client.post("/paddy/saves-fair", headers={"X-API-Key": "k-123"}, json={"prices": [
        {"home": "Arsenal", "away": "Hull", "kickoff": "2026-11-07T15:00:00Z", "line": 4.5, "over": 9.0, "team": "home"}]})
    assert r.json()["stored"] == 1 and [e.team for e in saves.STORE.values()] == ["home"]
    r = client.post("/paddy/saves-fair", headers={"X-API-Key": "k-123"}, json={"prices": [
        {"home": "Arsenal", "away": "Hull", "kickoff": "2026-11-07T15:00:00Z", "line": 4.5, "over": 9.0, "team": "both"}]})
    assert r.status_code == 422
```

The autouse fixture clears both `saves.STORE` and `saves.PADDY`. Add `timedelta` to the datetime import. Use Hills' book key as `feed.py` and `routers/hills.py` pass it (check the constant; `"williamhill"` above is its expected value).

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs/render/api && .venv/bin/python -m pytest tests/test_saves.py -q`
Expected: FAIL. `FairSaves` takes no `team`, and `saves_value` takes no `paddy`.

- [ ] **Step 3: Implement the Interfaces.** Rewrite the `saves.py` docstring:
  - Paddy's price is read off Paddy's match pages by the Scraper (`pp_saves`) and posted to `/saves-prices`, per goalkeeper, "N or more" (line N − 0.5, Over only).
  - The fair comes from Bookie Bashing; a fair with a `team` is per goalkeeper.
  - A match-total fair joins nothing, because Paddy sells no match-total saves market.

- [ ] **Step 4: Run the Render suite, then check the strings that must be gone.**
Run: `cd /home/callum/Projects/puntmore-jobs/render/api && .venv/bin/python -m pytest tests/ -q`
Expected: all passed, 1 skipped.
Run: `grep -rn "Saves ride along\|Paddy's line (OddsPapi)\|Paddy Power's line from OddsPapi" render/api/app render/api/tests`
Expected: no output.

- [ ] **Step 5: The docs** in the Files list, then commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add render/api/app/cards/saves.py render/api/app/routers/saves_ingest.py render/api/app/cards/feed.py \
  render/api/app/cards/oddspapi.py render/api/AGENTS.md docs/api.md render/api/tests/test_saves.py
git commit -F - <<'EOF'
Render: the saves card joins a goalkeeper's fair to Paddy's own price

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 7: Live proof (controller only, not a builder task)

**Run by the controller after the pull request is merged and the box has updated.** Do not:
- write hand-made or replayed price files;
- `curl -X POST` to `/api/paddy/*`;
- edit `targets.toml` by hand;
- touch `bb-saves`.

If a step fails, or the Scraper reports a block, stop and report to Callum. Run it on a Friday or Saturday, between 08:00 and midnight, outside 12:30–13:00, 15:00–15:30, 17:30–18:00 and 20:00–20:30. Below, `S` is `cd ~/Projects/puntmore && SCRAPER_DATA_DIR=/opt/puntmore-scraper/data PYTHONPATH=. ~/.venvs/puntmore/bin/python -m scraper`.

- [ ] **Step 1: The box is on the merge.**
Run: `git -C ~/Projects/puntmore log -1 --format='%h %s'` and `systemctl --user is-active puntmore-scraper puntmore-link`.
Expected: the merge sha, and `active` twice.
- [ ] **Step 2: Add the job through the CLI.**
Run: `$S targets add --name pp-saves --site paddypower --url https://www.paddypower.com/football --parser pp_saves --post /api/paddy/saves-prices --every-min 15 --days fri,sat --rotate '{"horizon_hours": 48}' --json`
Expected: exit 0, `"schedule": "every 15 min · Fri, Sat · all day · forever"`.
- [ ] **Step 3: The Scraper picks it up within 60 s.**
Run: `$S targets show pp-saves --json | jq .status.state`
Expected: `"running"`.
- [ ] **Step 4: Watch one pass in the job's log.**
Run: `$S targets log pp-saves -n 100` every minute.
Expected, in order:
  - a competition click, then match visits;
  - `N row(s) handed to the Hub (/api/paddy/saves-prices)` for matches with saves;
  - `no link for …` lines, if any, carrying what was on the page;
  - then one `pass done: … match(es) visited, … with saves, … row(s) written, … min` line.

  Note the pass time.
- [ ] **Step 5: The map fills.**
Run: `$S map`
Expected:
  - the leagues visited show their list-page URL and their matches, and the ones with saves are counted;
  - the three not seen are listed as "not found on Paddy yet".
- [ ] **Step 6: A `scraper_prices` file reaches Render.**
Run: `ls -t /opt/puntmore-scraper/data/hub/done/prices | grep pp-saves | head -1`, then `jq '.body.route, (.body.rows | length)'` on that file.
Expected: `"/api/paddy/saves-prices"` and a count.
Then run: `journalctl --user -u puntmore-link --since "-20 min" | grep "pp-saves -> /api/paddy/saves-prices"`.
Expected: `sent N row(s), Render answered {"stored": N, "rejected": []}`.
- [ ] **Step 7: Render holds them.** Read the key without printing it: `KEY=$(grep '^PUNTMORE_RENDER_API_KEY=' ~/Projects/puntmore/hub/.env | cut -d= -f2-)`. Then run: `curl -s -H "Authorization: Bearer $KEY" https://puntmore.com/api/paddy/saves-prices | jq '.count, .prices[0]'`.
Expected: a count of at least N. That match's rows equal the done file's rows, with the kickoff as `+00:00`.
- [ ] **Step 8: The guard held.**
Run: `$S status`
Expected: paddypower's loads this hour are at most 1800, with no cool-off.
Run: `sqlite3 /opt/puntmore-scraper/data/guard.sqlite3 "select min(b.ts-a.ts) from loads a join loads b on b.rowid=a.rowid+1 where a.site='paddypower' and b.site='paddypower' and a.ts > strftime('%s','now')-3600"`
Expected: at least 2.0.
- [ ] **Step 9: The Console.** Open http://127.0.0.1:8090, Scraper tab.
Expected: a `pp-saves` tile with its schedule words, state, the three figures and its newest log lines; "all lines" shows the pass.

---

## Self-review

- **Spec coverage.**
  - M1 (the map, its contents, what feeds it, the project leagues and the three not seen, pruning, `python -m scraper map`, no Console page): Task 3.
  - M2 (`pp_saves`, its filters, its row, matching runners to sides): Task 2.
  - M3 (the `rotate` block, one page, home/list/match/back, the guard and pacer on every move, waiting when the budget is spent, the hourly re-check, missing links logged, the pass line, closing off-days): Task 4. Paddy's limits: Task 1.
  - M4 (`PRICE_ROUTES`, the schema row shape, `PROTOCOL.md`, the referee tests): Task 2.
  - M5 (the store, 36 h, replaced per match, the fair's `team`, the join, the OddsPapi path removed, the read route): Tasks 5–6.
  - Live proof: Task 7.
  - Not built, as the spec says: a per-goalkeeper fair from Bookie Bashing, and a Console map page.
- **Step scan.**
  - Every fixture step names its capture file and what it keeps.
  - Every code step names its signatures and the spec's values (route, row, limits, breaks, the 1 h re-check, 48 h, 45 s, 36 h).
  - The state machine is given as a table, because its tests alone do not fix the order of its checks.
- **Type consistency.** These names are the same in every task that uses them:
  - Scraper: `Move` kinds, `Rotation.next_move(page_url, now)` / `moved(move, ok, now, rows=, saves=)`, `PaddyMap.to_visit(cid, now, horizon_s)`, `link_for(cid) -> {"href", "text"}`, `pp_saves.read(capture, event_id, since)`, `LIST_FEED` / `EVENT_FEED`, `Runner._handle_rows(t, url, rows, now)`.
  - Render: `PaddySaves(event_id, home, away, kickoff, team, line, over)`, `paddy_prices()`, `saves_value(events, min_edge, fairs=None, paddy=None)`, `market_key` `saves_home`/`saves_away`.
- **Review Focus.**
  - Line 1: Tasks 1 and 4 (spent budget, break).
  - Line 2: Task 5 (126.0).
  - Lines 3–4: Task 2.
  - Line 5: Task 5.
- **Proportion.** The code blocks are tests and the two fixture cutters. The bodies of the map, the machine, the runner's moves and the Render join are the builder's.
