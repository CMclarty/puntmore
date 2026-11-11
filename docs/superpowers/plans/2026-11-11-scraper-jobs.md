# Scraper jobs: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Scraper target becomes a job: it can run on chosen weekdays until an end date, it keeps its own log, and the Console shows each one as a tile that the CLI, the Console and HubAI all change the same way.

**Architecture:** Two new `Target` fields, `days` and `until`, are checked by `config._target` and acted on by a new `schedule.inactive()`, which replaces the bare window check in `Runner.tick` and `Runner.status`. `scraper/joblog.py` writes one bounded JSON-lines file per job, and `Runner._say` sends every per-target line both to stdout and to that file. The Console reads targets, schedule words and logs through the Scraper's own library (`scraper.targets`, `scraper.joblog`) and draws one tile per job. Code keeps the names `Target` and `targets.toml`; the docs and the Console say "job".

**Tech Stack:** Python 3 (Scraper: stdlib, pytest; Hub: FastAPI, pytest), plain JS/HTML/CSS for the Console.

**Spec:** `docs/superpowers/specs/2026-11-11-scraper-jobs-paddy-saves-design.md`, Part J. Plan M (`2026-11-11-paddy-map-saves.md`) runs after this one on the same branch.

## Spec corrections

1. **Where `days` and `until` are validated.** The spec says "validated in `scraper/targets.py`". `scraper/AGENTS.md` says "Validation stays in `config._target`; `targets.py` must not grow its own copy". So the rules live in `config._target`, and every edit reaches them through `targets.validate`, as every other rule does. `targets.py` only checks types, as it already does for `matches.market_tabs`.

## Planner's calls

1. `schedule.inactive(t, now) -> str | None` replaces `schedule.in_window` in the tick and in `status`. Reasons are checked in this order: `"finished"`, then `"not a run day"`, then `"outside window"`.
2. A job's **day** is the local date its window started on: the date minus one when the window wraps midnight and the time is before the window's end. `days` and `until` are both judged on that day.
3. `days` and `until` join `RESCHEDULE_FIELDS`, so changing them never closes a page by itself.
4. The schedule in words is made once, by `targets.schedule_words`. It is carried in `targets.summary()` as `"schedule"`, and both the CLI and the Console print that string.
5. `Runner._say(name, msg)` prints `f"{name}: {msg}"` and writes `msg` to the job's log. A line whose old prefix was a URL or a tab key keeps that text at the start of `msg`. The sink's "price row(s) handed to the Hub" line stays a sink print, and `Runner._deliver` writes the same text to the log.
6. Logs live in the Scraper's data dir. `targets.remove(path, name, timeout=None, data_dir=None)` removes the log, with `data_dir` defaulting to the targets file's directory. The CLI passes `SCRAPER_DATA_DIR`; the Console passes `cfg.scraper_data_dir`.
7. A tile shows the newest 5 log lines (`LOG_TAIL = 5`); "all lines" fetches `?n=200`. The route clamps `n` to 1..1000.
8. A tile shows exactly what J3 lists. The old table's other columns (mode, parser, post, priority, near, kick-off, next due) are no longer drawn. They stay in `targets show` and in the edit form where that form has them.

## Global Constraints

- Work only in `/home/callum/Projects/puntmore-jobs`, branch `scraper-jobs`. Never switch branches, never push, never use `git stash`, never touch another checkout.
- Nothing edits `docs/decisions.md`, `scraper/sites.py`'s limits, the live `/opt/puntmore-scraper/**` or `bb-saves`. No test or script loads a Paddy Power or Bookie Bashing page. Browser behaviour is tested with the stand-ins in `scraper/tests/test_pacing.py` (`_Nothing`, `_Page`, `_Ctx`, `_runner`).
- No randomised timing, input simulation, fingerprint or user-agent work is added anywhere.
- Frontend: add nothing the spec does not name.
- `days`: lower-case three-letter local weekday names from `("mon", "tue", "wed", "thu", "fri", "sat", "sun")`, no repeats; empty or absent means every day. `until`: `"YYYY-MM-DD"`, the last local day; absent means forever. A job past its `until` reports state `finished` and is not removed.
- Job log: `<data>/logs/<name>.log`, one `{"ts": <epoch>, "msg": "..."}` per line. Once a file passes 512 KB it is rewritten to its newest 1000 lines. Removing a job removes its log.
- TDD: write the failing test, run it to see it fail, write the code, run it to see it pass, commit. Small commits. Docs change in the same task as the code they describe.
- Hub tests: `cd /home/callum/Projects/puntmore-jobs/hub && ~/.venvs/puntmore/bin/python -m pytest -q`. Baseline: **179 passed, 1 warning**.
- Scraper tests, from the repo root: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`. Baseline: **109 passed, 8 failed, 2 errors**. Those are the 10 known failures from the missing headless browser: 8 in `test_e2e_fakesite.py`, 2 errors in `test_bb_saves.py`. "Green" means no failure outside those 10.
- Before the first commit, if `git config core.hooksPath` does not print `.githooks`, run `git config core.hooksPath .githooks`.
- Every commit message ends with exactly:
  ```
  Co-Authored-By: Claude <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
  ```

## Review Focus

1. **A window past midnight on the last run day.** `days = ["sat"]`, `window = "20:00-02:00"` must run until 02:00 Sunday and must not start again at Sunday 20:00. Test in Task 1.
2. **An edit to only `days` or `until` on a running job** must not close its pages. Test in Task 1.
3. **A log write that fails** (the logs path not writable) must never break a tick. Test in Task 2.
4. **A torn last line** (a crash mid-write) must not hide the lines before it. Test in Task 2.
5. **A job name with spaces or slashes** must log inside `<data>/logs/`, and the log route must never read outside it. Tests in Tasks 2 and 3.

---

### Task 1: Days and an end date

**Files:**
- Modify: `scraper/config.py` (`WEEKDAYS`, `Target.days`, `Target.until`, `_target`)
- Modify: `scraper/targets.py` (`_STRINGS`, type check on `days`, `to_mapping`, `schedule_words`, `summary`)
- Modify: `scraper/schedule.py` (`job_day`, `inactive`; module docstring)
- Modify: `scraper/runner.py` (`RESCHEDULE_FIELDS`, `tick`, `_outside_window`, `status`, `self.outside`)
- Modify: `scraper/__main__.py` (`--days`, `--until`, `_target_line`, docstring)
- Modify: `scraper/targets.example.toml` (field reference for `days`, `until`), `scraper/README.md` ("Changing targets": the two flags, "a target is a job"), `scraper/AGENTS.md` (module map line for `schedule.py`), `hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md` ("Adding a Finder source" step 4: `--days fri,sat`, `--until 2026-11-30`, `none` clears)
- Test: `scraper/tests/test_schedule.py`, `scraper/tests/test_targets_lib.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `config.WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")`.
  - `Target.days: tuple[str, ...] = ()` and `Target.until: str | None = None`, added after `near_hours`.
  - `config._target` refuses a bad value with `ValueError(f"target {name}: days must be a list of {', '.join(WEEKDAYS)} with no repeats, not {days!r}")` or `ValueError(f"target {name}: until must be a date \"YYYY-MM-DD\", not {until!r}")`. `days` is kept in the order given.
  - `schedule.job_day(window: str | None, now: float) -> datetime.date`.
  - `schedule.inactive(t, now: float) -> str | None`: one of `"finished"`, `"not a run day"`, `"outside window"`, or None when the job is active.
  - `runner.RESCHEDULE_FIELDS` gains `"days"` and `"until"`. `Runner.outside` becomes `dict[str, str]` (name to reason).
  - `Runner.status` reports these states and details:
    - finished: `("finished", f"ended {t.until}")`;
    - not a run day: `("paused", "not a run day")`;
    - outside window: `("paused", "outside window")`, unchanged.
  - `targets.to_mapping(t)["days"]` is a list.
  - `targets.schedule_words(d: dict) -> str`, joined with `" · "`. The parts are:
    - `f"every {every_min:g} min"`, or `f"when quiet {stale_min:g} min"`;
    - the days in week order, title-case (`"Fri, Sat"`), or `"every day"`;
    - the window with an en dash (`"07:00–01:00"`), or `"all day"`;
    - `"forever"`, or `f"until {day} {Mon} {year}"` (`"until 30 Nov 2026"`, no leading zero).
  - Each `targets.summary()` entry gains `"schedule": schedule_words(entry)`.
  - CLI: `--days fri,sat` (a comma list; `none` clears) and `--until YYYY-MM-DD` (`none` clears), on both `add` and `set`. `_target_line` prints `f"{name:28} {site:13} {state:12} {schedule}"`, followed by the priority and detail as today.

- [ ] **Step 1: Write the failing tests.** In `scraper/tests/test_schedule.py`:

```python
FRI, SAT, SUN, THU = (datetime(2026, 11, d) for d in (13, 14, 15, 12))   # 13 Nov 2026 is a Friday


def _on(day, hh, mm=0):
    return day.replace(hour=hh, minute=mm).timestamp()


def test_days_and_until_decide_whether_a_job_is_active():
    t = _bb("j", days=("fri", "sat"))
    assert schedule.inactive(t, _on(FRI, 10)) is None
    assert schedule.inactive(t, _on(SAT, 23, 59)) is None
    assert schedule.inactive(t, _on(THU, 10)) == "not a run day"
    assert schedule.inactive(t, _on(SUN, 0)) == "not a run day"
    assert schedule.inactive(_bb("every"), _on(THU, 10)) is None             # no days = every day
    u = _bb("u", until="2026-11-14")
    assert schedule.inactive(u, _on(SAT, 23, 59)) is None
    assert schedule.inactive(u, _on(SUN, 0)) == "finished"
    w = _bb("w", window="09:00-10:00", until="2026-11-14")
    assert schedule.inactive(w, _on(FRI, 8)) == "outside window"
    assert schedule.inactive(w, _on(SUN, 9, 30)) == "finished"               # finished wins


def test_a_window_past_midnight_belongs_to_the_day_it_started():
    t = _bb("late", window="20:00-02:00", days=("sat",), until="2026-11-14")
    assert schedule.inactive(t, _on(SAT, 21)) is None
    assert schedule.inactive(t, _on(SUN, 1, 59)) is None                     # Saturday's run
    assert schedule.inactive(t, _on(SUN, 2)) == "finished"
    assert schedule.inactive(t, _on(SAT, 1, 30)) == "not a run day"          # Friday's run
    assert schedule.inactive(_bb("x", window="20:00-02:00", days=("sat",)), _on(SUN, 21)) == "not a run day"
    assert schedule.job_day("20:00-02:00", _on(SUN, 1)) == SAT.date()
    assert schedule.job_day(None, _on(SUN, 1)) == SUN.date()


def test_off_days_close_the_job_and_status_says_why(tmp_path):
    r = _runner(tmp_path, [_bb("bb", days=("fri",), until="2026-11-14")])
    r.tick(_on(FRI, 10))
    assert "bb" in r.tabs and len(_loads(r)) == 1
    r.tick(_on(SAT, 10))
    assert "bb" not in r.tabs
    st = r.status(_on(SAT, 10))["targets"][0]
    assert (st["state"], st["detail"]) == ("paused", "not a run day")
    r.tick(_on(SUN, 10))
    st = r.status(_on(SUN, 10))["targets"][0]
    assert (st["state"], st["detail"]) == ("finished", "ended 2026-11-14")
    assert len(_loads(r)) == 1
```

In `scraper/tests/test_targets_lib.py`:

```python
def test_days_and_until_round_trip_and_are_refused_when_wrong(path):
    T.set(path, "pp", days=["fri", "sat"], until="2026-11-30")
    t = T.get(path, "pp")
    assert (t.days, t.until) == (("fri", "sat"), "2026-11-30")
    text = path.read_text()
    assert 'days = ["fri", "sat"]' in text and 'until = "2026-11-30"' in text
    assert T.to_mapping(t)["days"] == ["fri", "sat"]
    before = path.read_text()
    for bad, why in (({"days": ["friday"]}, "days must be"), ({"days": ["fri", "fri"]}, "days must be"),
                     ({"days": "fri"}, "days must be"), ({"until": "30/11/2026"}, "until must be"),
                     ({"until": "2026-02-30"}, "until must be")):
        with pytest.raises(T.TargetsError, match=why):
            T.set(path, "pp", **bad)
    assert path.read_text() == before
    T.set(path, "pp", days=None, until=None)
    assert (T.get(path, "pp").days, T.get(path, "pp").until) == ((), None)


def test_a_days_or_until_edit_is_applied_in_place():
    from scraper.runner import reschedule_only
    assert reschedule_only(FULL[0], dataclasses.replace(FULL[0], days=("fri",), until="2026-12-31"))


def test_schedule_in_words():
    w = T.schedule_words
    assert w({"every_min": 15, "stale_min": 20, "days": ["sat", "fri"], "window": "07:00-01:00",
              "until": None}) == "every 15 min · Fri, Sat · 07:00–01:00 · forever"
    assert w({"every_min": None, "stale_min": 20, "days": [], "window": None,
              "until": "2026-11-30"}) == "when quiet 20 min · every day · all day · until 30 Nov 2026"


def test_cli_days_and_until(cli, path):
    code, out, _ = cli("set", "pp", "--days", "fri,sat", "--until", "2026-11-30", "--json")
    d = json.loads(out)
    assert code == 0 and d["days"] == ["fri", "sat"] and d["until"] == "2026-11-30"
    assert d["schedule"] == "every 15 min · Fri, Sat · 07:00–23:30 · until 30 Nov 2026"
    code, out, _ = cli("set", "pp", "--days", "none", "--until", "none", "--json")
    assert (json.loads(out)["days"], json.loads(out)["until"]) == ([], None)
    code, _, err = cli("set", "pp", "--days", "fri,funday")
    assert code == 2 and "days must be" in err
```

Add `import dataclasses` to the test file. In `test_cli_human_list`, add `assert "every 15 min · every day · 07:00–23:30 · forever" in out`.

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests/test_schedule.py scraper/tests/test_targets_lib.py -q`
Expected: FAIL, because `Target` has no `days` and `schedule` has no `inactive`.

- [ ] **Step 3: Add the fields and the validation** in `config.py`, as in Interfaces. Then in `targets.py`:
  - `"until"` joins `_STRINGS`;
  - `days`, when given, must be a list or tuple of strings, refused with the same message `config._target` uses;
  - `to_mapping` lists `days`;
  - `render` already writes a non-default tuple as a TOML array.

- [ ] **Step 4: Add `schedule.job_day` and `schedule.inactive`** (Planner's calls 1–2). In `Runner.tick`, call `inactive` where `in_window` is called today:
  - A reason makes `_outside_window(t, why)` close the job's tabs. It prints once per change of reason: `f"{t.name}: outside window {t.window} - paused"` for the window (today's text), `f"{t.name}: {why} - paused"` otherwise.
  - Coming back prints `f"{t.name}: window open - running"`, as today.
  - `status()` uses the same function for its states.

- [ ] **Step 5: Add the CLI flags, `schedule_words` and the `"schedule"` key** (Interfaces). `--days` parses `"fri,sat"` into `["fri", "sat"]` and `"none"` into None. A bad name is left for `config._target` to refuse, which exits 2 with its message.

- [ ] **Step 6: Run the Scraper suite.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`
Expected: the baseline plus the new tests passing; no failure outside the 10 known ones.

- [ ] **Step 7: The docs** in the Files list. `targets.example.toml` gains commented `days` and `until` lines in its field reference. Then commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add scraper/config.py scraper/targets.py scraper/schedule.py scraper/runner.py scraper/__main__.py \
  scraper/targets.example.toml scraper/README.md scraper/AGENTS.md \
  hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md scraper/tests/test_schedule.py scraper/tests/test_targets_lib.py
git commit -F - <<'EOF'
Scraper: a job runs on chosen days until an end date

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 2: A log per job

**Files:**
- Create: `scraper/joblog.py`
- Modify: `scraper/runner.py` (`_say`; every per-target `print`; `_deliver`; `_requests`' dump lines)
- Modify: `scraper/targets.py` (`remove` gains `data_dir`)
- Modify: `scraper/__main__.py` (`targets log`; `remove` passes the data dir; docstring)
- Modify: `scraper/README.md` ("Changing targets": `targets log`), `scraper/AGENTS.md` (module map: `joblog.py`), `hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md` (Triage step 2: `targets log <job> -n 50` before the journal)
- Test: Create `scraper/tests/test_joblog.py`; modify `scraper/tests/test_targets_lib.py`

**Interfaces:**
- Consumes: Task 1's `Runner.outside` messages (the test reads them back).
- Produces:
  - `joblog.MAX_BYTES = 512 * 1024` and `joblog.KEEP_LINES = 1000`.
  - `joblog.path(data_dir: pathlib.Path, name: str) -> pathlib.Path`: `data_dir / "logs" / f"{sinks._safe(name)}.log"`.
  - `joblog.write(data_dir, name: str, msg: str, now: float | None = None) -> None`. It appends one line. Past `MAX_BYTES` it rewrites the file to its newest `KEEP_LINES` lines (temp file + rename). It never raises: an `OSError` is swallowed.
  - `joblog.read(data_dir, name: str, n: int = 50) -> list[dict]`: the newest `n` lines as `{"ts": float, "msg": str}`, oldest first. A line that does not parse is skipped. A missing file gives `[]`.
  - `joblog.remove(data_dir, name: str) -> None`.
  - `Runner._say(name: str, msg: str) -> None` (Planner's call 5).
  - `targets.remove(path, name, timeout=None, data_dir=None) -> list[Target]`: it also removes the job's log under `data_dir`, which defaults to `pathlib.Path(path).parent`.
  - CLI: `python -m scraper targets log NAME [-n 50] [--json]`.
    - `--json` prints `{"name": NAME, "lines": [...]}`.
    - Without it, each line prints as `f"{datetime.fromtimestamp(ts):%Y-%m-%d %H:%M:%S}  {msg}"`.
    - The data dir is `SCRAPER_DATA_DIR`, else `config.HERE / "data"`. `targets remove` passes the same directory.
- The prints that become `_say`. Each keeps its text, minus the leading `"<target name>: "`:
  - `tick`'s "window open" line and `_outside_window`'s line;
  - `_sweep`'s kick-off line;
  - `_load`'s "load failed", with the key in front of `msg`;
  - `_tick_matches`' "retired";
  - the three lines in `_open_market_tabs`, with the tab URL in front of `msg`;
  - `_parse`'s "capturing only" and "parser failed";
  - `_snapshot_once`'s "snapshot failed";
  - `_requests`' "dump" lines for a named target.
  - `_deliver` writes `f"{len(rows)} row(s) handed to the Hub ({t.post})"` to the log only, after a successful delivery.
  - Site-wide lines (paused, cool-off, block screenshots, `config: …`) stay plain prints.

- [ ] **Step 1: Write the failing tests** in `scraper/tests/test_joblog.py`:

```python
import json

from scraper import joblog
from scraper.tests.test_pacing import _runner
from scraper.tests.test_schedule import _at, _bb


def test_one_json_line_each_newest_last(tmp_path):
    joblog.write(tmp_path, "pp-saves", "first", now=100.0)
    joblog.write(tmp_path, "pp-saves", "second", now=101.0)
    assert joblog.read(tmp_path, "pp-saves") == [{"ts": 100.0, "msg": "first"}, {"ts": 101.0, "msg": "second"}]
    assert joblog.read(tmp_path, "pp-saves", n=1) == [{"ts": 101.0, "msg": "second"}]
    first = (tmp_path / "logs" / "pp-saves.log").read_text().splitlines()[0]
    assert json.loads(first) == {"ts": 100.0, "msg": "first"}
    assert joblog.read(tmp_path, "nobody") == []


def test_past_512_kb_a_log_keeps_its_newest_1000_lines(tmp_path):
    for i in range(3000):
        joblog.write(tmp_path, "j", f"line {i} " + "x" * 300, now=float(i))
    assert joblog.path(tmp_path, "j").stat().st_size <= joblog.MAX_BYTES + 400
    lines = joblog.read(tmp_path, "j", n=10_000)
    assert 1000 <= len(lines) < 1600
    assert lines[-1]["msg"].startswith("line 2999 ")
    assert [x["ts"] for x in lines] == [float(i) for i in range(3000 - len(lines), 3000)]


def test_a_torn_line_is_skipped_and_odd_names_stay_in_logs(tmp_path):
    joblog.write(tmp_path, "a/../b c", "ok", now=1.0)
    p = joblog.path(tmp_path, "a/../b c")
    assert p.parent == tmp_path / "logs"
    with p.open("a") as f:
        f.write('{"ts": 2.0, "msg": "torn')
    assert joblog.read(tmp_path, "a/../b c") == [{"ts": 1.0, "msg": "ok"}]


def test_a_write_that_fails_never_raises(tmp_path):
    (tmp_path / "logs").write_text("a file where the directory should be")
    joblog.write(tmp_path, "j", "lost")


def test_the_runner_logs_what_it_prints_about_a_job(tmp_path, capsys):
    r = _runner(tmp_path, [_bb("bb", window="09:00-10:00")])
    r.tick(_at(8, 59))
    r.tick(_at(9))
    out = capsys.readouterr().out
    assert "bb: outside window 09:00-10:00 - paused" in out and "bb: window open - running" in out
    assert [x["msg"] for x in joblog.read(tmp_path, "bb")] == ["outside window 09:00-10:00 - paused",
                                                              "window open - running"]


def test_a_tick_survives_a_log_it_cannot_write(tmp_path):
    (tmp_path / "logs").write_text("not a directory")
    r = _runner(tmp_path, [_bb("bb", window="09:00-10:00")])
    r.tick(_at(8, 59))
    r.tick(_at(9))
    assert "bb" in r.tabs
```

In `scraper/tests/test_targets_lib.py`:

```python
def test_removing_a_job_removes_its_log(path):
    from scraper import joblog
    joblog.write(path.parent, "tips", "something")
    T.remove(path, "tips")
    assert not joblog.path(path.parent, "tips").exists()


def test_cli_targets_log(cli, path):
    from scraper import joblog
    for i in range(3):
        joblog.write(path.parent, "pp", f"line {i}", now=1_800_000_000.0 + i)
    code, out, _ = cli("log", "pp", "-n", "2", "--json")
    assert code == 0 and json.loads(out) == {"name": "pp", "lines": [
        {"ts": 1_800_000_001.0, "msg": "line 1"}, {"ts": 1_800_000_002.0, "msg": "line 2"}]}
    code, out, _ = cli("log", "pp")
    assert code == 0 and out.splitlines()[-1].endswith("  line 2")
    cli("remove", "pp")
    assert not joblog.path(path.parent, "pp").exists()
```

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests/test_joblog.py scraper/tests/test_targets_lib.py -q`
Expected: FAIL with `ImportError: cannot import name 'joblog'`.

- [ ] **Step 3: Write `scraper/joblog.py`** (Interfaces). The size is checked after each append, with one `stat` per write.

- [ ] **Step 4: Wire the runner, `targets.remove` and the CLI** (Interfaces).

- [ ] **Step 5: Run the Scraper suite.**
Run: `cd /home/callum/Projects/puntmore-jobs && ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q`
Expected: no failure outside the 10 known ones. A test that greps stdout for an old line still passes, because stdout is unchanged except for the key/URL prefixes named above.

- [ ] **Step 6: The docs** in the Files list, then commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add scraper/joblog.py scraper/runner.py scraper/targets.py scraper/__main__.py scraper/README.md scraper/AGENTS.md \
  hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md scraper/tests/test_joblog.py scraper/tests/test_targets_lib.py
git commit -F - <<'EOF'
Scraper: each job keeps its own log

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 3: The Console's job routes

**Files:**
- Modify: `hub/puntmore_hub/web/scraper_targets.py` (`EDITABLE`, `LOG_TAIL`, `LOG_MAX`, `joblog`, `log`, `view`, `remove`; module docstring)
- Modify: `hub/puntmore_hub/web/app.py` (`GET /api/scraper/log/{target}`)
- Test: `hub/tests/test_web_scraper.py`

**Interfaces:**
- Consumes:
  - `targets.summary()`'s `"schedule"`, and the `days`/`until` fields (Task 1);
  - `joblog.read`, `joblog.write` and `targets.remove(..., data_dir=)` (Task 2).
- Produces:
  - `scraper_targets.EDITABLE` gains `"days"` and `"until"`.
  - `scraper_targets.LOG_TAIL = 5` and `scraper_targets.LOG_MAX = 1000`.
  - `scraper_targets.joblog(cfg) -> ModuleType`: `scraper.joblog`, loaded the way `library()` loads `scraper.targets`.
  - Each `view()` row's `"view"` gains `"schedule"` (the row's `"schedule"`) and `"log"`: the newest `LOG_TAIL` lines as `[{"at": <ISO via ts_iso>, "msg": str}]`.
  - `scraper_targets.log(cfg, name: str, n: int) -> dict`: `{"target": name, "n": <n clamped to 1..LOG_MAX>, "lines": [{"at", "msg"}]}`.
  - `GET /api/scraper/log/{target}?n=50` returns `scraper_targets.log(...)`, or 503 when the library is missing.
  - `scraper_targets.remove` calls the library's `remove` with `data_dir=cfg.scraper_data_dir`.

- [ ] **Step 1: Write the failing tests** in `hub/tests/test_web_scraper.py`:

```python
def test_days_and_until_through_the_console(scfg):
    c = client(scfg)
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H,
               json={"days": ["fri", "sat"], "until": "2026-11-30"})
    assert r.status_code == 200, r.text
    assert (r.json()["target"]["days"], r.json()["target"]["until"]) == (["fri", "sat"], "2026-11-30")
    v = {t["name"]: t for t in c.get("/api/scraper").json()["config"]["targets"]}["bb-saves"]["view"]
    assert v["schedule"] == "every 10 min · Fri, Sat · all day · until 30 Nov 2026"
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"days": ["funday"]})
    assert r.status_code == 400 and r.json()["field"] == "days"
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"until": "next week"})
    assert r.status_code == 400 and r.json()["field"] == "until"
    r = c.post("/api/actions/scraper/targets/bb-saves/set", headers=H, json={"days": None})
    assert r.status_code == 200 and r.json()["target"]["days"] == []
    r = c.post("/api/actions/scraper/targets/add", headers=H, json={
        "name": "weekend", "site": "generic", "url": "https://tips.example/w", "days": ["sat"], "enabled": False})
    assert r.status_code == 200 and r.json()["target"]["days"] == ["sat"]


def test_the_log_route_and_the_tile_tail(scfg):
    jl = scraper_targets.joblog(scfg)
    for i in range(8):
        jl.write(scfg.scraper_data_dir, "bb-saves", f"line {i}", now=1_800_000_000.0 + i)
    c = client(scfg)
    v = {t["name"]: t for t in c.get("/api/scraper").json()["config"]["targets"]}["bb-saves"]["view"]
    assert [x["msg"] for x in v["log"]] == [f"line {i}" for i in range(3, 8)]
    assert v["log"][0]["at"].endswith("+00:00")
    d = c.get("/api/scraper/log/bb-saves?n=3").json()
    assert d["target"] == "bb-saves" and [x["msg"] for x in d["lines"]] == ["line 5", "line 6", "line 7"]
    assert c.get("/api/scraper/log/bb-saves?n=0").json()["n"] == 1
    assert c.get("/api/scraper/log/bb-saves?n=99999").json()["n"] == 1000
    assert c.get("/api/scraper/log/nobody").json()["lines"] == []
    (scfg.scraper_data_dir / "secret.log").write_text('{"ts": 1, "msg": "outside logs/"}\n')
    assert scraper_targets.log(scfg, "../secret", 5)["lines"] == []        # never read outside logs/
    r = c.post("/api/actions/scraper/targets/bb-saves/remove", headers=H, json={"confirm": "bb-saves"})
    assert r.status_code == 200
    assert not (scfg.scraper_data_dir / "logs" / "bb-saves.log").exists()
```

- [ ] **Step 2: Run them and see them fail.**
Run: `cd /home/callum/Projects/puntmore-jobs/hub && ~/.venvs/puntmore/bin/python -m pytest tests/test_web_scraper.py -q`
Expected: FAIL. `days` is refused as not editable, and the log route answers 404.

- [ ] **Step 3: Implement the Interfaces.** `_clean` already passes lists through, and `field_of` finds `days` and `until` once they are in `EDITABLE`.

- [ ] **Step 4: Run the whole Hub suite.**
Run: `cd /home/callum/Projects/puntmore-jobs/hub && ~/.venvs/puntmore/bin/python -m pytest -q`
Expected: 179 + 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/callum/Projects/puntmore-jobs
git add hub/puntmore_hub/web/scraper_targets.py hub/puntmore_hub/web/app.py hub/tests/test_web_scraper.py
git commit -F - <<'EOF'
Console: job days, end date and log routes

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

### Task 4: Job tiles

**Files:**
- Modify: `hub/puntmore_hub/web/static/index.html` (the Targets heading and hint become Jobs; both forms gain days and until)
- Modify: `hub/puntmore_hub/web/static/app.js` (`renderTargets` draws tiles; `TARGET_STATE`; `EDIT_FIELDS`; `fieldValue`; the add and edit submits; `showLog`)
- Modify: `hub/puntmore_hub/web/static/style.css` (`.jobs`, `.job`, the tile's log `pre`, `.days`)
- Modify: `hub/README.md` (the "Scraper tab" paragraph: tiles, days, end date, log route), `hub/AGENTS.md` ("Where to change what": "Add or pause a Scraper job (days, end date)…")
- Test: `hub/tests/test_web_scraper.py`

**Interfaces:**
- Consumes: Task 3's `view.schedule`, `view.log` and `GET /api/scraper/log/{target}?n=`.
- Produces, for the page only:
  - Each job is an `article.job` inside `div.jobs` in `data-slot="config"`.
    - **Header:** the name as today's link button (opens `latest/<name>.json`) and the state tag.
    - **Lines:** `site · url`; `view.schedule`; state and detail; "last load", "last read" (`view.last_ok`) and "rows sent" (`view.rows_posted`).
    - **Log:** the newest lines from `view.log`, in a `pre`, as `HH:MM:SS msg`. An "all lines" button loads `?n=200` into that `pre`.
    - **Actions:** enable/disable, edit and remove, as today.
  - `TARGET_STATE` gains `finished: "off"`. `EDIT_FIELDS` gains `"days"` and `"until"`.
  - Each form gains:
    - `<fieldset class="days"><legend>days</legend>` with seven `<label><input type="checkbox" name="days" value="mon"> Mon</label>` … `sun`, then `<span class="field-error" data-err="days"></span>`;
    - `<label>until <input name="until" type="date"><span class="field-error" data-err="until"></span></label>`.
  - The ticked days are sent as a list in week order. None ticked sends `null`: no days on add, cleared on edit.

- [ ] **Step 1: Write the failing test.** Extend `test_the_page_has_the_tab_and_no_inline_script`:

```python
    assert page.count('name="until" type="date"') == 2
    for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        assert page.count(f'name="days" value="{day}"') == 2
    js = client(scfg).get("/static/app.js").text
    assert "/api/scraper/log/" in js and 'finished: "off"' in js
    assert ".job" in client(scfg).get("/static/style.css").text
```

- [ ] **Step 2: Run it and see it fail.**
Run: `cd /home/callum/Projects/puntmore-jobs/hub && ~/.venvs/puntmore/bin/python -m pytest tests/test_web_scraper.py -q`
Expected: FAIL on the `until` count.

- [ ] **Step 3: Build the tiles and the two form fields** (Interfaces). Keep `el()` and `fill()` (no `innerHTML` with data) and no inline script. On a phone (under 700 px) the tiles stack one per row.

- [ ] **Step 4: Run the Hub suite, then look at the page.**
Run: `cd /home/callum/Projects/puntmore-jobs/hub && ~/.venvs/puntmore/bin/python -m pytest -q`
Expected: all pass.
Then start the Console on a scratch data dir: a `targets.toml` with the `scfg` fixture's `TOML`, and a few lines written with `scraper.joblog.write`. Point `PUNTMORE_SCRAPER_DATA` at that dir and run `~/.venvs/puntmore/bin/python -m puntmore_hub.web` on a free port (`hub/README.md` gives the port and token settings). Check that both jobs show as tiles, that editing days and an end date on one changes its schedule line, and that "all lines" shows its log.

- [ ] **Step 5: The docs** in the Files list. Then check that nothing still calls the tab a table:
Run: `grep -rn "Targets table\|its table is\|a table of targets" --include=*.md . | grep -v "docs/superpowers/\|docs/decisions.md"`
Expected: no output. Then commit:

```bash
cd /home/callum/Projects/puntmore-jobs
git add hub/puntmore_hub/web/static/index.html hub/puntmore_hub/web/static/app.js hub/puntmore_hub/web/static/style.css \
  hub/README.md hub/AGENTS.md hub/tests/test_web_scraper.py
git commit -F - <<'EOF'
Console: the Scraper's jobs as tiles

Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB
EOF
```

---

## Self-review

- **Spec coverage.**
  - J1 (days, until, active, the midnight rule, `finished`): Task 1.
  - J2 (the log file, its bound, the runner's lines, `targets log`, removal): Task 2.
  - J3 (tiles, schedule in words, state, the three figures, the log with more on demand, the actions, the form fields, the routes, changes only through `scraper.targets`): Tasks 3–4.
  - J4 (HubAI's flags and `targets log`): the SKILL.md steps in Tasks 1–2.
  - Docs: in each task.
- **Step scan.** Every code step names its file and its signatures, and gives the values the spec pins: weekday names, 512 KB, 1000 lines, the words format and the route. The bodies are left to the builder. No step says "handle" without saying what.
- **Type consistency.** These names are the same in every task that uses them: `days` (a tuple on `Target`, a list in mappings and JSON), `until` (`str | None`), `schedule.inactive`, `targets.schedule_words`, `"schedule"`, `joblog.read`/`write`/`remove`/`path`, `targets.remove(..., data_dir=)`, `LOG_TAIL`, `LOG_MAX` and `view.log`.
- **Review Focus.** Lines 1–2 are tested in Task 1, lines 3–5 in Task 2, and line 5's route in Task 3.
- **Proportion.** The code blocks are tests. The bodies of `inactive`, `joblog`, the routes and the tiles are the builder's.
