# Bet Placer (design, in progress — 6 Nov 2026)

The tool that sizes, places and records bets from the three value boards (Paddy, 2-Up, Finder), on Callum's Hub and on third-party clients. Being designed with Callum on 6 Nov 2026; the sections below fill in as the conversation settles them. `docs/decisions.md` wins over this file.

## Findings carried in from the 2-Up build (6 Nov 2026)

These came out of building the 2-Up value feed and bind the placer's design:

1. **A stale board is no-bet.** `GET /api/2up/feed` serves the last good board when OddsPapi stops answering, with `status.stale: true`, `status.served_at` (when that board was built) and every row's `priced_at`. The placer must treat `stale: true`, or a `priced_at` older than a limit it sets (default: the feed's refresh interval, `twoup_feed_ttl`, 5 min), as no-bet, and read `status.cannot_price` when the board is empty. The same rule applies to any board that carries `priced_at` / `seen_at`.
2. **An empty board is not a quiet day until the status says so.** `status.not_yet_refreshed` (first pass pending after a deploy), `status.killed` (the global kill switch) and `status.degraded` with `cannot_price` are three different empties; the placer logs which one it saw and bets on none of them.
3. **The cross-tool value feed is not the placer's source.** `/api/value/{source}` is a read-only cross-tool view; it reports a board that cannot price only as an `errors` note. The placer reads each board's own feed route (`/api/2up/feed`, `/api/paddy/feed`, `/api/finder/feed`), which carries the full status.
4. **No Render-side ledger exists any more.** Render keeps no record of what was placed or settled (register, 6 Nov 2026). The placer's own log is the only book; the Finder board learns `placed` / `gone` only because the placer posts it (`POST /api/finder/rows/{tip_id}/status`).
5. **The Hub's old link socket is gone.** Render's end of `hub/PROTOCOL.md` (`/api/2up/ws`, bet lists down, reports up) is removed; a Hub endpoint is per tool. The placer defines its own Hub endpoint only if it needs one; the Hub link daemon now runs the inbox and mail loops without a socket (`PUNTMORE_RENDER_WS` blank).
6. **OddsPapi spend.** The 2-Up board refreshes round the clock (about 48–576 bet365 requests a day, calculated, not measured). The placer adds no OddsPapi spend of its own: it sees the live price on the phone at placement time and reports `gone` or re-prices locally, never by asking Render for a quote.
7. **Hermes advises, the placer decides** (register, 6 Nov 2026). A Finder row's `advised_stake` is advice; the placer's sizing across all sources is its own, set on a console page.

## Decisions so far (6 Nov 2026, Callum)

- **The Bet Placer is a black box for now.** Its outside is fixed: it reads the three boards' feed routes, sizes and places, keeps the only book, and posts `placed` / `gone` to Finder. Its inside (staking rules, the phone bot, the records, the console sizing page) is designed later. Two submodules are named: the **phone interface** and the **logic that controls staking and the interface**.
- **Phones are driven through DeviceFarmer** at a local port on the Hub: real phones, each holding bookmaker accounts in the bookmaker's app and in the phone's web browser; Callum can take manual control of any phone the same way.
- **bet365 bets go through the browser** on the phone, not the app.
- What exists: the placement engine on the Hub (`hub/puntmore_hub/placement/`: one placement per bet id, kill switch, dry-run, window, escalation; the bot an adapter stub); the staking code deleted from Render on 6 Nov 2026 (Kelly, ladder rounding, ceiling, growth cap, window, daily limit, per-client decision) is intact in git history at commit `f1f3172^` (`render/api/app/twoup/model.py`, `decision.py`, `ledger.py`) and is the starting point for the staking submodule; the Hub store's `bets` and `balances` tables; the Hub Console without a sizing page.
