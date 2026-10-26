# Scraper handover (18 Oct 2026)

**Repo:** https://github.com/CMclarty/puntmore. Private; you need to be added as a collaborator.
**Branch:** [`scraper-v0.1`](https://github.com/CMclarty/puntmore/tree/scraper-v0.1). Not merged to `main`.
**Code:** [`local/scraper/`](https://github.com/CMclarty/puntmore/tree/scraper-v0.1/local/scraper)
**Setup and usage:** [`local/scraper/README.md`](https://github.com/CMclarty/puntmore/blob/scraper-v0.1/local/scraper/README.md)
**Project rules:** [`CLAUDE.md`](https://github.com/CMclarty/puntmore/blob/scraper-v0.1/CLAUDE.md). See the "scraper" and "Goalkeeper SAVES" entries.

## What it is
A Linux Docker service with one real Chrome, running on a virtual display. It
reads Paddy Power prices (saves, cards, corners) and Bookie Bashing fair saves
prices, which feed puntmore's Paddy tool. It runs on its own server and is
**not** part of the Render deploy.

Each target page is loaded once and kept open, and the scraper records what
the page itself receives. Pages are reloaded only on an interval or when they
go quiet. Every page load goes through a guard that enforces per-site budgets.

## Status
| Piece | State |
|---|---|
| Browser, kept-open pages, capture of what pages receive | Built and tested |
| Guard: budgets, Paddy quiet hours 01–07, cool-off | Built and tested |
| Discord/log alerts on a blocked or logged-out page load | Built and tested |
| Docker image: Xvfb, VNC on localhost, healthcheck, clean stop | Built and run locally against a fake site |
| CI to GHCR ([workflow](https://github.com/CMclarty/puntmore/blob/scraper-v0.1/.github/workflows/scraper-image.yml)) | Written, **never run** (it only runs on `main`) |
| Real Chrome install in the image | Untested (the sandbox couldn't reach Google) |
| Parsers `bb_saves`, `pp_match` ([`parsers.py`](https://github.com/CMclarty/puntmore/blob/scraper-v0.1/local/scraper/parsers.py)) | **Not written.** They need real dumps and samples first |
| Alert when an *already-open* page gets blocked | **Half done.** See open item 1 |
| Run against the real sites | **Never** |

The tests are in [`tests/`](https://github.com/CMclarty/puntmore/tree/scraper-v0.1/local/scraper/tests).
9 pass: guard and config units, plus a real browser against a fake site on
localhost.

## Commits on the branch
- [`335a583`](https://github.com/CMclarty/puntmore/commit/335a583): first hardening of the old Bookie Bashing script, since replaced
- [`eb68e6d`](https://github.com/CMclarty/puntmore/commit/eb68e6d): scraper v0.1
- [`bcd94a3`](https://github.com/CMclarty/puntmore/commit/bcd94a3): groundwork. Records 403/429s the open pages receive, and attaches screenshots to Discord alerts. The runner doesn't use either yet.

## Open items, in order
1. **Wire the open-page block check into the runner.** On each tick:
   - for each open tab, check `capture.refused_within(600) >= 3`, a Cloudflare page (`browser.blocked`), or a logout;
   - on any of those, call `_on_blocked(site, reason, now, tab)` and screenshot the page into `data/blocks/`;
   - send the Discord alert with the image;
   - alert once when a cool-off has served its 24 h and is waiting on `resume`, and once when it is running again.

   Add a fake-site test where `/api/prices` starts returning 429 after the tab
   is open. Also skip `_keep` for responses with status ≥ 400, so a refusal
   doesn't count as page activity.
2. **Merge to `main`.** CI then tests and publishes `ghcr.io/cmclarty/puntmore-scraper`. The push also redeploys Render, but nothing in the API changed apart from two comments.
3. **Server install** (README). Until step 2 is done, build the image on the server from the branch.
4. **Bookie Bashing's domain.** The user says it's reached at `bookiebashing.com`, and `config.ALLOWED_HOSTS` only has `bookiebashing.net`. Add `.com` once it's confirmed.
5. **Get real data.** Log in to Bookie Bashing once over VNC and run `dump bb-saves`. Let the Paddy targets run for a few hours, then collect `data/dumps/` and `data/samples/`. **Never** share `data/profile/`: it holds the login cookies.
6. **Write the parsers** from that data. `bb_saves` must produce the shape `POST /api/paddy/saves-fair` wants (see the docstring), and set `SAVES_INGEST_KEY` on Render and on the server.
7. **Puntmore ingest for Paddy prices.** There's no endpoint for the scraper's Paddy rows yet. Design it once `pp_match` exists.
8. **Optional: lower footprint.** Drop `every_min` on the Bookie Bashing target so it reloads only when the page goes quiet.

## Boundaries (agreed; keep them)
- It detects challenges and logouts and **stops**. It never answers a check, never types a password, and never fakes a fingerprint. No proxies or IP rotation, no clicking through menus to look human, and no other ways to avoid detection. If Paddy blocks this server, the answer is to slow down or get the data another way, not to work around the block.
- Per-site rules live in code (`sites.py`). Config can only make it slower.
- Targets are direct page URLs in `targets.toml`.

## Credentials (none in the repo)
On the server, in `/opt/puntmore-scraper/.env`: `VNC_PASSWORD`,
`SCRAPER_ALERT_WEBHOOK` (Discord), `SCRAPER_HEALTHCHECK_URL` (optional) and
`SAVES_INGEST_KEY`, which must match the value set on Render.

The Bookie Bashing login lives in the Chrome profile on the server's volume.

## Related, but separate
`~/projects/pp-value` was an earlier Paddy-only probe, on Cal's Mac only. It
has no git remote and wasn't reachable from this work. This scraper replaces
it. Its `fixtures/reference/` shop saves table may still be useful for
checking `pp_match`.
