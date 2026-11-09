# AW 26: Render and the Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render stores slip photos in a queue, takes the fuller slip record Qwen will send, answers one day at a time, and the AW 26 page becomes Summary and Slips in the Paddy styling.

**Architecture:** This is step 2 of the design's order of work, the first of three plans (this one; then the Hub commands, photo loop and runbook; then results). Render stays a thin page over rows: every sum is done when a request asks, nothing runs in the background, and no photo is read. New behaviour sits in `render/api/app/aw26/` beside `service.py`; the page is rebuilt from `CardsPage.module.css`. Marking a selection is brought forward from the design's step 4 because the Summary's hand-set result needs it; "games due a result" stays in the third plan.

**Tech Stack:** FastAPI, SQLAlchemy on SQLite, pytest; React (Vite), CSS modules, vitest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-09-aw26-qwen-design.md`. The approved look is `docs/superpowers/specs/2026-11-09-aw26-qwen-mock.png`.

**Built by:** Opus 5.5 subagents at high effort, one task at a time.

## Global Constraints

- `docs/decisions.md` is the register and wins. Its edits for this plan are Task 10, in this pull request.
- Render never loads a bookmaker page, never places a bet, reads no photo and runs nothing in the background for AW 26: no scheduler job, no OCR provider call.
- `render/api/app/aw26/offers.py` is not changed. EV and returns are worked out as they are today.
- Never commit secrets. No stealth or fingerprint tooling.
- Frontend: add nothing to the page that this plan or the spec does not name (`render/frontend/AGENTS.md`).
- Start date: `START = date(2026, 11, 9)`. A slip placed before it (London) stays stored and is left out of every figure, every list and every mark. Nothing is deleted.
- All days are London days. Times are stored naive UTC.
- Targets: £1,000 EV a day (`EV_PER_DAY_TARGET`), £100,000 profit (`PROFIT_TARGET`).
- Results are `pending | won | lost | void`. Offers are `refund | boost | other`.
- Colours: band `#004833`; won `var(--good)`; lost `var(--red)` (it is `#b91c1c`).
- Exact words on the page: "AW 26", "all-time profit", "To date", "Waiting for Qwen", "Qwen could not read this", "Send again", "Delete", "Dismiss", "Summary", "Slips".
- Every AW 26 module reads the clock as `service.utcnow()` (`from . import service`), so a test pins it with one patch. No test depends on the real date.
- API tests: from `render/api/`, `/home/callum/Projects/puntmore/api/.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (863 passed, 1 skipped before this plan). Frontend: from `render/frontend/`, `npm ci` once, then `npm test && npm run lint && npm run build`.
- Work only in the worktree `/home/callum/Projects/puntmore-aw26`, branch `aw26-qwen`. Never `git stash`. Commits end with the two attribution lines the controller gives.

## Review Focus

1. A slip placed late in the evening in summer time (23:30 UTC on 1 June is 2 June in London) lands on the London day. Test in Task 4.
2. Results typed late or out of order: profit stays on the day of the game that decided the slip and does not move when later legs get results. Test in Task 2.
3. A leg with an assumed fair whose odds are then corrected: the fair follows the odds, so the correction cannot create EV. Test in Task 1.
4. A batch upload with one bad file (not an image, or empty): the whole batch is refused and nothing is stored. A photo deleted while Qwen is reading it: the record that follows stores no slip. Tests in Task 5.
5. All-time profit below zero, or no slips at all: the headline shows a minus sign, the bar is empty, and no "NaN" or "Infinity" reaches the page. Test in Task 7.

## File Structure

| File | Responsibility |
|---|---|
| `render/api/app/models.py` | `AwLeg.fair_assumed`; new `AwPhoto` |
| `render/api/app/security.py` | `ensure_schema`: add `aw_legs.fair_assumed` to an existing database |
| `render/api/app/schemas.py` | leg fields in, `fair_assumed` out, photo and summary shapes |
| `render/api/app/aw26/service.py` | build, price, settle; days; the day's figures |
| `render/api/app/aw26/selections.py` (new) | which legs are the same selection; grouping; marking |
| `render/api/app/aw26/photos.py` (new) | the photo queue's states |
| `render/api/app/aw26/record.py` | record a slip, with or without a photo |
| `render/api/app/routers/aw26.py` | the routes |
| `render/api/app/apikeys.py` | `grant` |
| `render/api/tests/aw26_kit.py` (new) | shared test set-up and builders |
| `render/api/tests/test_aw26*.py` | tests, one file per area |
| `render/frontend/src/aw26.js` (new) + `aw26.test.js` | the page's sums and labels, pure |
| `render/frontend/src/api.js` | the calls |
| `render/frontend/src/pages/Aw26Page.jsx` | shell: band, day pill, switch, data |
| `render/frontend/src/pages/Aw26Summary.jsx` (new) | the Summary screen |
| `render/frontend/src/pages/Aw26Slips.jsx` (new) | the Slips screen |
| `render/frontend/src/pages/Aw26Page.module.css` | only what `CardsPage.module.css` lacks |

---

### Task 1: The fuller leg record

**Files:**
- Create: `render/api/tests/aw26_kit.py`
- Modify: `render/api/app/models.py` (`AwLeg`), `render/api/app/security.py` (`ensure_schema`), `render/api/app/schemas.py` (`AwLegOut`, `AwSlipLegIn`), `render/api/app/aw26/service.py` (`build_slip`), `render/api/app/routers/aw26.py` (`edit_slip`)
- Test: `render/api/tests/test_aw26.py`

**Interfaces:**
- Produces: `AwLeg.fair_assumed` (Boolean, not null, default False, `server_default="0"`, the last column). `AwSlipLegIn` gains `kind: Literal["corners","cards","goals","saves","other"] | None`, `line: float | None` (0 to 100), `side: Literal["over","under"] | None` (input lower-cased first, so the board's "Over" is accepted), `fixture_id: str | None` (max 80), `fair_assumed: bool = False`. `AwLegOut` gains `fair_assumed: bool`.
- Produces: `tests/aw26_kit.py` exporting `engine`, `_db`, the autouse fixture `setup`, `LEGS`, `ODDS`, `FAIRS`, `_body`, `_record`, `_edit_body`, `_png` (all moved from `test_aw26.py`; `setup` additionally pins the clock at `datetime(2026, 11, 9, 13, 0)` through `clock`), plus:
  - `leg(event, market, selection, odds, fair=None, **extra) -> dict` (a leg body; `extra` passes `kickoff`, `kind`, `line`, `side`, `fixture_id`, `fair_assumed`)
  - `slip_body(receipt, legs, offer="other", stake=10, placed_at="09/11/2026 12:00", **extra) -> dict`
  - `clock(monkeypatch, when: datetime) -> None`, which patches `app.aw26.service.utcnow` to return `when`
  Every AW 26 test module imports what it uses by name, `setup` included (an autouse fixture imported into a module applies there).

- [ ] **Step 1: Move the shared set-up into `aw26_kit.py`**, add the three builders, import them in `test_aw26.py`. Run `pytest tests/test_aw26.py -q`: 22 passed, as before.

- [ ] **Step 2: Write the failing tests** in `test_aw26.py`

```python
def test_a_leg_keeps_what_qwen_read_about_it():
    client = TestClient(app)
    body = slip_body("K1", [leg("Arsenal v Chelsea", None, "Over 10.5 corners", 1.80, 1.72,
                                kind="corners", line=10.5, side="Over", fixture_id="E1",
                                kickoff="2026-11-09T15:00:00Z")])
    lg = client.post("/aw26/slips/record", json=body).json()["legs"][0]
    assert (lg["kind"], lg["line"], lg["side"], lg["fixture_id"]) == ("corners", 10.5, "over", "E1")
    assert lg["fair_assumed"] is False and lg["fair_odds"] == 1.72


def test_an_assumed_fair_is_the_odds_and_adds_nothing_of_its_own():
    client = TestClient(app)
    # A fair sent alongside "assumed" is ignored: assumed means equal to the odds.
    one = slip_body("A1", [leg("Leeds v Fulham", None, "Over 3.5 Leeds cards", 1.90, 1.50, fair_assumed=True)])
    s = client.post("/aw26/slips/record", json=one).json()
    assert s["legs"][0]["fair_odds"] == 1.90 and s["legs"][0]["fair_assumed"] is True
    assert s["ev"] == 0
    # A refund 5-fold of assumed fairs still carries the refund's own value.
    five = slip_body("A2", [leg(e, m, sel, o, fair_assumed=True) for e, m, sel, o, _ in LEGS],
                     offer="refund", stake=50)
    assert client.post("/aw26/slips/record", json=five).json()["ev"] == round(expected_value("refund", 50, ODDS, ODDS), 2)


def test_correcting_an_assumed_legs_odds_moves_its_fair_and_typing_a_fair_makes_it_real():
    client = TestClient(app)
    s = client.post("/aw26/slips/record", json=slip_body(
        "A3", [leg("Leeds v Fulham", None, "Over 3.5 Leeds cards", 1.90, fair_assumed=True)])).json()
    s = client.patch(f"/aw26/slips/{s['id']}", json=_edit_body(s, {0: {"odds": 2.10}})).json()
    assert (s["legs"][0]["fair_odds"], s["legs"][0]["fair_assumed"], s["ev"]) == (2.10, True, 0)
    s = client.patch(f"/aw26/slips/{s['id']}", json=_edit_body(s, {0: {"fair_odds": 2.00}})).json()
    assert (s["legs"][0]["fair_odds"], s["legs"][0]["fair_assumed"]) == (2.00, False)
    assert s["ev"] == round(expected_value("other", 10, [2.10], [2.00]), 2)


def test_an_old_database_gains_the_fair_assumed_column(tmp_path, monkeypatch):
    from app import security
    old = create_engine(f"sqlite:///{tmp_path}/old.db")
    with old.begin() as c:
        c.execute(text("CREATE TABLE aw_legs (id INTEGER PRIMARY KEY, slip_id INTEGER, event VARCHAR)"))
        c.execute(text("INSERT INTO aw_legs (id, slip_id, event) VALUES (1, 1, 'A v B')"))
    monkeypatch.setattr(security, "engine", old)
    security.ensure_schema()
    security.ensure_schema()                       # twice: it is idempotent
    with old.begin() as c:
        assert c.execute(text("SELECT fair_assumed FROM aw_legs WHERE id = 1")).scalar() == 0
```

Also change the pinned production tables in the same file: append `"fair_assumed"` to `TABLES["aw_legs"]` and add it to `NOT_NULL["aw_legs"]`, and reword the comment above them: one column is added on purpose (9 Nov 2026), by `ensure_schema`.

- [ ] **Step 3: Run them.** `pytest tests/test_aw26.py -q`. Expected: the four new tests and the pinned-tables test fail.

- [ ] **Step 4: Implement.**
  - `AwLeg.fair_assumed` as in Interfaces.
  - `ensure_schema`: when table `aw_legs` exists without the column, `ALTER TABLE aw_legs ADD COLUMN fair_assumed BOOLEAN NOT NULL DEFAULT 0`, in the style of the blocks already there. If the old-database test trips over another block that assumes its table exists, guard that block; do not weaken the test.
  - `build_slip` copies `kind`, `line`, `side`, `fixture_id`, `fair_assumed` onto each leg; when `fair_assumed`, the leg's `fair_odds` is its `odds` whatever the body carried. Update its docstring (the "stay NULL" sentence is no longer true).
  - `edit_slip`, per existing leg, before the new values are written: if the incoming `fair_odds` differs from the stored one, the leg is no longer assumed; otherwise, if the leg is assumed, its fair becomes the incoming odds. A leg added by the edit is not assumed.

- [ ] **Step 5: Run the whole API suite.** Expected: all pass, 4 more than before.

- [ ] **Step 6: Commit** `AW 26: a leg carries its kind, line, side, event id and "fair assumed"`.

---

### Task 2: Start date, London days, and when a slip was decided

**Files:**
- Modify: `render/api/app/aw26/service.py`
- Test: `render/api/tests/test_aw26_days.py` (new)

**Interfaces:**
- Produces, in `service.py`:
  - `START = date(2026, 11, 9)`
  - `day_bounds(day: date) -> tuple[datetime, datetime]`: naive UTC start and end (exclusive) of that London day
  - `decided_at(slip: AwSlip) -> datetime | None`: the kick-off of the game that decided the slip
  - `settle_slip(slip, now=None)` unchanged in signature; its dating rule changes (below)

- [ ] **Step 1: Write the failing tests**

```python
TUE1, TUE2, THU = "2026-11-10T15:00:00Z", "2026-11-10T19:45:00Z", "2026-11-12T19:45:00Z"
SAT = datetime(2026, 11, 14, 9, 0)


def _refund(kickoffs):
    body = slip_body("R1", [leg(e, m, sel, o, f, kickoff=k)            # the kit's qualifying 5-fold
                            for (e, m, sel, o, f), k in zip(LEGS, kickoffs)], offer="refund", stake=50)
    return build_slip(AwSlipRecordIn(**body), user="t", now=datetime(2026, 11, 9, 12, 0))


def test_day_bounds_are_london_days():
    assert day_bounds(date(2026, 11, 9)) == (datetime(2026, 11, 9, 0, 0), datetime(2026, 11, 10, 0, 0))
    assert day_bounds(date(2027, 6, 2)) == (datetime(2027, 6, 1, 23, 0), datetime(2027, 6, 2, 23, 0))


def test_profit_lands_on_the_deciding_games_day_whenever_the_result_is_typed():
    slip = _refund([TUE1, TUE2, THU, THU, THU])
    slip.legs[0].result = slip.legs[1].result = "lost"
    settle_slip(slip, now=SAT)                                   # typed four days late
    assert slip.status == "lost" and slip.settled_at == datetime(2026, 11, 10, 19, 45)
    for lg in slip.legs[2:]:
        lg.result = "won"
    settle_slip(slip, now=SAT)
    assert slip.settled_at == datetime(2026, 11, 10, 19, 45)     # later results move nothing


def test_the_order_results_are_typed_in_does_not_matter():
    slip = _refund([TUE1, TUE2, THU, THU, THU])
    for lg in slip.legs[2:]:
        lg.result = "won"
    settle_slip(slip, now=SAT)
    assert slip.status == "open" and slip.settled_at is None
    slip.legs[0].result = slip.legs[1].result = "lost"
    settle_slip(slip, now=SAT)
    assert slip.settled_at == datetime(2026, 11, 10, 19, 45)     # two losers on Tuesday decided it


def test_a_refunded_slip_is_decided_by_its_last_game():
    slip = _refund([TUE1, TUE2, THU, THU, THU])
    slip.legs[0].result = "lost"
    for lg in slip.legs[1:]:
        lg.result = "won"
    settle_slip(slip, now=SAT)
    assert slip.status == "refunded" and slip.settled_at == datetime(2026, 11, 12, 19, 45)


def test_a_result_on_a_leg_with_no_kickoff_dates_the_slip_by_when_it_was_recorded():
    slip = _refund([None, TUE2, THU, THU, THU])
    slip.legs[0].result = slip.legs[1].result = "lost"
    settle_slip(slip, now=SAT)
    assert slip.settled_at == SAT
    settle_slip(slip, now=datetime(2026, 11, 20, 9, 0))
    assert slip.settled_at == SAT                                # and it stays there
    slip.legs[0].result = "pending"
    settle_slip(slip, now=SAT)
    assert slip.status == "open" and slip.settled_at is None
```

- [ ] **Step 2: Run them.** `pytest tests/test_aw26_days.py -q`. Expected: fail on the missing names.

- [ ] **Step 3: Implement.**
  - `decided_at`: if any leg that has a result has no kick-off, `None`. Otherwise replay: take the kick-offs of the legs that have a result, earliest first; for each, run `offers.settle` with only the legs kicking off at or before it keeping their results and every other leg `pending`; the first kick-off at which the status is not `open` is the answer.
  - `settle_slip`: `settled_at` is `None` while open; otherwise `decided_at(slip)`, and where that is `None`, the `settled_at` already stored, else `now`.

- [ ] **Step 4: Run the whole API suite.** Expected: all pass.

- [ ] **Step 5: Commit** `AW 26: a slip is dated by the game that decided it`.

---

### Task 3: The same selection across slips, and marking it

**Files:**
- Create: `render/api/app/aw26/selections.py`
- Modify: `render/api/app/routers/aw26.py`, `render/api/app/schemas.py`
- Test: `render/api/tests/test_aw26_selections.py` (new)

**Interfaces:**
- Consumes: `service.START`, `service.day_bounds`, `service.london_day`, `service.settle_slip`.
- Produces, in `selections.py`:
  - `game_key(leg) -> str`: `"e:" + fixture_id` when the leg has one, else `"w:" + words(event)`
  - `selection_key(leg) -> str`: `game_key(leg) + "|" + words(market + " " + selection)`
  - `words(text) -> str`: lower case, `" vs "` and `" v. "` read as `" v "`, runs of white space collapsed, trimmed
  - `group(legs) -> list[dict]`: one dict per selection, ordered by kick-off (none last) then event, with keys `leg_id` (the lowest leg id in the group), `key`, `game`, `event`, `market`, `selection` (those three from that lowest leg), `kickoff` (the earliest known), `day` (ISO London day of `kickoff`, or of the slip's `placed_at` when there is none), `slips` (how many distinct slips), `odds` (mean of the known odds, 2 places, or `None`), `result` (the legs' result when they all agree, else `"pending"`), `fair_assumed` (true if any leg's is)
  - `mark(db, leg_id: int, result: str) -> tuple[int, int]`: sets `result` on every leg of the same selection on slips placed on or after `START`, re-settles each of those slips, commits; returns (legs changed, slips touched). Raises `LookupError` when the leg does not exist or its slip is before `START`.
- Produces the route `POST /aw26/selections/{leg_id}/result`, body `{"result": "won"}`, answer `{"legs": 3, "slips": 3}`; 404 for `LookupError`, 400 for a result outside `RESULTS`.

- [ ] **Step 1: Write the failing tests**

```python
CORNERS = dict(market=None, selection="Over 10.5 corners", odds=1.80, fair=1.72)


def _one(client, receipt, event, odds=1.80, selection="Over 10.5 corners", **extra):
    return client.post("/aw26/slips/record", json=slip_body(
        receipt, [leg(event, None, selection, odds, 1.72, **extra)])).json()


def test_one_mark_settles_the_selection_on_every_slip_that_carries_it():
    client = TestClient(app)
    a = _one(client, "S1", "Arsenal v Chelsea")
    b = _one(client, "S2", "arsenal  vs  chelsea")               # the same game, typed differently
    c = _one(client, "S3", "Arsenal v Chelsea", selection="Over 9.5 corners")
    r = client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "won"})
    assert r.status_code == 200 and r.json() == {"legs": 2, "slips": 2}
    day = client.get("/aw26/slips?day=2026-11-09").json()["slips"]
    by = {s["receipt"]: s for s in day}
    assert (by["S1"]["status"], by["S1"]["returns"]) == ("won", 18.0)
    assert by["S2"]["status"] == "won" and by["S3"]["status"] == "open"
    # A mark is not a correction: the fair and the EV stand.
    assert by["S1"]["legs"][0]["fair_odds"] == 1.72 and by["S1"]["ev"] == a["ev"] and by["S1"]["edited"] is False


def test_an_event_id_groups_legs_whatever_their_words_for_the_game():
    client = TestClient(app)
    a = _one(client, "S1", "Arsenal v Chelsea", fixture_id="E1")
    _one(client, "S2", "Arsenal vs Chelsea FC", fixture_id="E1")
    _one(client, "S3", "Arsenal v Chelsea", fixture_id="E2")
    r = client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "lost"})
    assert r.json() == {"legs": 2, "slips": 2}


def test_a_slip_from_before_the_start_is_never_marked():
    client = TestClient(app)
    old = client.post("/aw26/slips/record", json=slip_body(
        "OLD", [leg("Arsenal v Chelsea", None, "Over 10.5 corners", 1.80, 1.72)], placed_at="08/11/2026 12:00")).json()
    new = _one(client, "S1", "Arsenal v Chelsea")
    assert client.post(f"/aw26/selections/{new['legs'][0]['id']}/result", json={"result": "won"}).json() == {"legs": 1, "slips": 1}
    assert client.post(f"/aw26/selections/{old['legs'][0]['id']}/result", json={"result": "won"}).status_code == 404


def test_a_bad_result_or_a_missing_leg_is_refused():
    client = TestClient(app)
    a = _one(client, "S1", "Arsenal v Chelsea")
    assert client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "maybe"}).status_code == 400
    assert client.post("/aw26/selections/999/result", json={"result": "won"}).status_code == 404


def test_group_averages_the_price_and_counts_the_slips():
    client = TestClient(app)
    _one(client, "S1", "Arsenal v Chelsea", odds=1.80, kickoff="2026-11-09T15:00:00Z")
    _one(client, "S2", "Arsenal v Chelsea", odds=1.90, kickoff="2026-11-09T15:00:00Z", fair_assumed=True)
    db = next(_db())
    (g,) = selections.group(db.query(AwLeg).all())
    assert (g["slips"], g["odds"], g["result"], g["fair_assumed"], g["day"]) == (2, 1.85, "pending", True, "2026-11-09")
    assert g["game"] == "w:arsenal v chelsea" and g["kickoff"] == datetime(2026, 11, 9, 15, 0)
```

`GET /aw26/slips` takes `day` from Task 4; until then the parameter is ignored and the route answers every slip, so the first test passes as written in both tasks.

- [ ] **Step 2: Run them.** Expected: fail on the missing module and route.

- [ ] **Step 3: Implement** `selections.py` and the route as in Interfaces. `mark` narrows in SQL by `fixture_id` (equal, or null when the leg has none) and by `AwSlip.placed_at >= day_bounds(START)[0]`, then compares keys in Python. It does not touch `ev`, `fair_odds` or `edited`.

- [ ] **Step 4: Run the whole API suite.** Expected: all pass.

- [ ] **Step 5: Commit** `AW 26: one mark settles a selection on every slip`.

---

### Task 4: One day at a time

**Files:**
- Modify: `render/api/app/aw26/service.py`, `render/api/app/routers/aw26.py`, `render/api/app/schemas.py`
- Test: `render/api/tests/test_aw26_days.py`, `render/api/tests/test_aw26.py`, `render/api/tests/test_aw26_selections.py`

**Interfaces:**
- Consumes: `selections.group`, `service.START`, `day_bounds`, `london_day`, `profit`.
- Produces `service.day_summary(slips, selections, day: date, today: date) -> dict`:

```python
{"day": "2026-11-09", "today": "2026-11-11", "first_day": "2026-11-09",
 "days": 2,                                   # days on which a slip was placed
 "targets": {"ev_per_day": 1000.0, "profit": 100000.0},
 "chosen":  {"ev": 0.0, "ev_target": 1000.0, "profit": 0.0, "staked": 0.0, "slips": 0, "open": 0},
 "to_date": {"ev": 0.0, "ev_target": 2000.0, "profit": 0.0, "staked": 0.0, "slips": 0, "open": 0},
 "selections": [...]}                         # selections.group's dicts
```

  `chosen`: EV, staked, slips and open over the slips placed on `day`; profit over the slips decided on `day` (`settled_at`). `to_date`: everything; `ev_target` is `days × 1000`. The headline is `to_date.profit`. The figures read slip columns only and never touch `slip.legs`.
- Produces `GET /aw26/summary?day=YYYY-MM-DD` (default today) returning that dict (`AwSummaryOut`, a mapping like the old one). A day before `START` or after today is refused, 400. The selections are the legs whose day (kick-off day, or the slip's placed day when there is no kick-off) is `day`; when `day` is today, that day and every later one.
- Changes `GET /aw26/slips` to `GET /aw26/slips?day=YYYY-MM-DD` (default today): `{"slips": [...]}`, the slips placed on that London day, newest first. `AwSlipsOut` loses `summary`.
- Removes `service.summary` and `service._totals`.

- [ ] **Step 1: Write the failing tests** in `test_aw26_days.py`

```python
def _single(client, receipt, placed, kickoff, odds=2.0, fair=1.9, event="Arsenal v Chelsea"):
    return client.post("/aw26/slips/record", json=slip_body(
        receipt, [leg(event, None, "Over 10.5 corners", odds, fair, kickoff=kickoff)], placed_at=placed)).json()


def test_each_figure_counts_on_its_own_day(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 11, 22, 0))
    client = TestClient(app)
    a = _single(client, "D1", "09/11/2026 12:00", "2026-11-11T19:45:00Z")
    client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "won"})
    mon = client.get("/aw26/summary?day=2026-11-09").json()
    assert mon["chosen"] == {"ev": a["ev"], "ev_target": 1000.0, "profit": 0.0, "staked": 10.0, "slips": 1, "open": 0}
    assert mon["selections"] == []                               # the game is on Wednesday
    wed = client.get("/aw26/summary").json()
    assert wed["day"] == wed["today"] == "2026-11-11"
    assert wed["chosen"] == {"ev": 0.0, "ev_target": 1000.0, "profit": 10.0, "staked": 0.0, "slips": 0, "open": 0}
    assert wed["to_date"] == {"ev": a["ev"], "ev_target": 1000.0, "profit": 10.0, "staked": 10.0, "slips": 1, "open": 0}
    assert [s["result"] for s in wed["selections"]] == ["won"]


def test_a_day_is_a_day_on_which_a_slip_was_placed(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 14, 12, 0))
    client = TestClient(app)
    _single(client, "D1", "09/11/2026 12:00", None)
    _single(client, "D2", "09/11/2026 18:00", None)
    _single(client, "D3", "12/11/2026 12:00", None)
    s = client.get("/aw26/summary").json()
    assert s["days"] == 2 and s["to_date"]["ev_target"] == 2000.0 and s["to_date"]["slips"] == 3


def test_slips_from_before_the_start_count_nowhere(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 9, 12, 0))
    client = TestClient(app)
    _single(client, "OLD", "08/11/2026 12:00", "2026-11-09T15:00:00Z")
    s = client.get("/aw26/summary").json()
    assert s["days"] == 0 and s["to_date"]["slips"] == 0 and s["selections"] == []
    assert client.get("/aw26/slips?day=2026-11-09").json() == {"slips": []}
    assert client.get("/aw26/summary?day=2026-11-08").status_code == 400
    assert client.get("/aw26/summary?day=2026-11-10").status_code == 400     # tomorrow


def test_today_lists_from_today_forward_and_an_earlier_day_lists_itself(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 10, 12, 0))
    client = TestClient(app)
    _single(client, "D1", "09/11/2026 12:00", "2026-11-09T15:00:00Z", event="Mon v Day")
    _single(client, "D2", "09/11/2026 12:00", "2026-11-10T15:00:00Z", event="Tue v Day")
    _single(client, "D3", "09/11/2026 12:00", "2026-11-12T19:45:00Z", event="Thu v Day")
    _single(client, "D4", "10/11/2026 09:00", None, event="No v Kickoff")
    events = lambda day: [s["event"] for s in client.get(f"/aw26/summary{day}").json()["selections"]]
    assert events("") == ["Tue v Day", "Thu v Day", "No v Kickoff"]
    assert events("?day=2026-11-09") == ["Mon v Day"]


def test_a_late_summer_evening_belongs_to_the_london_day(monkeypatch):
    clock(monkeypatch, datetime(2027, 6, 2, 9, 0))
    client = TestClient(app)
    _single(client, "BST", "2027-06-01T23:30:00Z", None)          # 00:30 on 2 June in London
    assert len(client.get("/aw26/slips?day=2027-06-02").json()["slips"]) == 1
    assert client.get("/aw26/slips?day=2027-06-01").json() == {"slips": []}
    assert client.get("/aw26/summary").json()["chosen"]["slips"] == 1


def test_a_losing_run_and_an_empty_ledger_are_plain_numbers(monkeypatch):
    clock(monkeypatch, datetime(2026, 11, 9, 22, 0))
    client = TestClient(app)
    empty = client.get("/aw26/summary").json()
    assert empty["days"] == 0 and empty["to_date"] == {"ev": 0.0, "ev_target": 0.0, "profit": 0.0, "staked": 0.0, "slips": 0, "open": 0}
    a = _single(client, "L1", "09/11/2026 12:00", "2026-11-09T15:00:00Z")
    client.post(f"/aw26/selections/{a['legs'][0]['id']}/result", json={"result": "lost"})
    assert client.get("/aw26/summary").json()["to_date"]["profit"] == -10.0
```

- [ ] **Step 2: Rewrite the tests the change breaks.** In `test_aw26.py`: `test_a_recorded_slip_is_listed_priced_and_counted` reads the figures from `GET /aw26/summary` (`to_date`: slips 1, staked 50, the EV; `chosen.open` 1) and the list from `GET /aw26/slips`; `test_summary_counts_days_from_the_first_slip_against_the_targets` is deleted (the day-count test above replaces it); any other read of `["summary"]` moves the same way.

- [ ] **Step 3: Run them.** Expected: the new tests fail (no route, no `day` filter).

- [ ] **Step 4: Implement** as in Interfaces. Both routes query by `AwSlip.placed_at` between UTC bounds from `day_bounds`; the summary's selections come from one query of legs joined to their slips, filtered in SQL by the window and by the start date.

- [ ] **Step 5: Run the whole API suite.** Expected: all pass.

- [ ] **Step 6: Commit** `AW 26: the summary and the slips for one day`.

---

### Task 5: The photo queue

**Files:**
- Create: `render/api/app/aw26/photos.py`, `render/api/tests/test_aw26_photos.py`
- Modify: `render/api/app/models.py` (new `AwPhoto`), `render/api/app/schemas.py`, `render/api/app/aw26/record.py`, `render/api/app/routers/aw26.py`
- Test: also `render/api/tests/test_aw26.py` (the "reads no photo" and grant tests)

**Interfaces:**
- Produces the table `aw_photos` (`AwPhoto`): `id`, `created_at`, `user` (not null), `image_path` (not null), `source` (`page | telegram`, not null), `state` (`waiting | reading | failed | duplicate | done`, not null, default `waiting`, indexed), `taken_at`, `tries` (not null, default 0), `reason`, `slip_id` (FK `aw_slips.id`, `ondelete="SET NULL"`). A new table is created by `create_all` at boot; `ensure_schema` needs nothing.
- Produces `photos.py`: `MAX_TRIES = 3`, `STALE_AFTER = timedelta(minutes=15)`, `MAX_BATCH = 20`, and
  - `store(db, data: bytes, media_type: str, *, user: str, source: str) -> AwPhoto` (the file goes under `settings.upload_dir` as `uuid4().hex` plus the type's extension, after `images.prepare_for_ocr` has turned it upright; nothing reads it)
  - `take(photo) -> None`: `waiting` becomes `reading`, `taken_at` now, `tries + 1`; anything else raises `record.Refused(409, ...)`
  - `fail(photo, reason: str) -> None`: keeps the reason; back to `waiting` while `tries < MAX_TRIES`, else `failed`
  - `release_stale(db) -> None`: every `reading` photo taken more than `STALE_AFTER` ago is failed with "Qwen did not finish"
  - `resend(photo) -> None`: `failed` becomes `waiting`, tries 0, reason cleared; anything else `Refused(409, ...)`
- Produces the routes, all under the router's `aw26` grant:

| Route | Does | Answers |
|---|---|---|
| `POST /aw26/photos` (multipart: `files` repeated, `source` default `page`) | stores each photo, state `waiting` | `list[AwPhotoOut]`; 400 for a type outside `upload.ALLOWED_MEDIA_TYPES`, an empty file, more than `MAX_BATCH` files or a bad `source`, and then nothing is stored |
| `GET /aw26/photos` | `release_stale`, then every photo not `done`, oldest first | `list[AwPhotoOut]` |
| `POST /aw26/photos/{id}/take` | `take` | `AwPhotoOut`; 409 |
| `GET /aw26/photos/{id}/image` | the file | `FileResponse`; 404 |
| `POST /aw26/photos/{id}/fail` (`{"reason": "..."}`, 1 to 200 characters) | `fail`, from `waiting` or `reading` | `AwPhotoOut`; 409 otherwise |
| `POST /aw26/photos/{id}/resend` | `resend` | `AwPhotoOut`; 409 |
| `DELETE /aw26/photos/{id}` | removes the row and its file | `DeletedOut`; 409 when `done` (the slip owns the file) |

  `AwPhotoOut`: `id`, `created_at`, `source`, `state`, `tries`, `reason`, `slip_id`.
- Changes `AwSlipRecordIn`: `photo_id: int | None = None`. `record_slip` with a `photo_id`:
  - no such photo: `Refused(404, "No such photo")`, nothing stored; a `done` photo: `Refused(409, ...)`
  - no receipt: the photo is failed with "no receipt number" and the record refused, 400
  - a receipt already in: the photo becomes `duplicate` with the refusal's words as its reason, then the 409 as today
  - recorded: the slip's `image_path` is the photo's, the photo is `done` with `slip_id` set, in the same commit
  There is no separate "mark duplicate" route: the record does it, so Qwen has one step fewer to get wrong (builder's call under the spec's "names and shapes").
- Changes `delete_slip`: the slip's photo rows go with it.

- [ ] **Step 1: Write the failing tests** in `test_aw26_photos.py`

```python
def _upload(client, n=1, **kw):
    files = [("files", (f"s{i}.png", _png(), "image/png")) for i in range(n)]
    return client.post("/aw26/photos", files=files, **kw)


def _legs():
    return [leg("Arsenal v Chelsea", None, "Over 10.5 corners", 1.80, 1.72)]


def test_an_upload_is_stored_waiting_and_nothing_reads_it(tmp_path):
    client = TestClient(app)
    r = _upload(client, 2)
    assert r.status_code == 200 and [p["state"] for p in r.json()] == ["waiting", "waiting"]
    assert len(os.listdir(tmp_path)) == 2
    assert [p["id"] for p in client.get("/aw26/photos").json()] == [p["id"] for p in r.json()]
    assert client.get("/aw26/slips").json() == {"slips": []}
    assert client.get(f"/aw26/photos/{r.json()[0]['id']}/image").status_code == 200


def test_one_bad_file_refuses_the_whole_batch(tmp_path):
    client = TestClient(app)
    good = ("files", ("a.png", _png(), "image/png"))
    for bad in (("files", ("b.txt", b"hello", "text/plain")), ("files", ("c.png", b"", "image/png"))):
        assert client.post("/aw26/photos", files=[good, bad]).status_code == 400
    assert _upload(client, 21).status_code == 400
    assert client.post("/aw26/photos", files=[good], data={"source": "fax"}).status_code == 400
    assert os.listdir(tmp_path) == [] and client.get("/aw26/photos").json() == []


def test_a_photo_is_handed_out_once_and_fails_three_times_before_it_stays_failed():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    assert client.post(f"/aw26/photos/{pid}/take").json()["state"] == "reading"
    assert client.post(f"/aw26/photos/{pid}/take").status_code == 409
    for tries, state in ((1, "waiting"), (2, "waiting"), (3, "failed")):
        if tries > 1:
            client.post(f"/aw26/photos/{pid}/take")
        p = client.post(f"/aw26/photos/{pid}/fail", json={"reason": "not a slip"}).json()
        assert (p["tries"], p["state"], p["reason"]) == (tries, state, "not a slip")
    assert client.post(f"/aw26/photos/{pid}/take").status_code == 409
    p = client.post(f"/aw26/photos/{pid}/resend").json()
    assert (p["state"], p["tries"], p["reason"]) == ("waiting", 0, None)


def test_a_photo_taken_and_never_finished_comes_back_after_15_minutes(monkeypatch):
    client = TestClient(app)
    clock(monkeypatch, datetime(2026, 11, 9, 12, 0))
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    clock(monkeypatch, datetime(2026, 11, 9, 12, 14))
    assert client.get("/aw26/photos").json()[0]["state"] == "reading"
    clock(monkeypatch, datetime(2026, 11, 9, 12, 16))
    p = client.get("/aw26/photos").json()[0]
    assert (p["state"], p["reason"]) == ("waiting", "Qwen did not finish")


def test_a_recorded_slip_takes_the_photo_as_its_picture(tmp_path):
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    s = client.post("/aw26/slips/record", json=slip_body("P1", _legs(), photo_id=pid)).json()
    assert s["has_image"] is True and client.get(f"/aw26/slips/{s['id']}/image").status_code == 200
    assert client.get("/aw26/photos").json() == []               # done: off the queue
    assert client.delete(f"/aw26/photos/{pid}").status_code == 409 and len(os.listdir(tmp_path)) == 1
    client.delete(f"/aw26/slips/{s['id']}")
    assert os.listdir(tmp_path) == []
    assert next(_db()).query(AwPhoto).count() == 0


def test_a_slip_already_in_marks_the_photo_a_duplicate(tmp_path):
    client = TestClient(app)
    client.post("/aw26/slips/record", json=slip_body("P1", _legs()))
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    assert client.post("/aw26/slips/record", json=slip_body("P1", _legs(), photo_id=pid)).status_code == 409
    p = client.get("/aw26/photos").json()[0]
    assert p["state"] == "duplicate" and "P1" in p["reason"]
    assert client.delete(f"/aw26/photos/{pid}").status_code == 200 and os.listdir(tmp_path) == []
    assert len(client.get("/aw26/slips").json()["slips"]) == 1


def test_a_photo_with_no_receipt_is_failed_not_recorded():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    assert client.post("/aw26/slips/record", json=slip_body(None, _legs(), photo_id=pid)).status_code == 400
    p = client.get("/aw26/photos").json()[0]
    assert (p["state"], p["reason"]) == ("waiting", "no receipt number")
    assert client.get("/aw26/slips").json() == {"slips": []}
    # By hand, with no photo, a slip without a receipt is still accepted.
    assert client.post("/aw26/slips/record", json=slip_body(None, _legs())).status_code == 200


def test_a_photo_deleted_while_qwen_reads_it_becomes_no_slip():
    client = TestClient(app)
    pid = _upload(client).json()[0]["id"]
    client.post(f"/aw26/photos/{pid}/take")
    assert client.delete(f"/aw26/photos/{pid}").status_code == 200
    assert client.post("/aw26/slips/record", json=slip_body("P9", _legs(), photo_id=pid)).status_code == 404
    assert client.get("/aw26/slips").json() == {"slips": []}
```

The reads of `GET /aw26/slips` with no day work because the kit pins the clock on 9 Nov 2026, the day of its default `placed_at`.

In `test_aw26.py`: `test_render_reads_no_photo_and_runs_no_feeder_route` keeps every assertion (`POST /aw26/slips` with a file is still 404 or 405) and its docstring now says a photo is stored on `/aw26/photos` and never read; the grant test gains `GET /aw26/photos` and `POST /aw26/photos` among the routes refused without `aw26`; the API-key test gains an upload with `data={"source": "telegram"}` on the key, answering 200 with `source == "telegram"`.

- [ ] **Step 2: Run them.** Expected: fail on the missing routes.

- [ ] **Step 3: Implement** as in Interfaces. Reuse `routers/upload.py`'s `_read_capped` and `ALLOWED_MEDIA_TYPES` and `images.prepare_for_ocr`. Validate every file of a batch before storing any. The router's module docstring changes: Render now stores a photo; it still reads none.

- [ ] **Step 4: Run the whole API suite.** Expected: all pass, `test_nothing_aw26_is_scheduled` and `test_no_ocr_provider_reads_a_slip` among them, untouched.

- [ ] **Step 5: Commit** `AW 26: the photo queue`.

---

### Task 6: Adding a grant to an existing key

**Files:**
- Modify: `render/api/app/apikeys.py`
- Test: `render/api/tests/test_api_clients.py`

**Interfaces:**
- Produces `apikeys.grant(db, name: str, tools: str) -> ApiClient`: the client's grant becomes the union of what it has and `parse_tools(tools)` (`"*"` on either side gives `"*"`); the key is untouched. `ValueError` for no such client, a revoked one, or an unknown tool.
- Produces the command `python -m app.apikeys grant <name> --tools aw26,cards`, printing `Client 'hub' now has grant 'aw26+cards+finder'.` and never a key.

- [ ] **Step 1: Write the failing tests**, in the style of the file's own (`apikeys.create`, then calls with the key)

```python
def test_a_grant_is_added_to_an_existing_key_without_replacing_it():
    db = Session()
    apikeys.create(db, "hub", "finder")
    assert apikeys.grant(db, "hub", "aw26,paddy").permissions == "aw26+cards+finder"   # "paddy" is the alias
    assert apikeys.grant(db, "hub", "aw26").permissions == "aw26+cards+finder"         # again: no change
    with pytest.raises(ValueError):
        apikeys.grant(db, "nobody", "aw26")
    with pytest.raises(ValueError):
        apikeys.grant(db, "hub", "not-a-tool")
    apikeys.revoke(db, "hub")
    with pytest.raises(ValueError):
        apikeys.grant(db, "hub", "aw26")
```

and one through the app: a key created with `finder` gets 403 on `GET /aw26/photos`, then 200 after `apikeys.grant(db, name, "aw26")`, with the same key; and `apikeys.main(["grant", name, "--tools", "aw26,cards"]) == 0`, following how the file already tests `main(["create", ...])`.

- [ ] **Step 2: Run them.** Expected: fail, `grant` does not exist.
- [ ] **Step 3: Implement** `grant` and the `grant` subcommand; add it to the module docstring's usage.
- [ ] **Step 4: Run the whole API suite.** Expected: all pass.
- [ ] **Step 5: Commit** `API clients: add a grant to an existing key`.

---

### Task 7: The page's sums and calls

**Files:**
- Create: `render/frontend/src/aw26.js`, `render/frontend/src/aw26.test.js`
- Modify: `render/frontend/src/api.js`

**Interfaces:**
- Consumes: the JSON of `GET /aw26/summary`, `GET /aw26/slips`, `GET /aw26/photos` (Tasks 3 to 5).
- Produces, in `aw26.js` (pure, no React, no fetch):
  - `money(n, pence = false) -> string`: `+£3,240`, `−£12.50` (a real minus sign, U+2212), `£0`; `null` gives `—`
  - `pounds(n) -> string`: unsigned, `£10,700`
  - `progress(profit, target) -> {width: number, label: string}`: width 0 to 100 for the bar (0 when the target is 0), label `3.2% of £100,000`: the same clamped figure to one place, a trailing `.0` dropped
  - `dayName(day, today) -> string`: `Today`, `Tomorrow`, else `Thu 12 Nov`
  - `stepDay(day, by, first, today) -> string | null`: the ISO day `by` days on, or `null` outside `first..today`
  - `sections(selections, today, now) -> [{day, title, tally, games}]`: by `day`, in order; `games` is `[{game, event, kickoff, slips, picks}]` by kick-off then event, `slips` the sum of its picks' `slips`; each pick is the selection plus `mark`
  - `mark(selection, now) -> 'W' | 'L' | 'V' | '?' | '–'`: `?` when pending and its kick-off is more than 24 hours before `now`
  - `tally(selections) -> string`: `4 won · 3 lost · 3 to come`, parts that are zero left out, void not counted
  - `pickText(leg) -> string`: the selection's words with a leading `Over ` / `Under ` shortened to `O` / `U`; the market's words go in front only when the selection alone does not say what is counted (the old `legText` case, "Total Match Corners" + "Over 8.5")
  - `openTo(slip) -> string | null`: the short weekday of the latest kick-off among its pending legs
  - `flagged(slip) -> boolean`: `slip.ev == null` (my call: the slip Render could not price is the one with a blank that matters)
- Produces, in `api.js`: `getAwSummary(day)`, `getAwSlips(day)` (both add `?day=` only when given), `getAwPhotos()`, `uploadAwPhotos(files)` (one multipart request, each file under the field `files`, written like `uploadCoupon`), `resendAwPhoto(id)`, `deleteAwPhoto(id)`, `setAwResult(legId, result)`. `editAwSlip` and `deleteAwSlip` stay.

- [ ] **Step 1: Write the failing tests** in `aw26.test.js`

```js
import { describe, it, expect } from 'vitest'
import { money, pounds, progress, dayName, stepDay, sections, mark, tally, pickText, openTo, flagged } from './aw26'

const sel = (o) => ({ leg_id: 1, key: 'k', game: 'w:a v b', event: 'A v B', market: null,
  selection: 'Over 10.5 corners', kickoff: '2026-11-09T15:00:00', day: '2026-11-09',
  slips: 1, odds: 1.8, result: 'pending', fair_assumed: false, ...o })
const NOW = Date.parse('2026-11-09T18:00:00Z')

describe('aw26', () => {
  it('writes money with its sign', () => {
    expect(money(3240)).toBe('+£3,240')
    expect(money(-12.5, true)).toBe('−£12.50')
    expect(money(0)).toBe('£0')
    expect(money(null)).toBe('—')
    expect(pounds(10700)).toBe('£10,700')
  })
  it('keeps the bar between empty and full, whatever the profit', () => {
    expect(progress(3240, 100000)).toEqual({ width: 3.24, label: '3.2% of £100,000' })
    expect(progress(-250, 100000)).toEqual({ width: 0, label: '0% of £100,000' })
    expect(progress(0, 100000).width).toBe(0)
    expect(progress(250000, 100000).width).toBe(100)
    expect(progress(10, 0).width).toBe(0)
  })
  it('names days', () => {
    expect(dayName('2026-11-09', '2026-11-09')).toBe('Today')
    expect(dayName('2026-11-10', '2026-11-09')).toBe('Tomorrow')
    expect(dayName('2026-11-12', '2026-11-09')).toBe('Thu 12 Nov')
    expect(dayName('2026-11-08', '2026-11-09')).toBe('Sun 8 Nov')
  })
  it('steps a day at a time, inside the first day and today', () => {
    expect(stepDay('2026-11-10', -1, '2026-11-09', '2026-11-12')).toBe('2026-11-09')
    expect(stepDay('2026-11-09', -1, '2026-11-09', '2026-11-12')).toBe(null)
    expect(stepDay('2026-11-12', 1, '2026-11-09', '2026-11-12')).toBe(null)
    expect(stepDay('2026-11-30', 1, '2026-11-09', '2026-12-05')).toBe('2026-12-01')
  })
  it('marks a selection, and gives up on one a day after kick-off', () => {
    expect(mark(sel({ result: 'won' }), NOW)).toBe('W')
    expect(mark(sel({ result: 'lost' }), NOW)).toBe('L')
    expect(mark(sel({ result: 'void' }), NOW)).toBe('V')
    expect(mark(sel({}), NOW)).toBe('–')
    expect(mark(sel({ kickoff: '2026-11-08T15:00:00' }), NOW)).toBe('?')
    expect(mark(sel({ kickoff: null }), NOW)).toBe('–')
  })
  it('puts a game with several selections on one row, under its day', () => {
    const out = sections([
      sel({ leg_id: 1, slips: 9, result: 'won' }),
      sel({ leg_id: 2, selection: 'Over 4.5 cards', slips: 5, result: 'lost' }),
      sel({ leg_id: 3, game: 'w:c v d', event: 'C v D', kickoff: '2026-11-09T12:30:00', slips: 6 }),
      sel({ leg_id: 4, game: 'w:e v f', event: 'E v F', kickoff: '2026-11-10T19:45:00', day: '2026-11-10' }),
    ], '2026-11-09', NOW)
    expect(out.map(s => [s.title, s.tally])).toEqual([['Today', '1 won · 1 lost · 1 to come'], ['Tomorrow', '1 to come']])
    expect(out[0].games.map(g => [g.event, g.slips, g.picks.map(p => p.mark).join('')])).toEqual([['C v D', 6, '–'], ['A v B', 14, 'WL']])
  })
  it('counts a day', () => {
    expect(tally([sel({ result: 'won' }), sel({ result: 'void' })])).toBe('1 won')
    expect(tally([])).toBe('')
  })
  it('shortens a pick and keeps old slips readable', () => {
    expect(pickText({ market: null, selection: 'Over 10.5 corners' })).toBe('O10.5 corners')
    expect(pickText({ market: null, selection: 'Under 3.5 Chelsea cards' })).toBe('U3.5 Chelsea cards')
    expect(pickText({ market: 'Total Match Corners', selection: 'Over 8.5' })).toBe('Total Match Corners O8.5')
    expect(pickText({ market: 'Match Result', selection: null })).toBe('Match Result')
  })
  it('says how long a slip stays open and whether it needs a look', () => {
    const legs = [{ result: 'won', kickoff: '2026-11-14T15:00:00' }, { result: 'pending', kickoff: '2026-11-12T19:45:00' },
      { result: 'pending', kickoff: null }]
    expect(openTo({ legs })).toBe('Thu')
    expect(openTo({ legs: [{ result: 'pending', kickoff: null }] })).toBe(null)
    expect(flagged({ ev: null })).toBe(true)
    expect(flagged({ ev: 0 })).toBe(false)
  })
})
```

The API's times are naive UTC (no `Z`); `aw26.js` reads them as UTC and prints London time, as `paddy.js::kickoff` does for the Paddy board. "The selection alone does not say what is counted" means it has no letters after its line (`Over 8.5`).

- [ ] **Step 2: Run them.** `npm test -- aw26`. Expected: fail, no module.
- [ ] **Step 3: Implement** `aw26.js` and the `api.js` calls.
- [ ] **Step 4: Run** `npm test && npm run lint`. Expected: all pass. (The old page still imports `getAwSlips`; it keeps working until Task 8 replaces it, though it will show no summary.)
- [ ] **Step 5: Commit** `AW 26: the page's sums and calls`.

---

### Task 8: The Summary screen

**Files:**
- Modify: `render/frontend/src/pages/Aw26Page.jsx` (rebuilt), `render/frontend/src/pages/Aw26Page.module.css` (rewritten)
- Create: `render/frontend/src/pages/Aw26Summary.jsx`

**Interfaces:**
- Consumes: `aw26.js` and the `api.js` calls from Task 7; `CardsPage.module.css` (imported as `f`); `hooks/useAutoRefresh`.
- Produces `Aw26Page` (default export; the route and the dock entry do not change) holding: the chosen `day` (`null` is today), the `view` (`summary | slips`), the summary, and `refresh()`. It renders the band, the day pill, the view, and the floating switch. It passes the Slips screen `{ day, today, onChange: refresh }`; until Task 9 that view renders nothing.
- Produces `Aw26Summary({ summary, now, onMark })` where `onMark(legId, result)` calls `setAwResult` and then refreshes.

What to build, against the mock's left-hand screen:

- The page takes `f.page` with `--band: #004833`, and sets `--tool-band` / `--dock-bg` on the document while it is open exactly as `CardsPage` does.
- **Band** (`f.band`): title "AW 26" (`f.name`), under it "Day 12" from `summary.days`. On the right one pill (`f.pill`) showing `dayName(day, today)`. Tapping it opens a panel under the band (`f.panel`) with three chips: `‹ Earlier`, `Later ›`, `Today`; a chip that `stepDay` refuses is disabled.
- **Headline**, inside the band, on Summary only: `money(to_date.profit)` in large rounded heavy type with "all-time profit" beside it; a thin bar filled to `progress().width` per cent; under it the label on the left and "214 slips · 12 days" on the right.
- **Figures table**: a header row (blank, `dayName(day, today)`, "To date"), then EV (with "of £1,000" and "of £12,000" under the figures, from `ev_target`), Profit (signed; `--good` above zero, `--red` below), Staked, Slips (with "9 open" under each).
- **Selections**: a column header (blank, "KO", "Slips", "Odds", "Result"), then for each of `sections(...)` a grey heading (title in bold, tally beside it) and its games:
  - one pick: the `f.row` layout: `pickText`, the match under it, kick-off time, `×n`, odds to 2 places, the result;
  - several picks: one row "Arsenal v Chelsea ▾ 3" with the picks' marks in the result column (W green, L red); tapping it drops the picks down (`f.sub` / `f.subRow`): pick, `×n`, odds, result;
  - a pick with `fair_assumed` carries `~` after its text (`f.more`);
  - the result reads Won (green), Lost (red), Void, `–` or `?`. Tapping it opens four chips under that row (Won, Lost, Void, Pending); choosing one calls `onMark` and closes them.
- Loading and failure use `f.empty` and `f.error` as `CardsPage` does. With no selections, the list is one `f.empty` line: "No selections."
- **Switch**: `f.offerSwitch` with two `f.offerBtn` buttons, "Summary" and "Slips".
- It refreshes with `useAutoRefresh`, and when the day changes.

`Aw26Page.module.css` holds only what `CardsPage.module.css` has no class for (the headline, the bar, the figures table, the day headings, the result colours and chips). The paper-theme rules in it today all go.

- [ ] **Step 1: Build it.** There is no component test harness in this app; the sums are tested in Task 7 and the screen is checked by eye in Step 3.
- [ ] **Step 2: Run** `npm test && npm run lint && npm run build`. Expected: all pass.
- [ ] **Step 3: Look at it.** Serve the build (`npx vite preview --port 4179 --strictPort --host 127.0.0.1`, in the background) and take a 375 × 812 screenshot at device scale 2 with Playwright (`~/.venvs/puntmore/bin/python`, `executable_path="/usr/bin/google-chrome"`, `service_workers="block"`), fulfilling every `/api/` request from fixtures: `/api/auth/site` gives `{"me_host": ""}`, `/api/auth/me` gives `{"username": "callum", "tools": ["aw26"]}`, `/api/aw26/summary` gives a fixture carrying the mock's numbers (profit 3240, 214 slips, 12 days, today's EV 412, the mock's selections with two multi-selection games, one assumed fair, one overdue). Take a second shot with a multi-selection game dropped down and a result's chips open, and a third from a fixture with profit −250 and no selections. Read each picture and compare it with the mock. Fix what differs in layout, weight, spacing or colour. Keep the script and pictures out of the repository.
- [ ] **Step 4: Commit** `AW 26: the Summary screen`.

---

### Task 9: The Slips screen

**Files:**
- Create: `render/frontend/src/pages/Aw26Slips.jsx`
- Modify: `render/frontend/src/pages/Aw26Page.jsx`, `render/frontend/src/pages/Aw26Page.module.css`

**Interfaces:**
- Consumes: `getAwSlips(day)`, `getAwPhotos`, `uploadAwPhotos`, `resendAwPhoto`, `deleteAwPhoto`, `editAwSlip`, `deleteAwSlip`; `pickText`, `openTo`, `flagged`, `money`, `dayName` from `aw26.js`; the props `{ day, today, onChange }` from Task 8.
- Produces `Aw26Slips` (default export). `Aw26Page`'s band reads "12 slips today" under the title on this screen ("12 slips" on an earlier day); `Aw26Slips` reports the count up through a prop `onCount(n)`.

What to build, against the mock's right-hand screen:

- **Upload**: the first row under the band is one button, "Upload slips", over a hidden `<input type="file" accept="image/*" multiple>`. Choosing photos calls `uploadAwPhotos` and reloads the queue. A refused upload shows the server's words in `f.error`.
- **Queue**: one row per photo from `getAwPhotos`. `waiting` or `reading`: "Waiting for Qwen". `failed`: "Qwen could not read this", the reason under it, and two buttons, "Send again" (`resendAwPhoto`) and "Delete" (`deleteAwPhoto`). `duplicate`: the reason (the server's "Slip … is already in") and "Dismiss" (`deleteAwPhoto`). The queue shows on whatever day is chosen.
- While any photo is `waiting` or `reading`, the queue and the slips reload every 20 seconds (my call: `useAutoRefresh`'s two minutes would leave a read slip looking unread); otherwise `useAutoRefresh` alone. When a reload finds fewer photos in the queue, call `onChange` so the Summary's figures follow.
- **Slips** for the chosen day: each has a grey heading. Left: the offer capitalised in bold, the London time placed, the stake (`£50`). Right: `EV +£6.42`, then `· open to Thu` (`openTo`) while open or the profit once settled (signed, `--good` or `--red`). A `flagged` slip shows its `ev_note` in `--red` where the EV would be.
- Under the first slip's heading, the column header: "Leg", "Odds", "Fair", "Result". Each leg: `pickText` with the match under it (and `· Thu` after the match when the leg's kick-off day is not the day shown), odds in bold, fair in grey with `~` after it when assumed, the result as on Summary (not tappable here).
- Tapping a slip opens the editor the page has today (`SlipEditor`, moved into this file with its behaviour unchanged: every field correctable, the "Photo" link when `has_image`, delete), restyled only as far as it needs to sit on the white ground. Saving or deleting reloads the slips and calls `onChange`.
- No slips on the day: one `f.empty` line, "No slips."

- [ ] **Step 1: Build it.**
- [ ] **Step 2: Run** `npm test && npm run lint && npm run build`. Expected: all pass.
- [ ] **Step 3: Look at it**, as in Task 8 Step 3, with fixtures for `/api/aw26/slips` (the mock's two refund slips, one open to Thursday, one settled at −12.50, plus one flagged slip with a blank fair) and `/api/aw26/photos` (one waiting, one failed with the reason "no receipt number", one duplicate). Shots: the Slips screen; the same with a slip's editor open. Compare with the mock and fix what differs.
- [ ] **Step 4: Commit** `AW 26: the Slips screen, the upload and the queue`.

---

### Task 10: The register and the words

**Files:**
- Modify: `docs/decisions.md` (the AW 26 entry), `CLAUDE.md` (the AW 26 paragraph), `render/frontend/AGENTS.md` (a short section after the Paddy board's look). `README.md` only names AW 26 in a list and needs no change; `hub/AGENTS.md`, `docs/dataflow.md` and `docs/architecture.md` change with the Hub's plan.

- [ ] **Step 1: Add to the register's AW 26 entry**, after its last sentence, these words exactly (re-wrapped to the file's width):

> **The page and the photos (Callum, 9 Nov 2026).** The page is "summaries, individual slips on a sub screen", with "the selections listed in aggregate with win or lose" and "an all time profit" as its headline: "I need to keep motivated." "Carry the styling from the paddy tool." "Photo upload. Can you add an upload button on the page which goes to Qwen": Render stores the photo and hands it over when the Hub asks; it still reads nothing and runs nothing in the background. Fairs come from the Paddy board, and a bet that is not on it is "neutral EV. Better to be overly cautious": its fair is its odds, and it is marked. "Start fresh": slips placed before 9 Nov 2026 stay stored and count nowhere. The design is `docs/superpowers/specs/2026-11-09-aw26-qwen-design.md`.

The Flashscore exception, the Telegram backup and the Hub daemon's loop are not written in yet: each goes in with the pull request that carries its code.

- [ ] **Step 2: Rewrite `CLAUDE.md`'s AW 26 paragraph** to say what is now true: a Summary (all-time profit, the day against the targets, the selections in aggregate) and a Slips sub screen; a photo uploaded on the page waits in a queue on Render (`aw_photos`) for the Hub to collect; slips are recorded and marked on the same `aw26` routes by the page and by HubAI's key; Render does the sums when asked, reads no photo and runs nothing in the background; slips before 9 Nov 2026 do not count.

- [ ] **Step 3: Add to `render/frontend/AGENTS.md`** a section "AW 26's look (`pages/Aw26Page.jsx`, Callum, 9 Nov 2026)": it carries the Paddy board's styling by importing `CardsPage.module.css`; the headline is profit, not EV; what each screen holds is in the design; nothing is added to either screen that the design does not name.

- [ ] **Step 4: Verify everything.** From `render/api/`: the whole suite passes. From `render/frontend/`: `npm test && npm run lint && npm run build` pass. `git grep -n "summary(" render/api/app` shows no caller of the removed function. `git status` is clean after the commit.

- [ ] **Step 5: Commit** `AW 26: register and rules for the page and the photos`.

---

## After the last task

The controller, not a builder: whole-branch review; pull request to `main`; merge with a merge commit; watch the deploy (the bundle hash on `https://puntmore.com/` changes, `/api/ready` answers 200); open `/aw26` in production and upload nothing. Then give Callum the one command for Render's Shell:

```
python -m app.apikeys grant <the Hub key's name> --tools aw26,cards
```

Not in this plan: the Hub's commands, the photo loop, the runbook and the Telegram backup (the next plan, after the proof with Callum's slips); games due a result and Qwen's results run (the third). Until the next plan ships, an uploaded photo sits at "Waiting for Qwen".
