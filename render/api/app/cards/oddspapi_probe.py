"""Read-only looks at what OddsPapi carries, for learning a market before any
code prices it (`GET /api/paddy/oddspapi-probe`). Every billable call goes
through `OddsPapiClient.get`, so it is spaced, counted and stopped by the
daily budget like the sweep's own. Nothing here prices or stores anything.

    what=sports                          the sports list (1 request)
    what=tournaments&sport=10&find=nba   tournaments whose entry mentions a word (1)
    what=catalogue&sport=10              the market catalogue, grouped by type (cached)
    what=book&book=pinnacle&tournament=17,8
                                         what that book prices there, by market
                                         type: player props apart, one raw sample
                                         each (1, cached as the sweep's)
    what=raw&path=/players&params=a=1    a listed endpoint's answer, cut to `limit`
"""
import json
from urllib.parse import parse_qsl

from .oddspapi import _as_list, _first, _price

RAW_PATHS = ("/sports", "/tournaments", "/fixtures", "/fixture", "/players",
             "/participants", "/bookmakers")


def _line(m: dict):
    return _first(m, "handicap", "line", "value", "points", "total")


def catalogue_summary(catalogue, sport: int | None, player_only: bool) -> dict:
    """The catalogue by (type, period): how many market ids, the name, the
    lines and the outcome names. `sport` None = every sport, counted."""
    by_sport: dict[str, int] = {}
    groups: dict[tuple, dict] = {}
    for m in _as_list(catalogue):
        sid = m.get("sportId")
        by_sport[str(sid)] = by_sport.get(str(sid), 0) + 1
        if sport is not None and sid != sport:
            continue
        if player_only and not m.get("playerProp"):
            continue
        key = (sid, str(_first(m, "marketType", "type") or "?"),
               str(_first(m, "period", "periodName") or ""), bool(m.get("playerProp")))
        g = groups.setdefault(key, {"markets": 0, "names": set(), "lines": set(),
                                    "outcomes": set()})
        g["markets"] += 1
        g["names"].add(str(_first(m, "marketName", "name") or ""))
        if _line(m) is not None:
            g["lines"].add(_line(m))
        for o in m.get("outcomes") or []:
            g["outcomes"].add(str(_first(o, "outcomeName", "name") or ""))
    return {
        "entries_by_sport": by_sport,
        "groups": [
            {"sport": sid, "type": t, "period": p, "player_prop": pp, "markets": g["markets"],
             "names": sorted(g["names"])[:6],
             "lines": sorted(g["lines"], key=float)[:40],
             "outcomes": sorted(g["outcomes"])[:12]}
            for (sid, t, p, pp), g in sorted(groups.items(), key=lambda kv: str(kv[0]))
        ],
    }


def _full_index(catalogue) -> dict[str, dict]:
    return {str(_first(m, "marketId", "id", "_id")): m for m in _as_list(catalogue)}


def book_summary(fixtures, catalogue, slug: str) -> dict:
    """What one book prices across these fixtures, by catalogue type: fixtures,
    market ids, lines, outcome names, how many players, and whether a market
    is priced on one side or more. One raw market per type, as it arrived."""
    index = _full_index(catalogue)
    groups: dict[tuple, dict] = {}
    with_prices = 0
    listed = 0
    book_keys: set[str] = set()
    unknown = 0
    for fx in _as_list(fixtures):
        listed += 1
        odds = fx.get("bookmakerOdds") or {}
        book_keys.update(odds.keys())
        book = odds.get(slug) or odds.get(slug.split("+")[0]) or {}
        markets = book.get("markets") or {}
        if not markets:
            continue
        with_prices += 1
        seen_here = set()
        for mid, market in markets.items():
            info = index.get(str(mid))
            if info is None:
                unknown += 1
                continue
            key = (str(_first(info, "marketType", "type") or "?"),
                   str(_first(info, "period", "periodName") or ""), bool(info.get("playerProp")))
            g = groups.setdefault(key, {
                "fixtures": 0, "markets": 0, "names": set(), "lines": set(), "outcomes": set(),
                "players": set(), "sides_priced": {}, "sample": None})
            if key not in seen_here:
                seen_here.add(key)
                g["fixtures"] += 1
            g["markets"] += 1
            g["names"].add(str(_first(info, "marketName", "name") or ""))
            if _line(info) is not None:
                g["lines"].add(_line(info))
            names = {str(_first(o, "outcomeId", "id")): str(_first(o, "outcomeName", "name") or "")
                     for o in info.get("outcomes") or []}
            outcomes = market.get("outcomes") or {}
            priced = 0
            for oid, outcome in outcomes.items():
                g["outcomes"].add(names.get(str(oid), f"?{oid}"))
                players = (outcome or {}).get("players") or {}
                g["players"].update(p for p in players if str(p) != "0")
                if players:
                    priced += any(_price({"players": {"0": e}}) for e in players.values())
                else:
                    priced += bool(_price(outcome))
            g["sides_priced"][str(priced)] = g["sides_priced"].get(str(priced), 0) + 1
            if g["sample"] is None and priced:
                g["sample"] = {"fixtureId": _first(fx, "fixtureId", "id"), "marketId": mid,
                               "catalogue": info, "market": market}
    report = []
    for (t, p, pp), g in sorted(groups.items(), key=lambda kv: (not kv[0][2], kv[0])):
        report.append({
            "type": t, "period": p, "player_prop": pp, "fixtures": g["fixtures"],
            "markets": g["markets"], "names": sorted(g["names"])[:6],
            "lines": sorted(g["lines"], key=float)[:40], "outcomes": sorted(g["outcomes"])[:12],
            "players": len(g["players"]), "sides_priced": g["sides_priced"],
            "sample": (g["sample"] if len(json.dumps(g["sample"], default=str)) <= 20000
                       else "sample too large"),
        })
    return {"book": slug, "book_keys_in_response": sorted(book_keys), "fixtures_listed": listed,
            "fixtures_with_prices": with_prices, "markets_not_in_catalogue": unknown,
            "market_types": report}


def probe(client, what: str, *, sport: int | None = None, find: str = "",
          book: str = "paddypower", tournament: str = "", path: str = "", params: str = "",
          limit: int = 50, player_only: bool = True) -> dict:
    if what == "sports":
        return {"sports": client.get("/sports")}
    if what == "tournaments":
        if sport is None:
            return {"error": "tournaments needs sport=<id>"}
        rows = _as_list(client.get("/tournaments", sportId=sport))
        words = [w.strip().lower() for w in find.split(",") if w.strip()]
        hits = [r for r in rows
                if not words or any(w in json.dumps(r, default=str).lower() for w in words)]
        return {"sport": sport, "tournaments": len(rows), "matched": len(hits),
                "rows": hits[:limit]}
    if what == "catalogue":
        return catalogue_summary(client.catalogue(), sport, player_only)
    if what == "book":
        ids = [int(t) for t in tournament.split(",") if t.strip().isdigit()]
        if not ids:
            return {"error": "book needs tournament=<id>[,<id>]"}
        slug = client.resolve_book(book)
        out = book_summary(client.odds_by_tournaments(ids, bookmaker=slug),
                           client.catalogue(), slug)
        out["tournaments"] = ids
        return out
    if what == "raw":
        if path not in RAW_PATHS:
            return {"error": f"raw reads one of {', '.join(RAW_PATHS)}"}
        data = client.get(path, **dict(parse_qsl(params)))
        rows = _as_list(data)
        return {"path": path, "rows": len(rows), "data": rows[:limit] if rows else data}
    return {"error": "what is one of sports, tournaments, catalogue, book, raw"}
