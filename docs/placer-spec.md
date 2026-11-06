# Bet Placer — the module spec

The tool that sizes, places and records bets from the three boards (Paddy, 2-Up, Finder). Callum's runs on the Hub; a third party runs the same Bet Placer on 2-Up only. Decided with Callum on 6 Nov 2026; `docs/decisions.md` wins over this file, and it changes in the same pull request as the code.

## Decisions (6 Nov 2026, Callum)

- **A black box for now.** Its outside is fixed: it reads the three feeds, sizes and places, keeps the only book, and posts `placed` / `gone` to Finder. Its inside is designed when the box is opened. Two parts are named: the **phone interface** and the **staking logic** that drives it.
- **Phones are driven through DeviceFarmer** at a local port on the Hub: real phones, each holding one or more bookmaker accounts, in the bookmaker's app and in the phone's browser. Callum can take manual control of any phone the same way.
- **bet365 bets go through the phone's browser**, not the app.
- **Keys.** The Bet Placer's machine key on Render carries `accabonus` (2-Up), `cards` (Paddy), `finder` and `ingest` (the Finder status post). A third party's carries `accabonus` only. Keys are minted with `python -m app.apikeys` (`docs/api.md`).
- **HubAI advises, the Bet Placer decides.** A tip's `advised_stake` on Finder is advice; the sizing across all sources is the Bet Placer's own, set on a sizing page in the Console.

## What it reads, and the rules that bind it

1. **Each board's own feed**: `GET /api/2up/feed`, `GET /api/paddy/feed`, `GET /api/finder/feed`. Each carries its full status. `/api/value/{source}` is a cross-tool view for reading by hand; it reports a board that cannot price only as an `errors` note, so it is not the Bet Placer's source.
2. **A stale board is no-bet.** The 2-Up feed serves the last good board when OddsPapi stops answering, with `status.stale: true`, `status.served_at` and every row's `priced_at`. Treat `stale: true`, or a `priced_at` older than a limit the Bet Placer sets (default the feed's refresh interval, `twoup_feed_ttl`, 5 min), as no-bet; the same holds for any board that carries `priced_at` / `seen_at`.
3. **An empty board is not a quiet day until the status says so.** `status.not_yet_refreshed` (first pass pending after a deploy), `status.killed` (the global kill switch) and `status.cannot_price` are different empties; log which one was seen and bet on none of them. `status.degraded` means the board is partly priced.
4. **The only book is the Bet Placer's.** Render keeps no record of what was placed or settled; Finder learns `placed` / `gone` only because the Bet Placer posts it (`POST /api/finder/rows/{tip_id}/status`).
5. **No OddsPapi spend.** The Bet Placer sees the live price on the phone at placement time and reports `gone` or re-sizes locally, never by asking Render for a quote.
6. **Dry-run is the default**; Callum switches it off after two weeks of clean dry-run, nobody else. **The kill switch is a file** (`hub/data/KILL`); anyone may turn it on, only Callum turns it off.
7. **No auto-retry and no re-routing.** `failed` is recorded and escalated to HubAI. A changed price re-runs the sizing at the new price. Every placement is idempotent on its bet id.

## What exists

- The placement engine in `hub/puntmore_hub/placement/` (one placement per bet id, kill switch, dry-run, betting window, escalation; the phone adapter a stub; its README holds the adapter interface), and the Hub store's `bets` and `balances` tables.
- The staking code deleted from Render on 6 Nov 2026 (Kelly, ladder rounding, ceiling, growth cap, window, daily limit) is intact in git history at commit `f1f3172^` (`render/api/app/twoup/model.py`, `decision.py`, `ledger.py`): the starting point for the staking logic.
- The Console, without a sizing page.

## Open work

- [ ] **The phone interface**: drive a phone through DeviceFarmer, bet365 through the browser; read the price actually taken off the slip.
- [ ] **Two weeks of dry-run** against live feeds before `PUNTMORE_DRY_RUN=0`.
- [ ] The sizing page in the Console.

## Inherited from the 2-Up spec of 3 Nov, to be re-decided when the box opens

These were written for a 2-Up engine that sized its own bets on Render. They are the starting point for the staking logic, not decisions.

**Decision model.** Constrained optimisation: maximise expected log bankroll growth (Kelly) on the pooled bankroll, the stake spread across the accounts. Hard constraints: (1) bankroll: total stake bounded by the pooled balance, each account's share by its own balance less open exposure; (2) availability: only what is in today's feeds, no turnover target; (3) account preservation: the rules below. Per account, per day: rank the bets by edge, size with fractional Kelly, apply constraints 3 → 1 → 2, drop what fails. Open exposure is tracked per account so pending bets are never double-counted. Bet types: singles, doubles, trebles, occasional fourfolds.

**Account-preservation rules.**

| Rule | Value |
| --- | --- |
| Betting window | 07:30–22:00 local |
| Multi-leg timing | ≥1 leg within 24 h; all legs within 72 h |
| Stake rounding | Nearest of £1, £2, £5, £10, £20, £50, £100… |
| Bets per account per day | Hard max 20 |
| Stake ceiling | Dynamic from account bankroll and recent stakes; strong upper cap, no floor |
| Stake growth | Must not visibly step up after wins: cap the rate of change against trailing stakes |

**Cover bets.** Occasional non-value bets (Lucky 15s, bet builders) that Callum specifies, to make an account look less systematic: a known cost with its own small budget, which the optimiser accounts for.

**Failure handling.** No auto-retry, no re-routing. A `failed` placement is dropped and escalated to HubAI, which decides what happens next.

**Open questions.**

- [ ] Kelly fraction
- [ ] Dynamic stake-ceiling formula (window, max step)
- [ ] Cover-bet budget as a share of turnover
- [ ] Daily stats report: contents and destination
- [ ] Payback terms and reporting period for a third party's Bet Placer
