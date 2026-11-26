# Check on demand (WOPs first) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Callum says "check wops for the weekend" to Claude Code and gets back every WhatOddsPaddy bet that Paddy prices above Bookie Bashing's fair, with all comparisons saved to his Private tracker.

**Architecture:** The running Scraper service gains a request path (a JSON file in, a result file out) with two kinds: read Paddy match pages, and price presets on the open Bookie Bashing builder. A `check` command chains them (screen, then ladder on value games) from a small recipe. A Claude Code skill runs the command and reports.

**Tech Stack:** Python, Playwright (the Scraper's one Chrome), pytest; stdlib only for new modules.

**Spec:** `docs/superpowers/specs/2026-11-26-check-on-demand-design.md`

## Global Constraints

- Every page load is the Scraper service's. The CLI, the `check` runner and the skill load nothing. `hub/` and `render/` are not touched.
- No test loads a Paddy Power or Bookie Bashing page. Browser tests use `page.set_content` fakes (as `scraper/tests/test_bb_saves.py`) or the fake localhost site (`tests/test_e2e_fakesite.py`).
- Parsers are written only from real saved pages or payloads (`/opt/puntmore-scraper/data/dumps/`, `data/samples/`). A fixture is trimmed from one and never carries a request URL from a capture's `index.jsonl`.
- On a Paddy page nothing is clicked but the cookie banner. `look` stays refused for Paddy.
- A challenge or cool-off stops a request at once; nothing answers a challenge. No stealth, proxy, solver or user-agent work.
- A request skips Paddy's `per_hour`, `per_day` and quiet hours only. It keeps `load_gap_s` and the pacer. Bookie Bashing's limits are unchanged.
- The tracker Save is Private only, and dry (fill, do not press) unless the request says `"save": "press"`. The check command defaults to dry.
- `targets.toml` changes only through `python -m scraper targets ...`. Budgets in `behaviour.py` are not edited.
- The register (`docs/decisions.md`) gets Callum's decisions of 26 Nov 2026 as dated lines in the same PR as the code they allow (Tasks 1 and 4). `scraper/AGENTS.md` and `scraper/README.md` change with the code.
- Run the suite from the repo root: `PYTHONPATH=. SCRAPER_CHROME_PATH=/usr/bin/google-chrome ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`.

## Review Focus

- A request arrives while the service is in a cool-off for that site: the result says so and no page is loaded.
- A request file that is malformed, names an unknown job or parser, or asks for a host outside `paddypower.com`: refused with a reason in the result, the service keeps running.
- The service restarts mid-request: the request is not silently lost or run twice from the start; the result says it was interrupted and lists what was done.
- A Paddy WOP name that matches no preset, or a preset Bookie Bashing cannot price for that game (Total Odds never settles): reported as unmatched or unpriced, never given a guessed fair.
- Two requests at once (a second `check` started while one runs): the second is refused, not interleaved on the same builder page.

---

### Task 1: The request path, and `read` for Paddy

**Files:**
- Create: `scraper/ask.py`, `scraper/tests/test_ask.py`
- Modify: `scraper/runner.py` (`_requests`, a load path for requested loads), `scraper/guard.py`, `scraper/status.py`, `scraper/__main__.py`, `scraper/AGENTS.md`, `scraper/README.md`, `docs/decisions.md`
- Test: `scraper/tests/test_ask.py`, `scraper/tests/test_guard.py`, `scraper/tests/test_e2e_fakesite.py`

**Interfaces:**
- Produces: request file `data/requests/ask-<id>.json`:
  `{"id": str, "kind": "read", "job": "<a paddypower matches job>", "from": iso, "to": iso, "urls": [..] (instead of job/from/to), "parser": "<name>" | null, "keep": bool}`
- Produces: result file `data/requests/done/ask-<id>.json`:
  `{"id", "kind", "state": "done"|"refused"|"stopped"|"interrupted", "reason": str|null, "started", "finished", "items": [{"url", "home", "away", "kickoff", "state": "read"|"failed"|"skipped", "reason", "rows": [...], "saved": "<dump dir>"}]}`. While running, the same file exists with `"state": "running"` and the items done so far.
- Produces: `ask.parse(raw: dict) -> Request` (raises `ask.Refused(reason)`), `ask.write_result(data_dir, result)`, `ask.pending(data_dir) -> Request | None` (oldest first; a second request while one runs is refused with reason "another request is running").
- Produces: `Guard.record_load(site, url, now, requested=False)` and `Guard.spent(site, now) -> ...` counting only `requested=False`; `Guard.requested(site, now) -> (hour, day)`.
- Produces: CLI `python -m scraper ask '<json>' [--wait SECONDS] [--json]`: writes the request, polls the result, prints it; exit 0 only on `done`.

- [ ] **Step 1: Failing tests for `ask.parse`.** Cases: a good `read` by job and range; by `urls`; unknown kind; unknown parser; a URL whose host is not `paddypower.com` (use `config`'s host rule, do not copy it); more than 60 URLs; `from` after `to`. Assert the `Refused` reason text for each.
- [ ] **Step 2: Implement `ask.py`** (parse, pending, write_result with the same atomic tmp-then-rename as `sinks._drop`). Run the tests.
- [ ] **Step 3: Failing guard tests.** A requested load is stored, does not change `spent` or `can_load`, and shows in `requested`. The `loads` table gains the column by an in-place migration that keeps existing rows.
- [ ] **Step 4: Implement the guard change and a `status` line** `paddypower  requested: N this hour, M today` (only when non-zero).
- [ ] **Step 5: Failing runner test on the fake site.** A `read` request for two URLs: each is loaded in its own tab, one after the other with the site gap between them, the tab is closed, the result file goes `running` then `done` with two items, each `saved` to a dump directory (HTML, screenshot, and the captured payloads as `payloads.json`). With the site's budget already spent the request still loads; with the site in cool-off the result is `stopped` and nothing loads; a logged challenge on the first URL stops the request with the second `skipped`.
- [ ] **Step 6: Implement in the runner.** One request item per tick so the sweep keeps running; requested loads call the pacer but not `guard.can_load`'s budget and quiet-hours checks; cool-off, back-off and `host_allowed` still apply. For `job` + range, the URLs are that job's listing links whose kick-off (from `pp_listing.fixtures` on the listing tabs already open) falls in the range; no listing is loaded for a request. On service start, a `running` result is rewritten `interrupted`.
- [ ] **Step 7: CLI `ask`, docs and the register.** Register lines dated 26 Nov 2026, Callum: a request disregards Paddy's hourly and daily budget and quiet hours, keeps the gap between loads and stops on a cool-off; a request may read games outside the 48-hour window. Amend `scraper/AGENTS.md`'s "never add a second budget anywhere" to name this one exception.
- [ ] **Step 8: Whole suite green, commit.**

### Task 2: The Paddy WOP reader

**Files:**
- Create: `scraper/parsers/pp_wop.py`, `scraper/tests/test_pp_wop.py`, `scraper/tests/fixtures/pp_wop/`
- Modify: `scraper/parsers/__init__.py` (register `pp_wop`)

**Interfaces:**
- Consumes: the parser contract `fn(page, capture) -> list[dict]`; Task 1's saved `payloads.json`.
- Produces rows: `{"home", "away", "kickoff", "band": "Featured"|"Up to 9/1"|..., "name": "<the WOP as Paddy words it>", "price": float}`; only OPEN markets and runners; price read as `pp_most` reads it; one row per distinct name (Featured duplicates a band entry: keep one, prefer the band).

- [ ] **Step 1: Get real payloads.** First look in `/opt/puntmore-scraper/data/samples/` and `dumps/pp-wop-look-20261112-130751/` for a payload carrying `#WhatOddsPaddy` markets. If only HTML exists, run one Task 1 `read` (with `"parser": null, "keep": true`) on a game kicking off within a day and use what it saves. STOP and report if neither gives a payload: do not write the parser from a guess.
- [ ] **Step 2: Trim a fixture** (two games if available, one with no WOP markets) into `fixtures/pp_wop/`, no request URLs.
- [ ] **Step 3: Failing tests:** the rows for the fixture game (exact names and prices copied from the fixture), no rows for the game without WOPs, a suspended market gives no rows, a Featured duplicate appears once.
- [ ] **Step 4: Implement, suite green, commit.**

### Task 3: Pricing presets on the builder (`price`, read only)

**Files:**
- Create: `scraper/parsers/bb_presets.py`, `scraper/tests/test_bb_presets.py`
- Modify: `scraper/ask.py`, `scraper/runner.py`, `scraper/tests/test_bb_saves.py` (extend the `FAKE` builder with the presets controls, copied from a real dump)

**Interfaces:**
- Consumes: `bb_saves.bring_up`, `_field_select`, `_options`, `_select_label`, `_total_odds`; the `bb-look` job's open builder page.
- Produces: request `{"id", "kind": "price", "job": "bb-look", "items": [{"key": str, "competition": str, "home": str, "away": str, "preset": str}], "save": "no"|"dry"|"press"}`.
- Produces result items: `{"key", "state": "priced"|"no_game"|"no_preset"|"unsettled", "fair": float|null, "saved": "no"|"dry"|"pressed"|"failed"}`.
- Produces: `bb_presets.presets(page) -> list[str]` (the Use Preset options, whitespace collapsed) and `bb_presets.price(page, competition, home, away, preset) -> float | None`.

- [ ] **Step 1: Learn the controls from the dumps** `bb-look-20261112-1342*` to `-134459` (presets open, a preset loaded). Extend `FAKE` to behave as they show: "Show/Hide Presets" appears once a game is selected; choosing a preset fills the builder and keeps the game; Total Odds settles after a delay.
- [ ] **Step 2: Failing tests:** `presets` lists the options; `price` returns Total Odds for a known game and preset; unknown game gives `no_game`; unknown preset `no_preset`; Total Odds that never settles gives `unsettled` within `bb.settle_s` rules; a second item on the same game does not reselect the game.
- [ ] **Step 3: Implement `bb_presets`, then the `price` kind in `ask.parse` and the runner** (at most `bb.call_budget_s` of work per tick; `save` other than `"no"` is refused with reason "save not built" until Task 4). The game is found by matching home and away against the Game(s) options with the same team-name normalising `bb_saves` uses.
- [ ] **Step 4: Suite green, commit.**
- [ ] **Step 5 (live, by the controller, not a subagent):** enable `bb-look` with `python -m scraper targets set`, send one `price` request for one real game and the preset `Home 2,6`, compare the fair with the builder by eye on a `dump`.

### Task 4: Save to the Private tracker

**Files:**
- Modify: `scraper/parsers/bb_presets.py`, `scraper/ask.py`, `scraper/runner.py`, `scraper/tests/test_bb_presets.py`, `scraper/tests/test_bb_saves.py` (`FAKE`), `docs/decisions.md`, `scraper/AGENTS.md`

**Interfaces:**
- Produces: `bb_presets.save(page, bet_name: str, odds: float, press: bool) -> "dry"|"pressed"|"failed"`; request items gain `"bet_name"` and `"odds"` (Paddy's price).

- [ ] **Step 1 (live, by the controller):** learn the Save panel with `python -m scraper look bb-look '[...]'` on a priced preset: its fields (Bet Name, Bookmaker, Odds), the visibility control and its options, the Save button. Save nothing. Keep the dump path for the builder.
- [ ] **Step 2: Extend `FAKE` from that dump. Failing tests:** dry fills Bet Name, Bookmaker "Paddy Power", Odds and sets Private, and the fake records no save; press records exactly one save, Private; if the visibility control cannot be set to Private the result is `failed` and Save is not pressed; a panel that is missing gives `failed`.
- [ ] **Step 3: Implement.** Private is verified by reading the control back before Save is pressed.
- [ ] **Step 4: Register line** dated 26 Nov 2026, Callum: the Scraper saves comparisons to Bookie Bashing's tracker, Private only, with the builder's Save button, and presses nothing else there that changes the account. Reword `scraper/AGENTS.md` where it says no code presses an account-changing control.
- [ ] **Step 5: Suite green, commit.**

### Task 5: Matching, the recipe and `check`

**Files:**
- Create: `scraper/checks/__init__.py`, `scraper/checks/match.py`, `scraper/checks/recipes.py`, `scraper/checks/run.py`, `scraper/tests/test_checks.py`
- Modify: `scraper/__main__.py`, `scraper/README.md`

**Interfaces:**
- Consumes: Task 1 `read` results with `pp_wop` rows; Task 3/4 `price` results; `bb_presets.presets`.
- Produces: `match.key(name: str, home: str, away: str) -> str` (teams replaced by `home`/`away`, case, spacing and punctuation normalised, `#WhatOddsPaddy`/`#WOP` prefixes dropped) and `match.pair(paddy_rows, presets) -> (pairs, unmatched)`.
- Produces: `recipes.RECIPES = {"wops": Recipe(parser="pp_wop", job="pp-epl-most", screen=[...four preset names: home and away of 1,5,2 and 2,6...], ladder_prefix="#WOP", threshold=100.0, save="private")}`. The exact screen preset names are copied from the 12 Nov dump list (Bookie Bashing's own `#WOP` presets for both sides where they exist, Callum's `Home 2,6`/`Away 2,6` otherwise) and each must pair with a Paddy name in the Task 2 fixture; if one does not, STOP and report.
- Produces: `run.check(name, start, end, save: "dry"|"press", ask=...) -> dict` and the file `data/checks/<name>-<YYYYmmdd-HHMMSS>.json`: `{"name", "from", "to", "save", "games": [{"home","away","kickoff","state": "checked"|"no_wops"|"failed"}], "comparisons": [{"home","away","kickoff","stage": "screen"|"ladder","wop","preset","paddy","fair","rating","saved"}], "unmatched": [...], "unpriced": [...]}`. `rating = paddy / fair * 100`, 2 places.
- Produces: CLI `python -m scraper check <name> --weekend | --from ISO --to ISO [--press] [--json]`. `--weekend` is the coming Friday 17:00 to Sunday 23:59 Europe/London (today's, if run on Friday to Sunday). Prints the comparisons above the threshold, best first, then the games with no WOPs and the unmatched and unpriced counts.

- [ ] **Step 1: Failing tests for `match`** using real Paddy names from the Task 2 fixture and real preset names from the 12 Nov dump: the 1,5,2 home and away pair correctly; a name with no preset is in `unmatched`; two presets that differ only by a tab or double space are one.
- [ ] **Step 2: Failing tests for `run.check`** with a fake `ask` (no service): screen prices four presets per game; a game with a screen rating above 100 gets the ladder (every paired `#WOP` preset not already priced), one at or below 100 does not; every comparison carries `saved`; a game with no WOP rows is `no_wops` and nothing is priced for it; a `stopped` read ends the check with what it has and says why; `--weekend` on a Wednesday, a Friday 16:00 and a Sunday.
- [ ] **Step 3: Implement, wire the CLI, suite green, commit.**

### Task 6: The skill, and a first run

**Files:**
- Create: `.claude/skills/check-weekend/SKILL.md`
- Modify: `CLAUDE.md` (one line under the tools), `docs/architecture.md`, `docs/dataflow.md`

- [ ] **Step 1: Write the skill.** Trigger: "check <name> for the weekend" (or a named day). It runs `SCRAPER_DATA_DIR=/opt/puntmore-scraper/data ~/.venvs/puntmore/bin/python -m scraper check <name> --weekend --json` in the background from the repo root, waits, and reports from the result file: the comparisons above the threshold as a table best first, then games with no WOPs, then unmatched and unpriced. It never loads a page itself and never passes `--press` unless Callum has said saves are live.
- [ ] **Step 2 (live, by the controller, Callum watching):** one full dry run for the coming weekend; compare three comparisons by hand with the builder; then, on Callum's word, one run with `--press` and a look at the Private tracker.
- [ ] **Step 3: Docs, commit.**

## Order and what each slice gives

1. Task 1 alone is useful: Claude Code can have the Scraper read any Paddy match page on request.
2. Tasks 2 and 3 need real pages; Task 2 is best done Friday or Saturday, when WOPs are up.
3. Tasks 4 to 6 finish the check. Saves stay dry until Callum has watched a run.
