> Executed 6 Nov 2026; kept as the record of the build.

# Names and Strip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every document, rules file, diagram and code comment describes the structure settled on 6 Nov 2026 in the names of the register's "Names and structure" section, with no history blocks; the two product hosts are folded in code; the dead Hub wire code is removed.

**Architecture:** The register (`docs/decisions.md`, "Names and structure") is the authority and is already written. The inventory at `.superpowers/sdd/doc-inventory.md` (sections A–E) lists every stale line, history block, dead document, PLAN item and code comment; it is the work list. Code changes are confined to: the site tool lists, three environment-variable renames with fallbacks, the Hub's dead wire code, two retired tool scripts, and comments.

**Tech Stack:** Markdown; Python (FastAPI app, Hub package, Scraper); React (site.js, App.jsx, BottomNav.jsx); pytest and vitest.

**Spec:** `docs/decisions.md`, section "Names and structure (6 Nov 2026, Callum)"; the inventory `.superpowers/sdd/doc-inventory.md`.

## Global Constraints

- The register wins. Where the inventory's "Suggest" column and the register differ, the register's name is used.
- No history blocks: every "History (superseded …)" paragraph and every "(removed N Nov)" aside is deleted; the register's dated entries are the record; `docs/retired.md` gets a one-paragraph pointer per deleted document or module, nothing longer.
- Prose names: Scraper service / Scraper control / HubAI / Bet Placer / Console / Hub / Render / board / feed / Paddy / 2-Up / Finder / 365 tool / Callum / tip / drop file / Finder row / phone. Hermes, llama-server and Qwen appear only where the HubAI setup is described. Identifiers (`cards`, `accabonus`, `twoup`, `2up`, `finder`, `puntmore-link`) stay as they are in code and paths.
- Code behaviour does not change except: Task 2 (site lists), Task 3 (env fallbacks), Task 4 (Hub wire removal), Task 5's retirement of `tools/probe_corners.py`. Every other code touch is a comment or docstring.
- `hub/` tests for placement and the Scraper's drop files stay green; `mock_render` goes with the wire.
- Commits end with: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013jCUTuGdz9ogpzSZnRywMB`. Branch `names-and-strip` in `/home/callum/Projects/puntmore-strip`. Never force-push, never commit secrets.
- Test commands: API `cd render/api && /home/callum/Projects/puntmore/api/.venv/bin/python -m pytest tests/ -q`; frontend `cd render/frontend && npm ci --silent && npm run lint && npm test && npm run build` (node_modules is not in this worktree yet); hub `cd hub && ~/.venvs/puntmore/bin/python -m pytest -q`; scraper `PYTHONPATH=/home/callum/Projects/puntmore-strip SCRAPER_CHROME_PATH=/usr/bin/google-chrome ~/.venvs/puntmore/bin/python -m pytest scraper/tests -q` (some browser tests need the desktop; a failure that also fails on main is not yours).

## Review Focus

1. A coupons-only account on the main domain after the fold: it must be sent to shalgo's coupon tracker, not refused. (Task 2)
2. An old Hub `.env` still naming `PUNTMORE_WORKER_DATA` or `PUNTMORE_ESCALATE_TO_LOCAL_AI`: both must still be read. (Task 3)
3. The Hub daemon after the wire removal: inbox and mail loops run, `puntmore link` still starts it (service name unchanged), the Console's card describes it correctly, and escalation outcomes do not queue onto anything. (Task 4)
4. The Scraper's drop files after the schema cut: `scraper_tips` and `scraper_event` still validate on both sides. (Task 4)
5. `grep` for every dropped name finds nothing outside `docs/retired.md` and the register's dated lines. (Task 9)

---

### Task 1: The register and the two specs carry the structure

**Files:** `docs/decisions.md` (fix inventory B rows 28–33 and every other stale line in it: the three-host line becomes two product hosts + the main domain; `source_scope` goes; the 2-Up block names the Bet Placer; the per-client kill flag goes; "tip staking OPEN" is closed: HubAI judges from the tip's own price and fair price; the Scraper's remit is "every page any tool needs; fills what OddsPapi lacks"); `docs/finder-spec.md` and `docs/placer-spec.md` (names per the register; placer-spec gains the sections moved from the 2-Up spec in Task 5: decision model, account-preservation rules, cover bets, failure handling, the staking open questions, as "inherited from the 2-Up spec of 3 Nov, to be re-decided when the box opens"; the key grants per the register); `docs/2up-engine-spec.md` is handled in Task 5.

- [ ] **Step 1:** Edit; then `grep -n "betting agent\|Hub endpoint\|source_scope\|per-client\|Cal \|Cal's\|local AI\|local model\|client engine\|2up.puntmore" docs/decisions.md docs/finder-spec.md docs/placer-spec.md` shows only the register's dropped-names line and dated history entries.
- [ ] **Step 2:** Commit: `register: the names and structure of 6 Nov; the specs follow`

### Task 2: Two product hosts in code

**Files:** `render/api/app/site.py`, `render/frontend/src/site.js`, `render/api/app/identity.py` (site names, titles, manifests: shalgo's landing is the coupon tracker, me's is Finder), `render/frontend/src/App.jsx` and `components/BottomNav.jsx` (landing and OtherSite redirects), `render/api/app/routers/auth.py` (the login-site check), tests `render/api/tests/test_identity.py`, `render/frontend/src/site.test.js`.

**Interfaces:** `SHALGO_TOOLS = ("coupons", "cards", "hills", "accabonus", "goalscorers")`, `ME_TOOLS = ("finder", "pnl", "kelly", "boost", "shop")`; the main domain owns no tool: a logged-in user landing there is sent to the site of their first granted tool, same path; `site.js` mirrors both tuples.

- [ ] **Step 1:** Tests first: `test_every_tool_belongs_to_shalgo_or_me` (no tool resolves to the main site), `test_a_coupons_account_on_the_main_domain_lands_on_shalgo`, the frontend mirror test. Expected: FAIL.
- [ ] **Step 2:** Implement; run API tests, `npm run lint && npm test && npm run build`. Expected: PASS.
- [ ] **Step 3:** Commit: `sites: shalgo serves every public tool, me the personal ones, the main domain is the door`

### Task 3: Environment names

**Files:** `hub/puntmore_hub/config.py` (`PUNTMORE_SCRAPER_DATA` / `PUNTMORE_SCRAPER_STALE_MINUTES` primary, old names read as fallbacks: already so; `PUNTMORE_ESCALATE_TO_HUBAI` primary with `PUNTMORE_ESCALATE_TO_LOCAL_AI` fallback; the field `escalate_to_local_ai` renamed `escalate_to_hubai` and every reader), `hub/.env.example`, `hub/README.md`, `hub/AGENTS.md`, `hub/puntmore_hub/escalation.py` and the other readers; test in `hub/tests/test_inbox.py` or `test_escalation.py`: both names read.

- [ ] **Step 1:** Test, fail, implement, pass (`cd hub && pytest -q`).
- [ ] **Step 2:** Commit: `hub: HubAI and Scraper env names, old names still read`

### Task 4: Remove the dead wire from the Hub

**Files:** delete `hub/puntmore_hub/link/` (move the inbox and mail loops of `cli.py::cmd_link` into `hub/puntmore_hub/daemon.py::run()`; `puntmore link` keeps its name and calls it; add `puntmore daemon` as the documented name), `hub/puntmore_hub/mock_render.py`, `hub/schemas/examples/*` except `scraper_tips.json` and `scraper_event.json`, the wire types in `hub/schemas/protocol.schema.json` (keep the two file types and the envelope), the `Bet`/`Report`/`ScrapeJob`/`CandidateTip` classes in `hub/puntmore_hub/schemas.py` that nothing else uses (the placement engine's `Bet` stays if `engine.py` uses it: read it), `cmd_link_check`, the outbox table's writers (escalation outcomes no longer queue; `store.outbox_*` deleted if nothing reads them), the Console's link card (`web/collect.py`, `web/static/*`: a "Daemon" card with last inbox pass and last mail poll), `hub/PROTOCOL.md` cut to the drop-file contract and renamed in prose "the Scraper's drop files" (file name kept), `hub/README.md` and `hub/AGENTS.md` accordingly; tests: delete `test_link_integration.py`, `test_protocol.py`'s wire cases, `test_schemas_and_link.py`'s link cases; keep and adjust the drop-file cases; `render/api` has no copy of the schema any more (verify with grep).

- [ ] **Step 1:** Write the daemon test (`test_the_daemon_runs_inbox_and_mail_without_a_socket`, from the existing offline test) and the drop-file schema test; run; implement; `cd hub && pytest -q` PASS; the Scraper suite's sink tests PASS.
- [ ] **Step 2:** Commit: `hub: the wire is gone — daemon, drop files, Console card`

### Task 5: Top-level documents

**Files:** `README.md`, `CLAUDE.md`, `PLAN.md`, `docs/architecture.md`, `docs/dataflow.md`, `docs/2up-engine-spec.md`, `docs/retired.md`, `SPEC.md`; delete `docs/api-handover.md`, `docs/scraper-handover.md`, `docs/provider-verification.md`, `scraper/HANDOVER.md` (Boundaries into `scraper/AGENTS.md`, Credentials into `scraper/README.md`); retire `tools/probe_corners.py` and fix `config.py:30`, `goalscorers/value.py:25` comments; `docs/superpowers/plans/*` keep with a first-line banner "Executed 6 Nov 2026; kept as the record of the build".

Content rules:
- `README.md`: what Puntmore is; the two boxes; the boards and the two product hosts; the Hub's five sub-components; the reading order (register, architecture, dataflow, the three specs, the directory rules); the commands. Under 120 lines.
- `CLAUDE.md`: rules only; the "What runs where" table in the new names; the 365 paragraph rewritten; Finder and the Bet Placer present; no history.
- `PLAN.md`: Decisions section replaced by one line pointing at the register; Open work keeps items 1, 3, 4, 5, 8 as open with their residuals, lists 2, 2a–2f, 11, 12 as done in one line each, deletes 6, 7, 9 and 10 (10's two live residuals, the phone interface and the dry-run fortnight, move to `docs/placer-spec.md`); History section deleted; the R8 eligibility facts and the R10 bias lesson move to `render/api/AGENTS.md`.
- `docs/architecture.md`: the diagram redrawn top to bottom in the register's names: Render (boards: Paddy, 2-Up in 365, Finder; coupon tracker, Hills, personal tools; the two hosts), Hub (Scraper service → Scraper control → boards / HubAI; HubAI ↔ Telegram; Bet Placer with phone interface and staking logic → phones through DeviceFarmer; Console; the tunnel), third-party clients (Bet Placer on 2-Up only); every arrow labelled with its route or file; a components table with state (live / built / to build); the four HTTP flows replace the contract table; the open items: Scraper control routing of Paddy prices, the tunnel, the Bet Placer box, the Finder DNS.
- `docs/dataflow.md`: stages in the new names (page → Scraper service → drop file → Scraper control → board or HubAI → Finder row → Bet Placer → placed/gone); the four rules kept, rule 2 names `tip_id` and the Finder row; "where to look" updated.
- `docs/2up-engine-spec.md`: cut to the feed contract (with `status.stale` as built, `degraded` meaning partly priced), the rescue model and the BB constraint; the staking, tenancy, interfaces, cover bets and Bet Finder sections move to `docs/placer-spec.md` (Task 1) or are deleted; the title becomes "2-Up — the module spec".
- `SPEC.md`: cut to §3, §7, §8 (coupon markets and pricing), retitled "Coupon pricing (from the original web-app spec)"; the rest to `docs/retired.md` as a pointer.

- [ ] **Step 1:** Edit per the inventory (B rows for these files, B-H blocks, C, D).
- [ ] **Step 2:** `grep -rn "History (superseded\|superseded\|Worker\|betting agent\|client engine\|placement bot\|local AI\|sub-client\|orchestrator\|2up.puntmore\|bet list\|/2up/ws" README.md CLAUDE.md PLAN.md docs/*.md SPEC.md` → hits only in `docs/retired.md` and the register's dated lines.
- [ ] **Step 3:** Commit: `docs: one structure, one set of names, no history`

### Task 6: Directory rules and READMEs

**Files:** `render/api/AGENTS.md` (365 and Finder sections in the names; the API-clients section: the Bet Placer's grants per the register; the R8/R10 lessons from PLAN; delete Bet Finder and Hub endpoint remnants), `render/frontend/AGENTS.md`, `scraper/AGENTS.md` and `scraper/README.md` (remit: every page any tool needs; rows to Scraper control; the Docker fallback section says "a box with no screen"; Credentials from HANDOVER), `hub/AGENTS.md` and `hub/README.md` (the five sub-components; the daemon; the Finder CLI as HubAI's contract; placement under the Bet Placer; diagram redrawn), `hub/puntmore_hub/placement/README.md` (the adapter interface + DeviceFarmer + bet365 through the browser; open questions answered), `.env.example`, `hub/.env.example`, `scraper/targets.example.toml`, the Console's `index.html` strings, `render/api/clients/python/README.md`, `docs/api.md` (the placer reads the three feed routes; `/api/value` is a cross-tool view; grants per the register).

- [ ] **Step 1:** Edit; grep as in Task 5 over these files.
- [ ] **Step 2:** Commit: `rules: every directory in the one set of names`

### Task 7: Code comments and docstrings

**Files:** the 50 items of inventory section E (`render/api/app`, `hub/puntmore_hub`, `scraper/`), including the `fair_source` fallback literal `"bettingiscool"` in `render/api/app/cards/oddspapi.py:992` (replace with the OddsPapi fair book setting's default and a test that the old literal is gone), and the module docstrings named in the inventory brief.

- [ ] **Step 1:** Edit; all four suites PASS.
- [ ] **Step 2:** Commit: `comments: the code describes the structure it implements`

### Task 8: Verification

- [ ] All four suites PASS (frontend after `npm ci`).
- [ ] `grep -rn "Worker\b\|betting agent\|client engine\|placement bot\|placement service\|bet-placing\|private engine\|local AI\|sub-client\|sub_client\|Bet Finder orchestrator\|candidate tip\|link daemon\|2up\.puntmore\|/2up/ws\|bet_list\|Cal's\|\bCal\b" --include=*.md --include=*.py --include=*.js --include=*.jsx --include=*.toml --include=*.example --include=*.html . 2>/dev/null | grep -v "node_modules\|\.venv\|__pycache__\|docs/retired.md\|docs/superpowers/plans\|\.superpowers"` → only the register's "Dropped names" line and dated history entries.
- [ ] Do not push; report with the commit range, the suite results, the grep output and every `Ruling:` line from `.superpowers/sdd/strip-ledger.md`.
