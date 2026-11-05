"""Probe what corners/cards data each vendor actually serves, before building on it.

Run from api/:   .venv/bin/python tools/probe_corners.py

The Cards & Corners tool has never returned a row (docs/provider-verification.md).
Rebuilding it rests on three facts nobody has checked since the season started,
and two of them can be checked for almost nothing:

  1. Does the BETTINGISCOOL board carry corners/cards fixtures, and does its
     /api/odds serve totals for them?  <- the big one. The coupon tracker
     already sees these events and deliberately filters them OUT
     (EXCLUDED_LEAGUE_RE), so the board is known to list them; what is NOT
     known is whether they carry priceable markets. If they do, the FAIR side
     of this tool costs essentially nothing on a paid bettingiscool plan
     (500k tokens/DAY) instead of TheOddsApi's per-event billing.

  2. Does PADDY POWER price corners/cards now the league has started? The July
     sample said no, but it was 4 fixtures, mostly Brazilian, in a European
     off-season week, and these markets only appear near kickoff. That sample
     cannot answer the in-season question.

  3. What does one per-event sweep actually cost? Reported from the response
     headers rather than assumed.

COST: step 1 is bettingiscool tokens (~1 per row, against 500k/day). Step 2's
fixture list is FREE. Step 3 is the only real spend: ONE per-event call,
billed as (markets returned x regions) — a handful of credits, not a sweep.
Nothing here loops over a league.
"""
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

BIC = "https://api.bettingiscool.com"
ODDS_API = "https://api.the-odds-api.com/v4"
SOCCER = 29

# Paddy Power is a UK book. The shared client hardcodes regions=eu on per-event
# calls, which is fine only because an explicit `bookmakers` list overrides the
# region — worth being explicit here so the probe is not testing that quirk.
CORNER_CARD_MARKETS = "alternate_totals_corners,alternate_totals_cards"
BOOKS = "paddypower,pinnacle"


def rule(title):
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def probe_bettingiscool():
    rule("1. bettingiscool — does the board carry corners/cards fixtures?")
    key = settings.bettingiscool_api_key
    if not key:
        print("SKIPPED: BETTINGISCOOL_API_KEY is not set in .env")
        return

    today = datetime.now(timezone.utc).date()
    derived, plain = [], 0
    for offset in range(3):
        day = (today + timedelta(days=offset)).isoformat()
        resp = httpx.get(
            f"{BIC}/api/fixtures",
            params={"sport_id": SOCCER, "starts_from": day,
                    "starts_to": day, "limit": 1000},
            headers={"X-API-Key": key}, timeout=30,
        )
        resp.raise_for_status()
        for row in resp.json():
            blob = f"{row.get('league_name', '')} {row.get('runner_home', '')}"
            if any(w in blob.lower() for w in ("corner", "booking", "card")):
                derived.append(row)
            else:
                plain += 1
        print(f"  {day}: quota remaining {resp.headers.get('x-quota-remaining')}")

    print(f"\n  plain fixtures     : {plain}")
    print(f"  corners/cards ones : {len(derived)}")
    if not derived:
        print("\n  -> The board carries NO derived corners/cards fixtures.")
        print("     The fair side would have to come from TheOddsApi after all.")
        return

    leagues = Counter(r.get("league_name", "?") for r in derived)
    print("\n  leagues carrying them (top 10):")
    for name, n in leagues.most_common(10):
        print(f"    {n:4}  {name}")
    print("\n  sample fixtures:")
    for row in derived[:5]:
        print(f"    [{row.get('event_id')}] {row.get('runner_home')} vs "
              f"{row.get('runner_away')}  ({row.get('league_name')})")

    # The question that actually matters: are those events PRICED?
    rule("1b. bettingiscool — are those fixtures priceable?")
    sample = derived[0]
    resp = httpx.get(
        f"{BIC}/api/odds", params={"event_id": sample["event_id"]},
        headers={"X-API-Key": key}, timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    print(f"  event {sample['event_id']}: {sample.get('runner_home')} vs "
          f"{sample.get('runner_away')}")
    print(f"  {len(rows)} odds row(s)\n")
    markets = Counter(r.get("market") for r in rows if r.get("period") == 0)
    for market, n in markets.most_common():
        print(f"    {market:16} {n} line(s)")
    print("\n  full-match totals rows (the ones a corners tool would price off):")
    shown = 0
    for r in rows:
        if r.get("period") == 0 and r.get("market") == "totals":
            print(f"    line {r.get('line'):>6}  over {r.get('odds1')}  "
                  f"under {r.get('odds2')}")
            shown += 1
            if shown >= 8:
                break
    if not shown:
        print("    NONE — the fixture is listed but carries no totals.")


def probe_paddy_power():
    rule("2. TheOddsApi — does Paddy Power price corners/cards in-season?")
    key = settings.odds_api_key
    if not key:
        print("SKIPPED: ODDS_API_KEY is not set in .env")
        return

    # FREE endpoint — no quota cost.
    resp = httpx.get(f"{ODDS_API}/sports/soccer_epl/events",
                     params={"apiKey": key}, timeout=30)
    resp.raise_for_status()
    events = resp.json()
    if not events:
        print("  No EPL fixtures listed.")
        return
    print(f"  {len(events)} EPL fixture(s) upcoming (this call is free)")

    # These markets only appear NEAR kickoff, which is what the July sample
    # tripped over — so probe the SOONEST fixture, not an arbitrary one.
    event = min(events, key=lambda e: e["commence_time"])
    print(f"  probing the soonest: {event['home_team']} v {event['away_team']}"
          f"  ({event['commence_time']})")

    rule("3. One per-event call — the only real credit spend here")
    resp = httpx.get(
        f"{ODDS_API}/sports/soccer_epl/events/{event['id']}/odds",
        params={"apiKey": key, "regions": "uk", "markets": CORNER_CARD_MARKETS,
                "bookmakers": BOOKS, "oddsFormat": "decimal"},
        timeout=30,
    )
    print(f"  HTTP {resp.status_code}   "
          f"quota used={resp.headers.get('x-requests-used')} "
          f"remaining={resp.headers.get('x-requests-remaining')} "
          f"last-cost={resp.headers.get('x-requests-last')}")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:400]}")
        return

    books = resp.json().get("bookmakers", [])
    if not books:
        print("\n  NO bookmaker returned these markets on this fixture.")
        print("  Try again closer to kickoff — they post late.")
        return
    for bm in books:
        print(f"\n  {bm['key']}:")
        for market in bm.get("markets", []):
            outcomes = market.get("outcomes", [])
            print(f"    {market['key']}  ({len(outcomes)} outcomes)")
            for o in outcomes[:4]:
                print(f"      {o.get('name')} {o.get('point')} @ {o.get('price')}")

    have = {bm["key"] for bm in books}
    print("\n  VERDICT")
    print(f"    Paddy Power prices them: {'YES' if 'paddypower' in have else 'NO'}")
    print(f"    Pinnacle prices them   : {'YES' if 'pinnacle' in have else 'NO'}")
    if "paddypower" not in have:
        print("    Without a Paddy Power price there is no edge to compute —")
        print("    only a Pinnacle fair-odds display.")


if __name__ == "__main__":
    probe_bettingiscool()
    probe_paddy_power()
    print("\nDone. Paste the output back to decide the rebuild.\n")
