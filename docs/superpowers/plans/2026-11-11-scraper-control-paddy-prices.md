# Scraper control routes Paddy's prices: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Scraper service stops posting Paddy's prices to Render. It writes them as drop files, and the Hub's inbox (Scraper control) posts them to the same two Render routes with the same bodies.

**Architecture:** A third drop-file envelope, `scraper_prices`, written by `scraper/sinks.py` into `<scraper data>/hub/prices/`. The Hub's inbox pass (`hub/puntmore_hub/inbox.py`, every 30 s in the daemon) posts each file's rows as `{"prices": rows}` to the file's `route` through the Hub's one Render client (`finder.post_json`), oldest first. A file is moved to `done/prices/` once Render has answered for it. A file older than 15 minutes is never posted.

**Tech Stack:** Python 3 (Hub: stdlib `urllib` client, pytest; Scraper: Playwright runner, pytest), JSON Schema 2020-12 (`hub/schemas/protocol.schema.json`).

**Spec:** this document, Design section; `docs/architecture.md` Open item 1.

**Render:** nothing under `render/` changes. The two handlers (`render/api/app/routers/saves_ingest.py`) receive the same JSON bodies on the same paths, through the same `require_ingest_key` guard (the `ingest` grant). The only difference is the header: the Hub sends its key as `Authorization: Bearer`, where the Scraper sent `X-API-Key`. Render's `presented_key` (`app/security.py`) reads both headers the same way.

## Decisions for Callum

Each one is built on the recommendation as given. None is written into `docs/decisions.md`; a dated edit there is Callum's to make.

1. **A third drop-file envelope, `scraper_prices`.** The register ("The Scraper's drop files are `hub/PROTOCOL.md` + `hub/schemas/`") names two envelopes, `scraper_tips` and `scraper_event`. *Recommendation: add `scraper_prices`.* Its own directory (`hub/prices/`) is what keeps an old inbox from misreading price rows as tips. Once this ships, two lines in the register are stale: that one, and "the Paddy price posts go direct today and move behind Scraper control as the next Scraper work" under "Names and structure".
2. **A price file older than 15 minutes is dropped, not posted.** Render stores no read time with a price; it keeps whatever arrives for 36 h as if it were current. Posting a backlog after an outage would therefore put stale Paddy prices on the board as fresh ones, which the direct post never did. *Recommendation: `PRICE_MAX_AGE_S = 900`,* the same as `pp_most`'s own `PRICE_FRESH_S`.
3. **`bb-saves` takes the same path, with no special case.** The sink chooses by the target's `post`, so `post = "/api/paddy/saves-fair"` now means "drop for the Hub, route `/api/paddy/saves-fair`". *Recommendation: yes.* The target entry, its schedule, its parser and the route stay as they are. Render gets the same body on the same route, and the register leaves no direct path to keep. The target is disabled in the box's `targets.toml` today (since 6 Nov 2026, session expired), so it sends nothing either way until Callum decides on it.
4. **The key.** The Hub posts with `PUNTMORE_RENDER_API_KEY` (Bearer), the key it already uses for Finder. The Scraper stops reading `SAVES_INGEST_KEY` and `PUNTMORE_URL`. *Recommendation: yes; one Render client and one key on the Hub.* If that key is an API client key (the Bet Placer's) rather than the static `SAVES_INGEST_KEY`, Render admits it on the same `ingest` grant and updates that client's `last_used_at`, as Finder posts already do. The values in `/opt/puntmore-scraper/.env` are then unused; removing them is Callum's call.

## Design

### What moves

| | Today | After |
|---|---|---|
| Who posts `POST /api/paddy/most-prices` and `/api/paddy/saves-fair` | the Scraper service (`RenderSink`, httpx, `X-API-Key: SAVES_INGEST_KEY`, `PUNTMORE_URL`) | Scraper control: the Hub's inbox (`inbox.post_prices`, urllib, `Authorization: Bearer PUNTMORE_RENDER_API_KEY`, `PUNTMORE_RENDER_HTTP`) |
| Body | `{"prices": rows}`, rows as the parser returned them | the same: the rows are written to the file and posted unchanged; nothing filters them |
| Timeout | 30 s | 30 s (`finder.TIMEOUT`) |
| When | at each parse (every 60 s per tab) | within one inbox pass (≤ 30 s) of the parse |
| Targets | `post = "/api/..."` | unchanged; `targets.toml` on the box needs no edit |

### The drop file

`type: "scraper_prices"`. It uses the same envelope as the other two types (`client_id: "scraper"`, a 32-hex `msg_id`, `ts`). It is written atomically to `<scraper data>/hub/prices/<ms>-<source>-<msg_id[:8]>.json`, one file per parse of one tab. The `msg_id` suffix is needed because every tab of a target is parsed with the same `now` in one runner tick. Without it, a match-listing target's tabs would overwrite each other's file.

| Body field | Value |
|---|---|
| `source` | the target's name (`pp-epl-most`, `bb-saves`) |
| `at` | unix seconds of the parse (the runner's `now`) |
| `url` | the target's url |
| `route` | the target's `post`: `"/api/paddy/most-prices"` or `"/api/paddy/saves-fair"` (the schema's enum; `PRICE_ROUTES` on both sides) |
| `rows` | at least one object; the parser's rows, unchanged. Render's handler validates them. |

### One inbox pass over `prices/`

The price pass runs after the tips and the events, so a slow Render never delays them. It holds a non-blocking `flock` on `<scraper data>/hub/prices/.lock`. If another pass holds the lock (`puntmore inbox` or the Console's "inbox now" running beside the daemon), this pass posts nothing and counts its files as waiting. Files are taken oldest first by name. Only `*.json` files are read; a `*.json.tmp` is half-written. For each file:

| The file | What happens | Count |
|---|---|---|
| not JSON, not an object, `at` not a number, `route` not a string, `rows` not a non-empty list of objects | moved to `done/prices/`, warning | `price_dropped` |
| `now - at > PRICE_MAX_AGE_S` (900) | moved to `done/prices/` unsent, warning | `price_dropped` |
| `route` not in this inbox's `PRICE_ROUTES` | left in place for an inbox that knows it (then dropped once stale), warning | `price_waiting` |
| Render answers 2xx | moved to `done/prices/`, info line with Render's answer | `price_files`, `price_rows` |
| 408, 429, any 5xx, a network error or timeout (`OSError`, `http.client.HTTPException`), `PUNTMORE_RENDER_HTTP` or the key blank | **the pass stops**: this file and every later one stay for the next pass, so an older file is never posted after a newer one. One warning. | `price_waiting` |
| any other 4xx (422 bad body, 401/403 key) | moved to `done/prices/`, warning with the code and 200 characters of the answer | `price_dropped` |

`done/prices/` keeps its newest 300 files (`KEEP_DONE`), apart from the tips' and events' `done/`. Retrying is the inbox's own rule: a file stays until it is handled, and the next 30 s pass handles it. The Hub's Render client keeps its rule too: it never retries in place. No backoff or retry loop is added.

### Failure and version-skew cases

| Case | What happens | Render's state compared with the direct post |
|---|---|---|
| Render down or 5xx for *t* minutes | Files wait. On recovery they are posted oldest first; any older than 15 min are dropped unsent. | Same values. Direct posts in the outage were lost too, and the next post carried the current prices. For an outage under 15 min the values are identical and only the 36 h TTL clock runs from the late post. |
| 4xx | the file is moved aside and logged | Same: the direct post's rows were lost with "send failed". |
| The Hub's inbox not running | Files collect in `prices/`. The Console's Daemon card shows the unit down and the prices waiting. When it restarts, stale files are dropped and fresh ones posted. | Render keeps its last prices until their TTL, as when the Scraper is down today. |
| A drop that fails its schema | moved aside and logged, like an unreadable tips file | Nothing posted, so no change. |
| The process dies between the 2xx and the move | The next pass posts the same body again. | `TTLCache.set` on the same keys with the same values: no change but the TTL clock. |
| **New Scraper, old inbox** | An old inbox reads only `tips/` and `events/`, so price files wait untouched; none can be misread as tips. The new inbox posts them (≤ 15 min old). `puntmore update` restarts `puntmore-link` before `puntmore-scraper` in the same tick, so in practice this window is only open when that restart fails. | Prices pause, then resume. Nothing is doubled. |
| **Old Scraper, new inbox** (the Docker fallback image lagging `main`, or the Scraper not yet restarted) | The Scraper posts direct as today; `prices/` stays empty and the inbox posts nothing. | Unchanged. |
| Switch-over | Each parse goes one way only: the old process's last direct post comes before it stops, and the new process's first drop after it starts. | Nothing doubled. |
| A newer Scraper writes a route this inbox does not know | the file waits (warning); dropped once stale | Nothing posted. |

## Global Constraints

- Work only in `/home/callum/Projects/puntmore-s1`, branch `scraper-control-paddy`. Never switch branches, never touch `/home/callum/Projects/puntmore`, never push, never use `git stash`.
- Nothing under `render/` changes. `docs/decisions.md` is not edited.
- Routes exactly `"/api/paddy/saves-fair"` and `"/api/paddy/most-prices"`. Body exactly `{"prices": rows}` with `rows` as the parser returned them. Timeout 30 s. Key `PUNTMORE_RENDER_API_KEY` as Bearer to `PUNTMORE_RENDER_HTTP`.
- `PRICE_MAX_AGE_S = 900`. Price drops in `<scraper data>/hub/prices/`, handled ones in `<scraper data>/hub/done/prices/`.
- The `bb-saves` target block in `scraper/targets.example.toml`, its parser `scraper/parsers/bb_saves.py` and its schedule are not changed.
- Names are the register's: Callum, Hub, Render, Scraper service, Scraper control, HubAI; a **price** is a raw row for the Paddy board.
- Secrets live in `.env` files only. Before the first commit, if `git config core.hooksPath` does not print `.githooks`, run `git config core.hooksPath .githooks` (gitleaks pre-commit).
- Hub tests: `cd /home/callum/Projects/puntmore-s1/hub && ~/.venvs/puntmore/bin/python -m pytest -q` (bare `pytest` imports the wrong checkout). Baseline at `4b08626`: **130 passed, 1 warning**.
- Scraper tests: `cd /home/callum/Projects/puntmore-s1 && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q` (from the repo root, as `scraper/README.md` and CI do). Baseline at `4b08626`: **101 passed, 8 failed, 2 errors**. All 10 failures are Playwright's missing `chromium_headless_shell-1187` on this box: 8 in `test_e2e_fakesite.py` and 2 errors in `test_bb_saves.py`. "Green" for the Scraper means no failure outside those 10. CI installs the browser and runs them.
- Every commit message ends with exactly these two lines:
  ```
  Co-Authored-By: Claude <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
  ```

## Review Focus

1. Several tabs of one target parsed in the same tick (same `now`) must give separate files, none overwritten. Test in Task 2.
2. While Render hangs or is down, tips and events must still be handled in the same pass, with at most one 30 s timeout per pass. Test in Task 3.
3. A half-written drop (`*.json.tmp`) in `prices/` must never be read or moved. Test in Task 3.
4. The new Scraper must never put a price row where an old inbox would read it as a tip. Test in Task 2.
5. Two inbox passes at once (the daemon plus `puntmore inbox` or the Console's "inbox now") must not both post: only one does. Test in Task 3.

---

### Task 1: The `scraper_prices` envelope

**Files:**
- Modify: `hub/schemas/protocol.schema.json`
- Create: `hub/schemas/examples/scraper_prices.json`
- Modify: `hub/puntmore_hub/schemas.py` (`FILE_TYPES` and its comment, module docstring)
- Test: `hub/tests/test_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `puntmore_hub.schemas.FILE_TYPES == ("scraper_tips", "scraper_event", "scraper_prices")`.
  - The schema's `type` enum gains `"scraper_prices"`, with an `allOf` branch whose body `$ref` is `#/$defs/scraper_prices`.
  - `$defs/scraper_prices`: required `source` (string, minLength 1), `at` (number), `url` (string, minLength 1), `route` (`{"enum": ["/api/paddy/saves-fair", "/api/paddy/most-prices"]}`), `rows` (array, `minItems: 1`, items `{"type": "object"}`); `additionalProperties: false`. Its description says the rows pass through to Render unchanged and Render's handler is their validator.
  - Tasks 2 and 3 read the route enum at `SCHEMA["$defs"]["scraper_prices"]["properties"]["route"]["enum"]`.

- [ ] **Step 1: Write the failing tests.** Replace `test_the_schema_knows_the_two_drop_files_and_nothing_else` and add two tests:

```python
def test_the_schema_knows_the_three_drop_files_and_nothing_else():
    assert set(FILE_TYPES) == {"scraper_tips", "scraper_event", "scraper_prices"}
    assert set(SCHEMA["properties"]["type"]["enum"]) == set(FILE_TYPES)
    assert {p.stem for p in EXAMPLES} == set(FILE_TYPES)


MOST_ROW = {"home": "Chelsea", "away": "Man Utd", "kickoff": "2026-11-08T16:30:00Z", "market": "most_corners",
            "home_price": 1.5333, "tie_price": 9.0, "away_price": 2.375}


def _prices(**body):
    base = {"source": "pp-epl-most", "at": 1793957400.0,
            "url": "https://www.paddypower.com/football/english-premier-league",
            "route": "/api/paddy/most-prices", "rows": [MOST_ROW]}
    return {"type": "scraper_prices", "client_id": "scraper", "msg_id": "c0ffee0000000012",
            "ts": "2026-11-06T09:30:00+00:00", "body": {**base, **body}}


def test_a_prices_drop_names_one_of_the_two_paddy_routes():
    validator.validate(_prices())
    validator.validate(_prices(route="/api/paddy/saves-fair"))
    for bad in ("/api/finder/rows", "/api/paddy/feed", "hub:tips", ""):
        with pytest.raises(ValidationError):
            validator.validate(_prices(route=bad))


def test_a_prices_drop_has_rows_and_passes_them_through():
    with pytest.raises(ValidationError):
        validator.validate(_prices(rows=[]))
    validator.validate(_prices(rows=[{"anything": "Render's handler validates rows"}]))
    with pytest.raises(ValidationError):
        validator.validate(_prices(trust_weight=0.5))        # a tips field: additionalProperties is false
```

The example file `hub/schemas/examples/scraper_prices.json` is exactly `_prices()` above (msg_id `c0ffee0000000012`). `test_example_validates` picks it up through `EXAMPLES`.

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-s1/hub && ~/.venvs/puntmore/bin/python -m pytest tests/test_protocol.py -q`
Expected: FAIL. `FILE_TYPES` has two types, and `"scraper_prices"` is not in the enum.

- [ ] **Step 3: Add the type to the schema, the example and `FILE_TYPES`** (Interfaces above). Update the schema's `title`/`description` from "type scraper_tips or scraper_event" to name all three. Update the docstring in `schemas.py` from "the two envelopes" to "the three envelopes".

- [ ] **Step 4: Run the whole Hub suite.**
Run: `cd /home/callum/Projects/puntmore-s1/hub && ~/.venvs/puntmore/bin/python -m pytest -q`
Expected: all pass (130 + 2 new).

- [ ] **Step 5: Commit**

```bash
cd /home/callum/Projects/puntmore-s1
git add hub/schemas/protocol.schema.json hub/schemas/examples/scraper_prices.json hub/puntmore_hub/schemas.py hub/tests/test_protocol.py
git commit -F - <<'EOF'
Protocol: a scraper_prices drop file for Paddy's prices

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 2: The Scraper drops its prices for the Hub and posts nothing

**Files:**
- Modify: `scraper/sinks.py` (delete `RenderSink`; add `PRICE_ROUTES`, `deliver_prices`, `PriceSink`; `Sinks` makes `hub/prices/`; module docstring)
- Modify: `scraper/config.py` (`_target` validates `post`; remove `Settings.puntmore_url`, `Settings.ingest_key` and their two lines in `load()`)
- Modify: `scraper/.env.example` (remove `PUNTMORE_URL`, `SAVES_INGEST_KEY` and their comments)
- Modify: `scraper/targets.example.toml` (only the two header comment lines on `post`)
- Modify: `scraper/runner.py:29` (docstring: "sinks.py delivers its rows (drop files for the Hub)")
- Modify: `scraper/parsers/__init__.py` (docstring: rows are handed to the Hub as a `scraper_prices` drop for the target's `post` route, which the Hub posts as `{"prices": rows}`; a parser still emits exactly the shape that endpoint takes)
- Modify: `scraper/tests/test_e2e_fakesite.py:109-110,297-298` and `scraper/tests/test_pacing.py:53-54` (drop the `puntmore_url=` and `ingest_key=` arguments)
- Test: `scraper/tests/test_sinks_schema.py`

**Interfaces:**
- Consumes: Task 1's schema at `hub/schemas/protocol.schema.json` (`$defs/scraper_prices`, route enum).
- Produces:
  - `scraper.sinks.PRICE_ROUTES = ("/api/paddy/saves-fair", "/api/paddy/most-prices")`.
  - `scraper.sinks.deliver_prices(data_dir: pathlib.Path, target: Target, rows: list[dict], now: float) -> bool`. It writes one `scraper_prices` envelope to `data_dir/hub/prices/<int(now*1000)>-<_safe(target.name)>-<msg_id[:8]>.json` (temp file + rename) with body `{"source": target.name, "at": float(now), "url": target.url, "route": target.post, "rows": rows}`.
    - Rows are not filtered or changed.
    - It returns False and writes nothing when `rows` is empty, or when `target.post` is not in `PRICE_ROUTES`. In the second case it prints `f"{target.name}: no Hub route for post {target.post!r}; nothing dropped"`.
    - On success it prints `f"{target.name}: {len(rows)} price row(s) handed to the Hub ({target.post})"`. The live proof greps for this text.
  - `Sinks(settings)` needs only `settings.data_dir`. `Sinks.deliver(t, rows, now) -> bool`: `hub:` targets go to `deliver_to_hub` as today; any other `post` goes to `deliver_prices`.
  - `config._target` raises `ValueError(f"target {name}: post must be \"hub:<channel>\" or one of {', '.join(PRICE_ROUTES)}, not {post!r}")` for any `post` that is not None, does not start with `"hub:"`, and is not in `PRICE_ROUTES`. Import `PRICE_ROUTES` inside the function, as `_target` already does with `SITES`.
  - `config.Settings` has no `puntmore_url` and no `ingest_key`.

- [ ] **Step 1: Write the failing tests** in `scraper/tests/test_sinks_schema.py`. New imports: `import pytest`, `from types import SimpleNamespace`, and `from scraper import sinks` plus `from scraper.sinks import PRICE_ROUTES, Sinks, deliver_prices`. Update its module docstring to name three types.

```python
MOST = [{"home": "Chelsea", "away": "Man Utd", "kickoff": "2026-11-08T16:30:00Z", "market": "most_corners",
         "home_price": 1.5333, "tie_price": 9.0, "away_price": 2.375}]


def _price_target(post="/api/paddy/most-prices"):
    return config._target({"name": "pp-epl-most", "site": "paddypower",
                           "url": "https://www.paddypower.com/football/english-premier-league",
                           "parser": "pp_most", "post": post})


def test_a_price_target_drops_its_rows_for_the_hub_and_sends_nothing(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("the Scraper must not post to Render"))
    t, now = _price_target(), time.time()
    assert Sinks(SimpleNamespace(data_dir=tmp_path)).deliver(t, MOST, now)
    env = _only(tmp_path / "hub" / "prices")
    SCHEMA.validate(env)
    assert env["type"] == "scraper_prices" and env["client_id"] == "scraper" and len(env["msg_id"]) == 32
    assert env["body"] == {"source": "pp-epl-most", "at": now, "url": t.url,
                           "route": "/api/paddy/most-prices", "rows": MOST}
    assert not list((tmp_path / "hub" / "tips").glob("*"))     # never where an old inbox reads tips


def test_bb_saves_rows_take_the_same_path_unchanged(tmp_path):
    t = config._target({"name": "bb-saves", "site": "bookiebashing",
                        "url": "https://www.bookiebashing.net/tools/betbuilder/", "every_min": 10,
                        "parser": "bb_saves", "post": "/api/paddy/saves-fair"})
    rows = [{"home": "Arsenal", "away": "Spurs", "kickoff": "2026-11-08T16:30:00Z", "line": 6.5, "over": 1.952}]
    assert deliver_prices(tmp_path, t, rows, time.time())
    env = _only(tmp_path / "hub" / "prices")
    SCHEMA.validate(env)
    assert env["body"]["route"] == "/api/paddy/saves-fair" and env["body"]["rows"] == rows


def test_two_tabs_of_one_target_in_one_tick_make_two_files(tmp_path):
    t, now = _price_target(), time.time()
    other = [{**MOST[0], "home": "Arsenal", "away": "Spurs"}]
    assert deliver_prices(tmp_path, t, MOST, now) and deliver_prices(tmp_path, t, other, now)
    files = sorted((tmp_path / "hub" / "prices").glob("*.json"))
    assert len(files) == 2 and all(f.name.startswith(f"{int(now * 1000)}-pp-epl-most-") for f in files)
    assert sorted(json.loads(f.read_text())["body"]["rows"][0]["home"] for f in files) == ["Arsenal", "Chelsea"]


def test_price_rows_are_not_filtered_render_validates_them(tmp_path):
    odd = [{**MOST[0], "home_price": 500.0}]          # Render's model refuses it; today's body carried it
    assert deliver_prices(tmp_path, _price_target(), odd, time.time())
    assert _only(tmp_path / "hub" / "prices")["body"]["rows"] == odd


def test_no_rows_or_no_route_drops_nothing(tmp_path):
    assert not deliver_prices(tmp_path, _price_target(), [], time.time())
    unknown = SimpleNamespace(name="x", url="https://www.paddypower.com/", post="/api/paddy/feed")
    assert not deliver_prices(tmp_path, unknown, MOST, time.time())
    assert not list((tmp_path / "hub" / "prices").glob("*.json")) if (tmp_path / "hub" / "prices").exists() else True


def test_the_scrapers_routes_are_the_schemas():
    assert set(PRICE_ROUTES) == set(SCHEMA.schema["$defs"]["scraper_prices"]["properties"]["route"]["enum"])


def test_a_post_with_no_hub_route_is_refused_when_targets_load():
    for post in ("/api/paddy/feed", "/api/finder/rows", "tips"):
        with pytest.raises(ValueError, match="post must be"):
            _price_target(post)
    assert _price_target("/api/paddy/saves-fair").post == "/api/paddy/saves-fair"
    assert _price_target("hub:tips").post == "hub:tips"


def test_the_scraper_holds_no_render_url_or_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCRAPER_TARGETS", raising=False)
    monkeypatch.setenv("SAVES_INGEST_KEY", "k")
    s = config.load()
    assert not hasattr(s, "ingest_key") and not hasattr(s, "puntmore_url")
    assert not hasattr(sinks, "RenderSink")
```

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-s1 && ~/.venvs/puntmore/bin/python -m pytest scraper/tests/test_sinks_schema.py -q`
Expected: collection error, `ImportError: cannot import name 'PRICE_ROUTES'`.

- [ ] **Step 3: Implement** `PRICE_ROUTES`, `deliver_prices`, `PriceSink` (its `deliver` calls `deliver_prices(self.data_dir, t, rows, now)`), the `Sinks` changes and the `_target` check, as given in Interfaces. Delete `RenderSink`. Remove the two `Settings` fields and their `load()` lines. To get the `msg_id` suffix into the file name, give `_drop` a keyword `unique: bool = False`: when it is true, the final name is `<ms>-<safe name>-<msg_id[:8]>.json`; tips and events keep their names. Update the sinks module docstring: `"/api/..."` is now "one `scraper_prices` drop file per parse into `data/hub/prices/`, which the Hub's inbox posts to Render as `{"prices": rows}` on its key". Then make the `.env.example`, `targets.example.toml` comment (`post   the Render route the Hub posts the parsed rows to (a scraper_prices drop file), or "hub:tips"`), runner docstring, parsers docstring and test-constructor edits listed under Files.

- [ ] **Step 4: Run the Scraper suite.**
Run: `cd /home/callum/Projects/puntmore-s1 && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`
Expected: the new tests pass. The only failures are the 10 browser-missing ones from the baseline.
Also run: `grep -rn "ingest_key\|puntmore_url\|RenderSink\|SAVES_INGEST_KEY\|PUNTMORE_URL" scraper/`
Expected: no output except `scraper/tests/test_targets_file.py` (it sets the old variables to show they change nothing) and the new test above.

- [ ] **Step 5: Commit**

```bash
cd /home/callum/Projects/puntmore-s1
git add scraper/sinks.py scraper/config.py scraper/.env.example scraper/targets.example.toml scraper/runner.py scraper/parsers/__init__.py scraper/tests/test_sinks_schema.py scraper/tests/test_e2e_fakesite.py scraper/tests/test_pacing.py
git commit -F - <<'EOF'
Scraper: Paddy's prices go to the Hub as drop files, not to Render

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 3: The Hub's inbox posts the price drops to Render

**Files:**
- Modify: `hub/puntmore_hub/finder.py` (rename `_post` to public `post_json`; its two callers; module docstring: "the Hub's one Render client, used by Finder and by the inbox's price pass")
- Modify: `hub/puntmore_hub/inbox.py` (`PRICE_ROUTES`, `PRICE_MAX_AGE_S`, `PRICE_COUNT_KEYS`, `post_prices`, `ingest(..., opener=None)`, module docstring)
- Test: `hub/tests/test_inbox.py`

**Interfaces:**
- Consumes:
  - Task 1's `FILE_TYPES` (with `scraper_prices`), so `inbox._body` unwraps price envelopes too.
  - The schema's `$defs/scraper_prices`.
  - The file layout Task 2 writes: `<inbox_dir>/prices/<ms>-<source>-<8hex>.json`.
- Produces:
  - `finder.post_json(cfg: Settings, path: str, body: dict, opener: Opener | None = None) -> tuple[int, str]`. This is today's `_post` unchanged: Bearer `cfg.render_api_key` to `cfg.render_http + path`, `TIMEOUT` 30. It raises `RuntimeError` naming `PUNTMORE_RENDER_HTTP` or `PUNTMORE_RENDER_API_KEY` when either is blank. Network errors propagate. `finder._open` keeps its name, because `tests/test_finder.py` patches it.
  - `inbox.PRICE_ROUTES = ("/api/paddy/saves-fair", "/api/paddy/most-prices")`, `inbox.PRICE_MAX_AGE_S = 900`, `inbox.PRICE_COUNT_KEYS = ("price_files", "price_rows", "price_dropped", "price_waiting")`.
  - `inbox.post_prices(cfg: Settings, *, opener: finder.Opener | None = None) -> dict[str, int]`. One pass over `cfg.inbox_dir / "prices"` exactly as the Design section's "One inbox pass over `prices/`" table says, returning the four counts. Handled files go to `cfg.inbox_dir / "done" / "prices"` through `_finish`. The lock is `fcntl.flock(fh, LOCK_EX | LOCK_NB)` on `prices/.lock`; when the lock is busy, every `*.json` there counts as `price_waiting`. A missing `prices/` gives all four counts 0.
  - `inbox.ingest(store, cfg=default_settings, *, notify=True, opener=None) -> dict` keeps today's keys and adds the four from `post_prices(cfg, opener=opener)`, run after the tips and events.
  - Log lines (logger `puntmore.inbox`). Task 6 greps for these words:
    - info `"prices %s -> %s: sent %d row(s), Render answered %s"` (source, route, rows, answer[:200])
    - warning `"prices %s: %s; %d file(s) wait for the next pass"` (source, reason such as `HTTP 503` or the exception, count)
    - warning `"prices %s -> %s: refused HTTP %d %s; moved aside"`
    - warning `"prices %s: %ds old, past the %ds cut-off; moved aside unsent"`
    - warning `"prices %s: route %r unknown to this inbox; left for one that knows it"`
    - warning `"prices: unreadable %s (%s); moved aside"`

- [ ] **Step 1: Write the failing tests** in `hub/tests/test_inbox.py`. New imports: `fcntl`, `http.client`, `urllib.error`, `pytest`. Add `"prices": "scraper_prices"` to `TYPE_OF`. In `test_tips_are_stored_once_and_not_queued`, change the exact `counts ==` assertion to also expect `"price_files": 0, "price_rows": 0, "price_dropped": 0, "price_waiting": 0`. Shared helpers:

```python
MOST_ROW = {"home": "A", "away": "B", "kickoff": "2026-11-08T16:30:00Z", "market": "most_corners",
            "home_price": 1.5333, "tie_price": 9.0, "away_price": 2.375}
SAVES_ROW = {"home": "Arsenal", "away": "Spurs", "kickoff": "2026-11-08T16:30:00Z", "line": 6.5, "over": 1.952}


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body if isinstance(body, str) else json.dumps(body)

    def read(self):
        return self._body.encode()


class _Render:
    """Stands in for Render: answers each post with the next code (200 when none are left)."""
    def __init__(self, *codes):
        self.codes, self.sent = list(codes), []

    def __call__(self, req, timeout):
        self.sent.append((req.full_url, req.get_header("Authorization"), json.loads(req.data)))
        code = self.codes.pop(0) if self.codes else 200
        return _Resp(code, {"stored": 1, "rejected": []} if code == 200 else "nope")


@pytest.fixture
def pcfg(cfg):
    return cfg.with_overrides(render_http="https://puntmore.example/", render_api_key="k-ingest")


def _price_drop(cfg, at, *, route="/api/paddy/most-prices", rows=None, check=True):
    body = {"source": "pp-epl-most", "at": at, "url": "https://www.paddypower.com/x", "route": route,
            "rows": rows if rows is not None else [MOST_ROW]}
    env = {"type": "scraper_prices", "client_id": "scraper", "msg_id": uuid.uuid4().hex, "ts": utcnow_iso(), "body": body}
    if check:
        SCHEMA.validate(env)
    d = cfg.inbox_dir / "prices"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{int(at * 1000)}-pp-epl-most-{env['msg_id'][:8]}.json"
    path.write_text(json.dumps(env))
    return path


def _left(cfg):
    return sorted(p.name for p in (cfg.inbox_dir / "prices").glob("*.json"))


def _done(cfg):
    return sorted((cfg.inbox_dir / "done" / "prices").glob("*.json"))
```

The tests:

```python
def test_a_price_drop_is_posted_to_its_route_with_todays_body_and_moved_aside(pcfg, store):
    _price_drop(pcfg, time.time())
    render = _Render(200)
    counts = inbox.ingest(store, pcfg, notify=False, opener=render)
    assert render.sent == [("https://puntmore.example/api/paddy/most-prices", "Bearer k-ingest", {"prices": [MOST_ROW]})]
    assert {k: counts[k] for k in inbox.PRICE_COUNT_KEYS} == {"price_files": 1, "price_rows": 1, "price_dropped": 0, "price_waiting": 0}
    assert _left(pcfg) == [] and len(_done(pcfg)) == 1
    assert not list((pcfg.inbox_dir / "done").glob("*.json"))      # the tips' done/ is not flooded


def test_bb_saves_rows_go_to_the_saves_route(pcfg, store):
    _price_drop(pcfg, time.time(), route="/api/paddy/saves-fair", rows=[SAVES_ROW])
    render = _Render(200)
    inbox.ingest(store, pcfg, notify=False, opener=render)
    assert render.sent[0][0].endswith("/api/paddy/saves-fair") and render.sent[0][2] == {"prices": [SAVES_ROW]}


def test_files_are_posted_oldest_first(pcfg, store):
    now = time.time()
    for i, home in enumerate(["first", "second", "third"]):
        _price_drop(pcfg, now - 120 + 60 * i, rows=[{**MOST_ROW, "home": home}])
    render = _Render()
    inbox.ingest(store, pcfg, notify=False, opener=render)
    assert [s[2]["prices"][0]["home"] for s in render.sent] == ["first", "second", "third"]


@pytest.mark.parametrize("failure", [500, 502, 503, 408, 429, urllib.error.URLError("refused"),
                                     TimeoutError("timed out"), http.client.RemoteDisconnected("closed")])
def test_render_down_leaves_the_files_and_stops_the_pass_so_order_is_kept(pcfg, store, failure):
    now = time.time()
    _price_drop(pcfg, now - 60, rows=[{**MOST_ROW, "home": "older"}])
    _price_drop(pcfg, now, rows=[{**MOST_ROW, "home": "newer"}])

    def down(req, timeout):
        down.calls += 1
        if isinstance(failure, int):
            return _Resp(failure, "down")
        raise failure
    down.calls = 0
    counts = inbox.ingest(store, pcfg, notify=False, opener=down)
    assert down.calls == 1 and counts["price_waiting"] == 2 and counts["price_files"] == 0
    assert len(_left(pcfg)) == 2 and _done(pcfg) == []
    render = _Render()
    inbox.ingest(store, pcfg, notify=False, opener=render)               # Render is back
    assert [s[2]["prices"][0]["home"] for s in render.sent] == ["older", "newer"] and _left(pcfg) == []


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_a_refused_body_is_moved_aside_not_sent_again(pcfg, store, code):
    _price_drop(pcfg, time.time())
    render = _Render(code)
    counts = inbox.ingest(store, pcfg, notify=False, opener=render)
    assert len(render.sent) == 1 and counts["price_dropped"] == 1 and _left(pcfg) == [] and len(_done(pcfg)) == 1
    inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent again"))


def test_a_price_file_past_the_cut_off_is_moved_aside_unsent(pcfg, store):
    assert inbox.PRICE_MAX_AGE_S == 15 * 60
    _price_drop(pcfg, time.time() - inbox.PRICE_MAX_AGE_S - 1)
    counts = inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_dropped"] == 1 and _left(pcfg) == [] and len(_done(pcfg)) == 1


def test_a_route_this_inbox_does_not_know_waits_until_it_is_stale(pcfg, store):
    never = lambda req, timeout: pytest.fail("sent")
    _price_drop(pcfg, time.time(), route="/api/paddy/new-kind", check=False)
    counts = inbox.ingest(store, pcfg, notify=False, opener=never)
    assert counts["price_waiting"] == 1 and len(_left(pcfg)) == 1
    _price_drop(pcfg, time.time() - inbox.PRICE_MAX_AGE_S - 1, route="/api/paddy/new-kind", check=False)
    counts = inbox.ingest(store, pcfg, notify=False, opener=never)
    assert counts["price_dropped"] == 1 and counts["price_waiting"] == 1


def test_a_malformed_price_file_is_moved_aside(pcfg, store):
    good = {"source": "pp-epl-most", "at": time.time(), "url": "u", "route": "/api/paddy/most-prices", "rows": [MOST_ROW]}
    bad = ["{not json", "[]", json.dumps({**good, "rows": []}), json.dumps({k: v for k, v in good.items() if k != "at"}),
           json.dumps({**good, "rows": "x"}), json.dumps({**good, "rows": [1]}), json.dumps({**good, "route": 7})]
    d = pcfg.inbox_dir / "prices"
    d.mkdir(parents=True)
    for i, text in enumerate(bad):
        (d / f"{i}-bad.json").write_text(text)
    counts = inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_dropped"] == len(bad) and _left(pcfg) == []


def test_no_render_configured_leaves_prices_waiting(cfg, store):
    _price_drop(cfg, time.time())                                       # cfg: render_http and key blank
    counts = inbox.ingest(store, cfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_waiting"] == 1 and len(_left(cfg)) == 1


def test_render_hanging_never_holds_up_tips_or_events(pcfg, store):
    _drop(pcfg, "tips", "tips-a", {"source": "tips-a", "at": time.time(), "url": "https://a.example.com",
                                   "channel": "tips", "trust_weight": 0.5,
                                   "rows": [{"event": "A v B", "market": "Match Result", "selection": "A"}]})
    _price_drop(pcfg, time.time())

    def hang(req, timeout):
        raise TimeoutError("timed out")
    counts = inbox.ingest(store, pcfg, notify=False, opener=hang)
    assert counts["new_tips"] == 1 and counts["price_waiting"] == 1


def test_a_half_written_price_drop_is_never_read(pcfg, store):
    d = pcfg.inbox_dir / "prices"
    d.mkdir(parents=True)
    (d / "1-pp-epl-most-abcd1234.json.tmp").write_text("{")
    counts = inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert (d / "1-pp-epl-most-abcd1234.json.tmp").exists() and counts["price_dropped"] == 0


def test_a_second_pass_while_one_holds_the_lock_posts_nothing(pcfg, store):
    _price_drop(pcfg, time.time())
    with open(pcfg.inbox_dir / "prices" / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        counts = inbox.ingest(store, pcfg, notify=False, opener=lambda req, timeout: pytest.fail("sent"))
    assert counts["price_waiting"] == 1 and len(_left(pcfg)) == 1


def test_the_hubs_routes_are_the_schemas():
    assert set(inbox.PRICE_ROUTES) == set(SCHEMA.schema["$defs"]["scraper_prices"]["properties"]["route"]["enum"])
```

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-s1/hub && ~/.venvs/puntmore/bin/python -m pytest tests/test_inbox.py -q`
Expected: FAIL. `ingest()` takes no `opener`, and `PRICE_COUNT_KEYS` is not defined.

- [ ] **Step 3: Rename `finder._post` to `finder.post_json`,** then implement `PRICE_ROUTES`, `PRICE_MAX_AGE_S`, `PRICE_COUNT_KEYS`, `post_prices` and the `ingest` change as given in Interfaces and the Design table. Classify a status code as: 2xx posted; 408, 429 or 5xx waits; any other code refused. Catch `(OSError, http.client.HTTPException)` and `RuntimeError` (Render not configured) from `post_json` as "waits". Update the inbox module docstring: it also posts the Scraper's price drops to Render, the one thing in the inbox that crosses a network, and says how.

- [ ] **Step 4: Run the whole Hub suite.**
Run: `cd /home/callum/Projects/puntmore-s1/hub && ~/.venvs/puntmore/bin/python -m pytest -q`
Expected: all pass, including `tests/test_finder.py` unchanged.

- [ ] **Step 5: Commit**

```bash
cd /home/callum/Projects/puntmore-s1
git add hub/puntmore_hub/finder.py hub/puntmore_hub/inbox.py hub/tests/test_inbox.py
git commit -F - <<'EOF'
Hub: the inbox posts the Scraper's price drops to Render

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 4: The daemon and the Console show the price drops

**Files:**
- Modify: `hub/puntmore_hub/daemon.py` (module docstring; the log condition in `run`)
- Modify: `hub/puntmore_hub/web/collect.py` (`daemon()`: count `prices` drops; a `warn` state while prices wait)
- Modify: `hub/puntmore_hub/web/static/app.js` (the "drop files waiting" line)
- Test: `hub/tests/test_daemon.py`, `hub/tests/test_web.py`

**Interfaces:**
- Consumes: Task 3's `inbox.ingest` counts, including `price_files`, `price_dropped` and `price_waiting`.
- Produces:
  - `collect.daemon(...)["inbox_drops"]` has keys `tips`, `events` and `prices`; a key is None when its directory is missing.
  - The Daemon card's state is `warn`, with detail containing `f"{n} price drop(s) waiting for Render"`, when the last inbox counts have `price_waiting = n > 0`. This check comes after the down, error and stale-pass checks and before the mail check.
  - `daemon.run` logs `"inbox: %s"` when any of `tip_files`, `events`, `price_files` or `price_dropped` is non-zero.
  - The daemon docstring stops saying "Nothing here talks to Render". It says the inbox pass also posts the Scraper's Paddy price drops to Render (`inbox.post_prices`).

- [ ] **Step 1: Write the failing tests.** In `hub/tests/test_web.py::test_daemon_collector`, the first assertion becomes `out["inbox_drops"] == {"tips": 1, "events": 0, "prices": None}`. Add:

```python
def test_the_daemon_card_warns_while_prices_wait_for_render(wcfg):
    from puntmore_hub import daemon
    st = Store(wcfg.db_path)
    daemon.record_inbox(wcfg, {"tip_files": 0, "events": 0, "price_files": 0, "price_rows": 0,
                               "price_dropped": 0, "price_waiting": 3})
    out = collect.daemon(wcfg, st, run=lambda c, timeout=5: (0, "active\n"))
    assert out["state"] == "warn" and "3 price drop(s) waiting for Render" in out["detail"]
```

In `hub/tests/test_daemon.py`:

```python
def test_a_pass_that_posted_prices_is_logged(cfg, monkeypatch, caplog):
    calls = []

    def ingest(store, c):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {"tip_files": 0, "events": 0, "price_files": 2, "price_rows": 6, "price_dropped": 0, "price_waiting": 0}
    monkeypatch.setattr(inbox, "ingest", ingest)
    caplog.set_level(logging.INFO, logger="puntmore.daemon")
    daemon.run(cfg, sleep=lambda s: None)
    assert "price_files" in caplog.text
```

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-s1/hub && ~/.venvs/puntmore/bin/python -m pytest tests/test_web.py tests/test_daemon.py -q`
Expected: FAIL. `inbox_drops` has no `prices`, there is no warn state, and nothing is logged for prices.

- [ ] **Step 3: Implement** the three changes in Interfaces. In `app.js`, the line becomes `` `tips ${show(drops.tips)}, events ${show(drops.events)}, prices ${show(drops.prices)}` ``.

- [ ] **Step 4: Run the whole Hub suite.**
Run: `cd /home/callum/Projects/puntmore-s1/hub && ~/.venvs/puntmore/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /home/callum/Projects/puntmore-s1
git add hub/puntmore_hub/daemon.py hub/puntmore_hub/web/collect.py hub/puntmore_hub/web/static/app.js hub/tests/test_web.py hub/tests/test_daemon.py
git commit -F - <<'EOF'
Hub: the daemon and the Console show Paddy's price drops

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 5: The docs

**Files (all Modify):** `hub/PROTOCOL.md`, `docs/architecture.md`, `docs/dataflow.md`, `scraper/AGENTS.md`, `hub/AGENTS.md`, `docs/api.md`, `README.md`, `hub/README.md`, `scraper/README.md`, `docs/finder-spec.md`, `hub/.env.example`, `.env.example` (repo root). `PLAN.md` does not list this item: no change. `docs/decisions.md`: no change.

**Interfaces:**
- Consumes the names fixed by Tasks 1–4:
  - type `scraper_prices` with body `source, at, url, route, rows`
  - `<scraper data>/hub/prices/<ms>-<source>-<8hex>.json` and `<scraper data>/hub/done/prices/`
  - `scraper.sinks.deliver_prices`, `PRICE_ROUTES`
  - `inbox.post_prices`, `PRICE_MAX_AGE_S` (15 min), the counts `price_files`, `price_rows`, `price_dropped`, `price_waiting`
  - `finder.post_json`, `PUNTMORE_RENDER_HTTP`, `PUNTMORE_RENDER_API_KEY`
  - the log words `price row(s) handed to the Hub`, `prices … sent`, `wait for the next pass`, `refused`, `past the … cut-off`
- Produces: nothing code reads.

- [ ] **Step 1: `hub/PROTOCOL.md`.** Make these changes:
  - The intro: the Hub reads the files on its own disk, and posts one type, the prices, on to Render.
  - The envelope's `type` row lists the three types.
  - "The two types" becomes "The three types", with a `scraper_prices` row: where it is written, the body fields from the Design section, and one file per parse of one tab.
  - A new section, "Prices: what the Hub does with them". It holds the Design section's per-file table, the 15 min cut-off and why, the lock, `done/prices/`, and that the rows pass through unfiltered with Render as their validator.
  - The rule "Never a guessed row" now says the price sink filters nothing, so the body is what the parser read.
  - Tests: the example `scraper_prices.json`, and `hub/tests/test_inbox.py` for what the Hub posts.

- [ ] **Step 2: `docs/architecture.md`.**
  - "As of 11 Nov 2026".
  - Diagram: replace the edge `SVC -- "prices: POST … (direct today)" --> PADDY` with `SVC -- "prices: drop files (scraper_prices)" --> CTRL` and `CTRL -- "prices: POST /api/paddy/saves-fair, /most-prices" --> PADDY`. Merge the first into the existing tips edge's label as `"tips and prices: drop files (scraper_tips, scraper_prices, scraper_event)"`.
  - CTRL node: `part built: targets library, inbox (tips, events, Paddy's prices), mailbox poll`.
  - Components: the Paddy row reads "OddsPapi plus the Scraper's prices, posted by Scraper control". The Scraper control row adds "posts the Scraper's Paddy prices to Render (`inbox.post_prices`)".
  - "What crosses": Paddy prices up, Who = "Scraper control (the Hub's inbox) for the Scraper service", Key = "ingest key (`PUNTMORE_RENDER_API_KEY`)".
  - Open: delete item 1 and renumber 2–6 as 1–5.

- [ ] **Step 3: `docs/dataflow.md`.**
  - Stage 3: drop the "exception for now" sentence. Add the `scraper_prices` files under `<data>/hub/prices/`, one per parse of a price target's tab.
  - Stage 4: add "prices: posted to Render as `{"prices": rows}` on the file's route (`inbox.post_prices`), oldest first; a file older than 15 min is moved aside unsent", with `done/prices/`.
  - Rule 1: three types.
  - "Where to look when something is lost": add **A Paddy price that never reached the board**:
    1. the Scraper's journal (`journalctl --user -u puntmore-scraper`, "price row(s) handed to the Hub");
    2. `<scraper data>/hub/prices/` (waiting) and `done/prices/` (handled);
    3. the daemon's journal (`journalctl --user -u puntmore-link`, `prices …` lines: sent, wait, refused, cut-off);
    4. the Console's Daemon card (prices waiting);
    5. on Render, the match's most-corners/cards row on `GET /api/paddy/feed`.

- [ ] **Step 4: The rest.**
  - `scraper/AGENTS.md`:
    - The intro drops "Paddy's prices still post straight to Render…". All rows go to Scraper control as drop files; prices are `scraper_prices` files that the Hub posts to Render.
    - Module map `sinks.py`: "`/api/...` targets: `scraper_prices` drop files the Hub posts to that route; `hub:<chan>`: tips; events".
    - "the only thing it sends Render is the prices it posts" becomes "it sends Render nothing; its prices go through the Hub".
  - `hub/AGENTS.md`:
    - The intro lists the inbox's price posts as part of Scraper control's intake.
    - The drop-files rule names the three types.
    - A Rules line: "Paddy's prices go to Render through the inbox only (`inbox.post_prices`, `PUNTMORE_RENDER_API_KEY`); no retry loop: a file waits for the next pass, and is dropped once 15 min old."
    - Checks: `puntmore inbox` also posts waiting price drops.
  - `docs/api.md`:
    - The `ingest` line names "Scraper control's posts of the Scraper's prices" instead of "the Scraper service".
    - The Paddy ingest row's caller is "Scraper control (the Hub's inbox), posting the Scraper service's prices".
  - `README.md`: the Scraper control bullet says Paddy's prices now go through it to Render.
  - `hub/README.md`:
    - In the diagram, the `Paddy board ◄── POST /api/paddy/*` arrow comes from Scraper control, not the Scraper service.
    - Layout: the `inbox.py` line adds "prices -> Render".
    - The drop-files section has a paragraph on prices.
  - `scraper/README.md`:
    - Lines 13–16: prices are drop files too.
    - Credentials: remove `SAVES_INGEST_KEY`.
  - `docs/finder-spec.md:31`: "as the Scraper does for Paddy" becomes "as Scraper control does for Paddy's prices".
  - `hub/.env.example`: the `PUNTMORE_RENDER_API_KEY` comment says it also posts the Scraper's Paddy prices.
  - Root `.env.example`: the `SAVES_INGEST_KEY` comment names `local/bookiebashing_saves.py` and the Hub (Scraper control, posting the Scraper's prices), not "the Scraper (scraper/, on the Hub)".

- [ ] **Step 5: Check that no stale line is left.**
Run: `cd /home/callum/Projects/puntmore-s1 && grep -rn "straight to Render\|direct today\|exception for now\|the two types\|two drop files" --include=*.md . | grep -v "docs/superpowers/\|docs/decisions.md"`
Expected: no output.
Run both suites again.
Expected: the baselines from Tasks 3–4.

- [ ] **Step 6: Commit**

```bash
cd /home/callum/Projects/puntmore-s1
git add hub/PROTOCOL.md docs/architecture.md docs/dataflow.md scraper/AGENTS.md hub/AGENTS.md docs/api.md README.md hub/README.md scraper/README.md docs/finder-spec.md hub/.env.example .env.example
git commit -F - <<'EOF'
Docs: Paddy's prices go through Scraper control

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 6: Live proof (controller)

**Run by the controller after the pull request is merged, not by a builder.** Nothing here may post anything to Render that the Scraper's direct post would not have posted. That rules out:
- hand-made or replayed price files;
- `curl -X POST` to `/api/paddy/*`;
- edits to `targets.toml` or `bb-saves`.

If a step fails, stop and report it to Callum.

- [ ] **Step 1: The box is on the merge.** Run `git -C ~/Projects/puntmore log -1 --format='%h %s'` and `tail -5 ~/Projects/puntmore/hub/data/update.log`. Expected: the merge sha, and a run that restarted `puntmore-link.service` (and `puntmore-scraper.service`, since `scraper/` changed).
- [ ] **Step 2: The Hub's Render settings are present.** Values are never printed. Run `grep -c '^PUNTMORE_RENDER_HTTP=..*' ~/Projects/puntmore/hub/.env` and `grep -c '^PUNTMORE_RENDER_API_KEY=..*' ~/Projects/puntmore/hub/.env`. Expected: `1` and `1`. A `0` means every price would wait and then be dropped: stop and tell Callum.
- [ ] **Step 3: The Scraper is running.** It has been `failed` (start-limit-hit, Chrome exiting on SIGTRAP) since 7 Nov 2026. Run `systemctl --user is-active puntmore-scraper`. If it is not active, run `systemctl --user reset-failed puntmore-scraper && systemctl --user start puntmore-scraper`, then `journalctl --user -u puntmore-scraper -n 50 --no-pager`. If Chrome still dies, stop: the proof needs a real parse, and the failure is Callum's to see.
- [ ] **Step 4: The Scraper drops and does not post.** Do this in Paddy hours (outside 01:00–07:00) with a Premier League match listed. Run `journalctl --user -u puntmore-scraper -f | grep -E "price row\(s\) handed to the Hub|sent [0-9]+ ->|send failed"`. Expected: only `pp-epl-most: N price row(s) handed to the Hub (/api/paddy/most-prices)`. Any `sent N ->` line after the restart means an old Scraper process is still posting directly.
- [ ] **Step 5: The Hub posts them.** Run `journalctl --user -u puntmore-link -f | grep prices`. Expected: within 30 s of each Scraper line, `prices pp-epl-most -> /api/paddy/most-prices: sent N row(s), Render answered {"stored": N, "rejected": []}`. There should be no `wait for the next pass`, `refused` or `cut-off` lines.
- [ ] **Step 6: The files.** Run `ls /opt/puntmore-scraper/data/hub/prices/`. Expected: empty, or only files under 30 s old. Run `ls -t /opt/puntmore-scraper/data/hub/done/prices | head -3`. Then check that `jq '.body.rows' <the newest of them>` equals the rows in `/opt/puntmore-scraper/data/latest/pp-epl-most.json` when that file's `at` equals the drop's `body.at`. `latest/` is rewritten per tab, so pick a matching pair.
- [ ] **Step 7: Render shows what a direct post would have shown.** Open the Paddy board on `https://puntmore.com/paddy` as Callum, or call `GET /api/paddy/feed` with a key that holds `cards`. For the match in the newest done file, the "most corners" / "most cards" rows should carry Paddy's odds equal to that file's `home_price`, `tie_price` and `away_price`.
- [ ] **Step 8: The Console.** Open http://127.0.0.1:8090, Daemon card. Expected: state up, "drop files waiting" shows `prices 0` (or a count that empties within 30 s), and the last inbox counts show `price_files` ≥ 1.

---

## Self-review

- **Spec coverage.** Every item in the Design section is built by a task:
  - Drop files and their fields: Tasks 1–2.
  - Who posts what, where, with which key: Task 3 (Render side unchanged; Scraper side Task 2).
  - Every failure case: Task 3's tests (5xx/408/429/network/timeout; 4xx; stale; unknown route; malformed; not configured; lock; half-written).
  - Inbox not running: shown through Task 4's Console; the age rule is Task 3.
  - Version skew: new Scraper with an old inbox uses a separate directory (Task 2 test that `tips/` stays empty). Old Scraper with a new inbox needs no code: the inbox only reads `prices/`.
  - `bb-saves`: Task 2's saves test and Task 3's saves-route test.
  - Docs: Task 5.
  - Live proof: Task 6.
- **Step scan.** Each code step names the file, the signature and the values. The bodies are left to the tests, except the classification rule, which is stated as a table.
- **Type consistency.** These names are the same in every task that uses them: `PRICE_ROUTES` (the same two strings in the Scraper, the Hub and the schema, pinned by a test on each side), `PRICE_COUNT_KEYS`, `post_prices(cfg, *, opener=None)`, `ingest(..., opener=None)`, `finder.post_json`, `deliver_prices(data_dir, target, rows, now)`, `done/prices`.
- **Review Focus.** Each of the five lines has its test in Task 2 or Task 3.
- **Proportion.** The code blocks are tests and signatures. The bodies of `deliver_prices`, `post_prices` and the Console change are the builder's.
