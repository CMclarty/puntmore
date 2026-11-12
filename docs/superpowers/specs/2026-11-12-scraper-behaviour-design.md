# The Scraper's behaviour: one catalogue in code, changed live from the Console

Callum, 12 Nov 2026: "I want the behavior of the scraper to be controlled/live from a pane on
the hub and also very visible in the code, in case it needs to be changed after issues. This is
the behavior of the scraper, the way it clicks on the page, the timings, the limits etc."

## Intent

Today the Scraper's behaviour is constants spread over seven modules (`sites.py`,
`schedule.py`, `guard.py`, `browser.py`, `login.py`, `__main__.py`, `parsers/bb_saves.py`).
Changing one means finding it, a pull request and a restart, and nobody can see the whole of
how the Scraper behaves in one place. After this:

- every behaviour setting is declared once, in `scraper/behaviour.py`, with its default, unit,
  bounds and a one-line plain description - that file IS the description of how the Scraper
  behaves;
- any of them can be changed while the Scraper runs, from a Behaviour pane on the Console's
  Scraper tab (or the command line), applied within 60 seconds without a restart or a page
  reload, and put back to its default with one press;
- every change is logged.

## Goals, non-goals

- Goal: no behaviour changes at the defaults. Each default is today's constant, pinned by a test.
- Goal: a wrong value cannot stop the Scraper: a value outside a setting's bounds is refused
  with a message naming the setting; an unreadable overrides file keeps the running values.
- Non-goal: nothing new about what the browser shows a site (no user-agent, navigator,
  fingerprint or stealth settings; none exist and none are added). What a block means (stop,
  cool off, wait for `resume`), never answering a challenge, and the allowed hosts stay code.
- Non-goal: per-job schedule (`targets.toml`) - unchanged, still the jobs list.
- Non-goal: HubAI changing behaviour. The Console (token) and the command line on the box only;
  HubAI's runbook says so. (Controller's call: an agent should not loosen limits unasked.)

## Decisions

1. **The catalogue** (`scraper/behaviour.py`): `SETTINGS`, an ordered tuple of
   `Setting(key, group, kind, default, lo, hi, unit, says)`. `kind` is `int`, `float`, `bool`,
   `range` (a low-high pair, lo <= hi, both inside the bounds) or `hours` (a start-end hour
   pair, 0-24). `behaviour.get(key)` returns the live value. No other module holds a
   behaviour constant; each reads `behaviour.get(...)` at the moment of use, so a change
   applies to the next load / click / tick.
2. **The settings** (key: default - what it is). Groups in this order:
   - *Limits, per site* (`paddypower`, `bookiebashing`, `generic`):
     `<site>.per_hour` (15 / 8 / 60), `<site>.per_day` (120 / 120 / 400),
     `<site>.load_gap_s` (90 / 60 / 20: least gap between two of its loads),
     `<site>.quiet_hours` (bool: true / false / false).
   - *Limits, all sites*: `quiet.hours` (1-7), `cooloff.hours` (24),
     `login.max_signins` (3), `login.window_h` (6).
   - *Spacing of page loads*: `load.any_gap_s` (20-45), `load.site_gap_x` (1.0-1.8),
     `reload.jitter_x` (0.75-1.35), `relist.jitter_x` (0.9-1.3), `open_gap.jitter_x` (1.0-1.6),
     `recycle.hours` (0.5), `recycle.jitter_x` (0.85-1.2), `fetch.default_min` (60),
     `fetch.retry_s` (300), `restart.hours` (1.5-2.0: the service restarts itself this often).
   - *Clicks*: `click.every_s` (600: a kept page's market tabs are clicked again this often),
     `click.every_jitter_x` (0.8-1.4), `click.before_move_ms` (300-900),
     `click.move_steps` (12-30), `click.before_press_ms` (250-900), `click.press_ms` (50-140),
     `click.aim_x` (0.3-0.7), `click.aim_y` (0.35-0.65).
   - *Reading down a page*: `read.passes` (2-4), `read.move_steps` (8-25),
     `read.scroll_px` (300-700), `read.pause_ms` (900-2500), `read.back_up_px` (2500-4000),
     `read.back_up_pause_ms` (500-1200).
   - *Bookie Bashing bet builder*: `bb.call_budget_s` (40), `bb.update_every_s` (1800),
     `bb.within_h` (48), `bb.look_again_s` (86400), `bb.xsaves_retries` (3),
     `bb.settle_s` (0.25).
   The implementer confirms each default against the code and adds any behaviour constant
   this list missed (same naming), saying so in the report. Internal plumbing (capture ring
   sizes, log sizes, watchdog, parse interval, file names) is not behaviour and stays put.
3. **Bounds** are for sanity, not policy: wide enough that Callum can loosen or tighten any
   setting (e.g. `per_hour` 0-600, gaps 0-3600 s, pauses 0-10000 ms), tight enough that a typo
   (a negative, a gap of a week) is refused. `0` for a site's `per_hour` or `per_day` means no
   loads for that site.
4. **Overrides** live in `<data>/behaviour.toml`: flat `key = value`, only settings that differ
   from their default. Written only through `behaviour.set_(data_dir, key, value)` /
   `behaviour.reset(data_dir, key | None)`: a file lock, validation, atomic save (the way
   `targets.py` does it). The runner looks at the file's mtime with its targets check (every
   60 s) and loads it; a file that will not load, or a key it does not know, keeps the running
   values and is reported once (journal and `data/logs/behaviour.log`). Every applied change
   is written to that log: `key: old -> new`.
5. **Command line**: `python -m scraper behaviour` (list: group, key, value, default, marks the
   changed ones), `behaviour set <key> <value>`, `behaviour reset <key>`, `behaviour reset --all`;
   `--json` for the Console.
6. **Console** (hub/, stdlib only, through the Scraper's own library like the jobs list):
   `GET /api/scraper/behaviour` (the catalogue with live values);
   `POST /api/actions/scraper/behaviour/set` `{key, value}` and `.../reset` `{key}` or
   `{all: true}` - token-guarded, logged to `console-actions.log` like every action.
   On the Scraper tab, a **Behaviour** section under the jobs: closed until its button is
   pressed (as "Add target" is); one table per group: setting (the plain description, the key
   small beneath), value (editable; two boxes for a range), unit, default, and "reset" on a
   changed row; changed rows marked; a refusal shows beside the field. A line at the top says
   how many settings differ from default, visible while the section is closed.
7. **Register** (Callum, 12 Nov 2026): "Budgets are code" and "Config can only make it
   slower / no config edit can loosen it" are replaced by: the Scraper's behaviour (limits,
   timings, clicks) is declared in `scraper/behaviour.py`; Callum changes any of it live from
   the Console's Behaviour pane; every change is logged; what a block means and the allowed
   hosts stay code. `scraper/AGENTS.md`, `scraper/README.md`, `hub/README.md`, `hub/AGENTS.md`
   and HubAI's scraper-ops runbook follow.

## Slices

A. The catalogue, the overrides file, the command line; every module reads from it; defaults
   pinned to today's constants. No runner reload yet (values read at start).
B. Live: the runner reloads overrides each minute; the guard's limits, quiet hours and cool-off
   follow; the change log; `status.json` carries the count of changed settings.
C. The Console: the two routes and the Behaviour section. Docs and the register.
