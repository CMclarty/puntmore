"""Answer, in one run, whether OddsPapi carries the prices Puntmore needs.

    ODDSPAPI_KEY=... .venv/bin/python tools/probe_oddspapi.py

FOUR QUESTIONS, and all four are currently blocking a tool:

  1. Does PADDY POWER price CORNERS?   -> the cards/corners tool has never had a
                                          confirmed counterparty. The July
                                          finding was off-season and inconclusive.
  2. Does PADDY POWER price CARDS?     -> same.
  3. Does BET365 price the ACCA BOOST  -> /accabonus/diagnostics reports bet365
     sports?                              on ZERO of 625 events via TheOddsApi.
                                          If OddsPapi carries it, the AccaBonus
                                          tool has a route back.
  4. Does PINNACLE come through?       -> the fair-odds reference for both tools.

THE KEY IS READ FROM THE ENVIRONMENT and never written to a file. It is a
credential; keep it out of the repo and out of logs.

REQUEST BUDGET. The trial key allows 250 requests, so this counts every call and
stops at MAX_REQUESTS. It needs roughly 20. Nothing here loops over a league.

WRITTEN AGAINST DOCUMENTATION THIS ENVIRONMENT CANNOT REACH — oddspapi.io is
egress-blocked here, so the endpoint shapes below come from published examples
rather than from their docs directly. Every call therefore prints what it
actually got back on a mismatch, rather than assuming: if the shape is wrong,
the output says so and shows enough to correct it.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

BASE = "https://api.oddspapi.io/v4"
MAX_REQUESTS = 40           # well under the 250 allowance
TIMEOUT = 25.0

# Bookmakers to look for, by fuzzy name. Slugs are DISCOVERED from /bookmakers
# rather than guessed — a wrong slug returns nothing and looks like a book that
# does not price the market, which is the exact confusion this exists to end.
WANT_BOOKS = {
    "paddypower": ("paddy", "power"),
    "bet365": ("bet365", "bet 365"),
    "pinnacle": ("pinnacle",),
}

# Market names that mean corners / cards, matched case-insensitively.
CORNER_WORDS = ("corner",)
CARD_WORDS = ("card", "booking")

_calls = 0


def get(path: str, **params):
    """One GET, counted against the budget. Returns (data, error)."""
    global _calls
    if _calls >= MAX_REQUESTS:
        return None, f"request budget of {MAX_REQUESTS} reached"
    _calls += 1
    params["apiKey"] = os.environ["ODDSPAPI_KEY"]
    try:
        resp = httpx.get(f"{BASE}{path}", params=params, timeout=TIMEOUT)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text[:300]}"
    try:
        return resp.json(), None
    except ValueError:
        return None, f"not JSON: {resp.text[:300]}"


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def as_list(data):
    """The API may wrap a list in {"data": [...]}. Accept either."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "results", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def show_shape(label, data, limit=600):
    print(f"  [{label}] unexpected shape — first {limit} chars of the response:")
    print("   ", json.dumps(data)[:limit])


# --- 1. Which bookmakers exist, and what are their slugs? ------------------

def find_books():
    rule("1. Bookmakers — do Paddy Power, bet365 and Pinnacle exist here?")
    data, err = get("/bookmakers")
    if err:
        print(f"  FAILED: {err}")
        return {}
    books = as_list(data)
    if not books:
        show_shape("bookmakers", data)
        return {}

    print(f"  {len(books)} bookmaker(s) on this plan")
    found = {}
    for want, needles in WANT_BOOKS.items():
        for book in books:
            blob = " ".join(
                str(book.get(k, "")) for k in ("name", "slug", "title", "id")
            ).lower()
            if any(n in blob for n in needles):
                slug = book.get("slug") or book.get("id") or book.get("key")
                found[want] = slug
                print(f"    {want:12} FOUND  slug={slug!r}  name={book.get('name')!r}")
                break
        else:
            print(f"    {want:12} NOT LISTED on this plan")
    return found


# --- 2. Which sports and markets? ------------------------------------------

def find_sports():
    rule("2. Sports")
    data, err = get("/sports")
    if err:
        print(f"  FAILED: {err}")
        return {}
    sports = as_list(data)
    if not sports:
        show_shape("sports", data)
        return {}
    out = {}
    for sport in sports:
        name = str(sport.get("name") or sport.get("title") or "").lower()
        sid = sport.get("id") or sport.get("sportId")
        out[name] = sid
    for label in ("soccer", "football", "tennis", "basketball", "ice hockey",
                  "baseball", "american football", "mma", "mixed martial arts"):
        if label in out:
            print(f"    {label:20} id={out[label]}")
    return out


def corner_and_card_markets():
    rule("3. Markets — are corners and cards even modelled?")
    data, err = get("/markets")
    if err:
        print(f"  FAILED: {err}")
        return [], []
    markets = as_list(data)
    if not markets:
        show_shape("markets", data)
        return [], []
    corners, cards = [], []
    for m in markets:
        name = str(m.get("name") or m.get("title") or "").lower()
        mid = m.get("id") or m.get("marketId")
        if any(w in name for w in CORNER_WORDS):
            corners.append((mid, name))
        elif any(w in name for w in CARD_WORDS):
            cards.append((mid, name))
    print(f"  {len(markets)} market(s) total")
    print(f"  corners: {len(corners)}")
    for mid, name in corners[:8]:
        print(f"    {mid}  {name}")
    print(f"  cards/bookings: {len(cards)}")
    for mid, name in cards[:8]:
        print(f"    {mid}  {name}")
    return corners, cards


# --- 4. A real fixture, and who prices what on it --------------------------

def first_fixture(sport_id, label):
    now = datetime.now(timezone.utc)
    data, err = get(
        "/fixtures", sportId=sport_id,
        **{"from": now.isoformat(), "to": (now + timedelta(hours=48)).isoformat()},
    )
    if err:
        print(f"  [{label}] fixtures FAILED: {err}")
        return None
    fixtures = as_list(data)
    if not fixtures:
        print(f"  [{label}] no fixture in the next 48h")
        return None
    fx = fixtures[0]
    print(f"  [{label}] {len(fixtures)} fixture(s); probing "
          f"{fx.get('name') or fx.get('id')}")
    return fx


def odds_for(fixture, slugs, label):
    """Which of `slugs` priced this fixture, and on which markets."""
    fid = fixture.get("id") or fixture.get("fixtureId")
    data, err = get("/odds", fixtureId=fid, bookmakers=",".join(slugs))
    if err:
        print(f"  [{label}] odds FAILED: {err}")
        return {}
    book_odds = None
    if isinstance(data, dict):
        book_odds = data.get("bookmakerOdds") or data.get("data", {}).get("bookmakerOdds")
    if not isinstance(book_odds, dict):
        show_shape(f"{label} odds", data)
        return {}

    out = {}
    for slug, payload in book_odds.items():
        markets = (payload or {}).get("markets") or {}
        names = []
        for mid, m in markets.items():
            names.append(str((m or {}).get("name") or mid).lower())
        out[slug] = names
        print(f"    {slug:16} {len(names)} market(s)")
    return out


def main():
    if not os.environ.get("ODDSPAPI_KEY"):
        sys.exit("Set ODDSPAPI_KEY in the environment (do not paste it into a file).")

    slugs = find_books()
    sports = find_sports()
    corners, cards = corner_and_card_markets()

    soccer_id = sports.get("soccer") or sports.get("football")

    rule("4. Paddy Power on a real soccer fixture — corners and cards?")
    if soccer_id is None:
        print("  no soccer sport id — skipping")
    else:
        fx = first_fixture(soccer_id, "soccer")
        if fx:
            want = [s for s in (slugs.get("paddypower"), slugs.get("pinnacle")) if s]
            got = odds_for(fx, want, "soccer") if want else {}
            for slug, names in got.items():
                has_c = [n for n in names if any(w in n for w in CORNER_WORDS)]
                has_k = [n for n in names if any(w in n for w in CARD_WORDS)]
                print(f"      {slug}: corners={len(has_c)} cards={len(has_k)}")
                for n in (has_c + has_k)[:6]:
                    print(f"        - {n}")

    rule("5. bet365 on one fixture of each Acca Boost sport")
    if not slugs.get("bet365"):
        print("  bet365 is not listed on this plan — nothing to test.")
    else:
        want = [s for s in (slugs.get("bet365"), slugs.get("pinnacle")) if s]
        for label in ("tennis", "basketball", "ice hockey", "baseball",
                      "american football", "mma", "mixed martial arts"):
            sid = sports.get(label)
            if sid is None:
                continue
            fx = first_fixture(sid, label)
            if fx:
                odds_for(fx, want, label)

    rule("SUMMARY")
    print(f"  requests used: {_calls} of {MAX_REQUESTS} (key allowance 250)")
    print(f"  paddypower slug : {slugs.get('paddypower') or 'NOT ON PLAN'}")
    print(f"  bet365 slug     : {slugs.get('bet365') or 'NOT ON PLAN'}")
    print(f"  pinnacle slug   : {slugs.get('pinnacle') or 'NOT ON PLAN'}")
    print("\n  Paste this whole output back to decide what each tool does next.\n")


if __name__ == "__main__":
    main()
