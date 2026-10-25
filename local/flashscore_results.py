"""FlashScore results -> puntmore's AW 26 slips.

Runs on YOUR PC (playwright), like the Bookie Bashing scripts — but FlashScore
needs no login, so no profile/login step. It scrapes each recent finished
match's stats and POSTs them to POST /api/aw26/results, which settles the slips
deterministically (corners/goals/saves vs the leg's line/threshold). ONE source,
applied directly — no second-source double-check (Callum, 22 Oct 2026).

Confirmed page structure (22 Oct 2026):
  - league results list: /football/<country>/<league>/results/
  - match score  : header .detailScore__wrapper ("0-1"), teams from
                   .duelParticipant__home/.away .participant__participantName
                   (HEADER order, not the URL slug order)
  - corners+saves: /match/.../summary/stats/  rows "home \\n LABEL \\n away"
                   (labels "Corner kicks", "Goalkeeper saves")
  - cards        : shown as timeline incidents, NOT in stats. Not scraped yet —
                   sent as null, so the server leaves cards legs pending (never
                   assumed). TODO: read the incident icons for a card count.

WHICH LEAGUES
  Unless FLASHSCORE_LEAGUES / --leagues names them, the leagues come from the
  server: GET /api/aw26/open-leagues lists the events on still-pending legs
  and their leagues (sport keys, from the value snapshots). Nothing pending ->
  nothing scraped. An event with no known league, a sport key with no path in
  SPORT_KEY_LEAGUES, or the server unreachable -> the full DEFAULT_LEAGUES, so
  a leg is never left unsettled for want of a league.

COMMANDS
    check   Scrape and PRINT the match stats (no POST) — verify the read.
    once    Scrape and POST once (the systemd user timer runs this every 2h,
            13:00-00:00 London — local/systemd/README.md).
    run     The same, then again every --every minutes.

SETTINGS (environment variables, or flags)
    PUNTMORE_URL         default https://puntmore.com
    SAVES_INGEST_KEY     the same key as the server (one key for the BB + FS scripts)
    FLASHSCORE_LEAGUES   comma list of result-page paths; overrides the server's list
    FLASHSCORE_MAX       most-recent matches to take per league (default 20)
"""
import argparse
import os
import sys
import time
from datetime import datetime

BASE = "https://www.flashscore.com"
DEFAULT_LEAGUES = [
    "england/premier-league", "england/championship", "spain/laliga",
    "italy/serie-a", "germany/bundesliga", "france/ligue-1",
    "europe/champions-league", "europe/europa-league",
]
# The sweep's sport keys (api/app/cards/oddspapi.py TOURNAMENT_SPORT_KEYS) ->
# FlashScore result-page paths. Only the paths DEFAULT_LEAGUES already uses —
# the rest are unverified, so their keys fall back to the full list.
SPORT_KEY_LEAGUES = {
    "soccer_epl": "england/premier-league",
    "soccer_efl_champ": "england/championship",
    "soccer_spain_la_liga": "spain/laliga",
    "soccer_italy_serie_a": "italy/serie-a",
    "soccer_germany_bundesliga": "germany/bundesliga",
    "soccer_france_ligue_one": "france/ligue-1",
    "soccer_uefa_champs_league": "europe/champions-league",
    "soccer_uefa_europa_league": "europe/europa-league",
}
STAT_LABELS = {"Corner kicks": "corners", "Goalkeeper saves": "saves"}


def _accept_cookies(page):
    for sel in ["#onetrust-accept-btn-handler", "button:has-text('Accept')"]:
        try:
            if page.locator(sel).first.is_visible(timeout=1500):
                page.locator(sel).first.click()
                page.wait_for_timeout(600)
                return
        except Exception:
            pass


def _int(text):
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


def _match_links(page, league, limit):
    page.goto(f"{BASE}/football/{league}/results/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    _accept_cookies(page)
    page.wait_for_timeout(1000)
    hrefs = page.eval_on_selector_all(
        ".event__match a[href*='/match/']",
        "els => [...new Set(els.map(e => e.href.split('?')[0]))]")
    return hrefs[:limit]


def _scrape_match(page, base_url):
    """One finished match -> the dict POST /api/aw26/results wants, or None."""
    page.goto(base_url + "summary/stats/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    try:
        home = page.locator(".duelParticipant__home .participant__participantName").first.inner_text().strip()
        away = page.locator(".duelParticipant__away .participant__participantName").first.inner_text().strip()
    except Exception:
        return None
    if not home or not away:
        return None
    out = {"home": home, "away": away, "cards_home": None, "cards_away": None}
    # score -> goals (header "0-1" / "2 - 1")
    try:
        score = page.locator(".detailScore__wrapper").first.inner_text()
        nums = [n for n in score.replace("\n", " ").split() if n.lstrip("-").isdigit()]
        if len(nums) >= 2:
            out["goals_home"], out["goals_away"] = int(nums[0]), int(nums[1])
    except Exception:
        pass
    # corners + saves from the stats rows
    lines = [l.strip() for l in page.locator("body").inner_text().splitlines() if l.strip()]
    for i, line in enumerate(lines):
        kind = STAT_LABELS.get(line)
        if kind and 0 < i < len(lines) - 1:
            out[f"{kind}_home"] = _int(lines[i - 1])
            out[f"{kind}_away"] = _int(lines[i + 1])
    return out


def scrape(leagues, limit):
    from playwright.sync_api import sync_playwright
    matches, seen = [], set()
    with sync_playwright() as p:
        ctx = p.chromium.launch(headless=True)
        page = ctx.new_context(locale="en-GB", viewport={"width": 1400, "height": 1000}).new_page()
        for league in leagues:
            try:
                links = _match_links(page, league, limit)
            except Exception as exc:
                print(f"  ! {league}: {exc}", file=sys.stderr)
                continue
            for base_url in links:
                if base_url in seen:
                    continue
                seen.add(base_url)
                try:
                    m = _scrape_match(page, base_url)
                except Exception as exc:
                    print(f"  ! {base_url}: {exc}", file=sys.stderr)
                    continue
                if m:
                    matches.append(m)
        ctx.close()
    return matches


def leagues_for(open_body):
    """The league paths to scrape, from GET /api/aw26/open-leagues' body.
    [] when nothing is pending; DEFAULT_LEAGUES when any league is unknown."""
    if not open_body.get("events"):
        return []
    paths = [SPORT_KEY_LEAGUES.get(k) for k in open_body.get("sport_keys", [])]
    if open_body.get("unknown") or None in paths:
        return list(DEFAULT_LEAGUES)
    return sorted(set(paths))


def _open_leagues(server, key):
    import httpx
    r = httpx.get(server.rstrip("/") + "/api/aw26/open-leagues",
                  headers={"X-API-Key": key}, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(matches, server, key):
    import httpx
    r = httpx.post(server.rstrip("/") + "/api/aw26/results",
                   json={"matches": matches}, headers={"X-API-Key": key}, timeout=60)
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["check", "once", "run"])
    ap.add_argument("--server", default=os.environ.get("PUNTMORE_URL", "https://puntmore.com"))
    ap.add_argument("--key", default=os.environ.get("SAVES_INGEST_KEY"))
    ap.add_argument("--leagues", default=os.environ.get("FLASHSCORE_LEAGUES"))
    ap.add_argument("--max", type=int, default=int(os.environ.get("FLASHSCORE_MAX", "20")))
    ap.add_argument("--every", type=float, default=12 * 60, help="minutes between runs")
    a = ap.parse_args()
    if not a.key and a.command != "check":
        sys.exit("Set SAVES_INGEST_KEY or pass --key.")
    fixed = [s.strip() for s in a.leagues.split(",")] if a.leagues else None

    while True:
        stamp = datetime.now().strftime("%d %b %H:%M")
        leagues = fixed
        if leagues is None and not a.key:          # `check` with no key: read them all
            leagues = list(DEFAULT_LEAGUES)
        if leagues is None:
            try:
                leagues = leagues_for(_open_leagues(a.server, a.key))
            except Exception as exc:
                print(f"{stamp}  open legs unavailable ({exc}); reading every league",
                      file=sys.stderr)
                leagues = list(DEFAULT_LEAGUES)
        if not leagues:
            print(f"{stamp}  no pending legs; nothing to read")
            if a.command != "run":
                return
            time.sleep(a.every * 60)
            continue
        matches = scrape(leagues, a.max)
        if a.command == "check":
            for m in matches:
                print(m)
            print(f"\n{stamp}  scraped {len(matches)} finished match(es) (not posted)")
            return
        if not matches:
            print(f"{stamp}  no finished matches found in {len(leagues)} league(s)")
            if a.command == "once":
                return
            time.sleep(a.every * 60)
            continue
        try:
            body = _post(matches, a.server, a.key)
            print(f"{stamp}  posted {len(matches)}, resolved {body['resolved']} leg(s), "
                  f"settled {body['settled']} slip(s)")
        except Exception as exc:
            if a.command == "once":
                sys.exit(f"failed: {exc}")
            print(f"{stamp}  failed: {exc}")
        if a.command == "once":
            return
        time.sleep(a.every * 60)


if __name__ == "__main__":
    main()
