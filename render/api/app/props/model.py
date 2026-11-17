"""Player props: Paddy Power against Pinnacle, pure (no request, no settings).

Design: docs/superpowers/specs/2026-11-17-player-props-design.md.

ONE STATEMENT, WHATEVER THE SHAPE. OddsPapi carries the same nine player
statistics in two shapes (`players-<stat>` with outcomes `0`, `1+`, `2+` ...,
and `playertotals-<stat>` with Over/Under at x.5), and each book may quote
either. Every quote is turned into one statement about a player in a fixture:

    at_least(stat, n)   the player records n or more
    fewer(stat, n)      the player records fewer than n

so a Paddy `players-shots` `2+` and a Pinnacle `playertotals-shots` Over 1.5
are the same bet. A selection is (fixture id, player id, stat, n): Paddy's
price is its `at_least` quote, and Pinnacle's fair needs BOTH its `at_least`
and its `fewer` quote at exactly that (player, stat, n), de-vigged with the
power method. A line Pinnacle does not quote is never estimated from a
neighbouring one: the selection is refused and counted.

The join is on OddsPapi's own fixture and player ids. No name matching.
"""
from dataclasses import dataclass, field
import re

from ..cards.oddspapi import SOCCER, _as_list, _first, _price
from ..odds.utils import devigd

AT_LEAST, FEWER = "at_least", "fewer"

# The nine statistics both shapes carry, in the catalogue's own spelling.
STATS = ("goals", "assists", "cards", "foulscommitted", "offsides", "saves", "shots",
         "shotsongoal", "tackles")
# How the page prints each stat after "N+" (goals and a single card have
# their own wording, `pick`).
LABELS = {
    "goals": "goals", "assists": "assists", "cards": "cards", "foulscommitted": "fouls",
    "offsides": "offsides", "saves": "saves", "shots": "shots",
    "shotsongoal": "shots on target", "tackles": "tackles",
}
# Both mean the whole match; halves and anything else are not read.
PERIODS = ("result", "fulltime")
# The reasons a Paddy selection is left out, every one counted per stat.
REASONS = ("no_fair_player", "no_fair_line", "fair_one_sided", "unnamed")

_AT_LEAST_RE = re.compile(r"^(\d+)\+$")


def _statement(market_type: str, line, outcome: str) -> tuple[str, str, int] | None:
    """(kind, stat, n) for one catalogue outcome, or None when it is not read:
    exact counts, first/last scorer, whole-number lines and anything unknown."""
    outcome = outcome.strip()
    low = outcome.lower()
    if market_type == "players-anytimegoalscorer":
        return {"yes": (AT_LEAST, "goals", 1), "no": (FEWER, "goals", 1)}.get(low)
    if market_type.startswith("players-"):
        stat = market_type[len("players-"):]
        if stat not in STATS:
            return None
        m = _AT_LEAST_RE.match(outcome)
        if m and int(m.group(1)) >= 1:
            return AT_LEAST, stat, int(m.group(1))
        if outcome == "0":
            return FEWER, stat, 1
        if stat == "cards":
            return {"yes": (AT_LEAST, "cards", 1), "no": (FEWER, "cards", 1)}.get(low)
        return None
    if market_type.startswith("playertotals-"):
        stat = market_type[len("playertotals-"):]
        try:
            line = float(line)
        except (TypeError, ValueError):
            return None
        twice = line * 2
        # x.5 only: a whole-number line can push, and is not the same bet.
        if stat not in STATS or line <= 0 or abs(twice - round(twice)) > 1e-9 \
                or round(twice) % 2 != 1:
            return None
        n = int(round(line + 0.5))
        return {"over": (AT_LEAST, stat, n), "under": (FEWER, stat, n)}.get(low)
    return None


def display_name(raw) -> str | None:
    """OddsPapi's "Surname, First" as "First Surname"; any other form as given."""
    name = " ".join(str(raw or "").split())
    if not name:
        return None
    surname, comma, first = name.partition(",")
    if comma and surname.strip() and first.strip():
        return f"{first.strip()} {surname.strip()}"
    return name.strip(", ") or None


def _is_football_prop(m: dict) -> bool:
    return bool(m.get("playerProp")) and str(_first(m, "sportId", "sport_id")) == str(SOCCER)


def statements(catalogue) -> dict[str, dict[str, tuple[str, str, int]]]:
    """{marketId: {outcomeId: (kind, stat, n)}} for every football player-prop
    outcome the table reads (the spec's statement table)."""
    out: dict[str, dict[str, tuple[str, str, int]]] = {}
    for m in _as_list(catalogue):
        if not _is_football_prop(m):
            continue
        if str(_first(m, "period", "periodName") or "").strip().lower() not in PERIODS:
            continue
        mtype = str(_first(m, "marketType", "type") or "").strip().lower()
        line = _first(m, "handicap", "line", "value")
        sides = {}
        for o in _as_list(_first(m, "outcomes") or []):
            oid = _first(o, "outcomeId", "id", "_id")
            st = _statement(mtype, line, str(_first(o, "outcomeName", "name") or ""))
            if oid is not None and st is not None:
                sides[str(oid)] = st
        mid = _first(m, "marketId", "id", "_id")
        if mid is not None and sides:
            out[str(mid)] = sides
    return out


def player_market_ids(catalogue) -> set[str]:
    """Every football player-prop market id, read or not: whether a book posts
    player markets at all, apart from whether this tool reads their shape."""
    return {str(_first(m, "marketId", "id", "_id")) for m in _as_list(catalogue)
            if _is_football_prop(m) and _first(m, "marketId", "id", "_id") is not None}


def book_odds(fx: dict, slug: str) -> dict:
    """One book's odds on a merged fixture. A plan can grant a book under a
    variant slug ("pinnacle+30") and the response may key it either way."""
    odds = fx.get("bookmakerOdds") or {}
    if not slug:
        return {}
    base = slug.split("+")[0]
    if odds.get(slug):
        return odds[slug]
    if odds.get(base):
        return odds[base]
    for key, book in odds.items():
        if str(key).split("+")[0] == base and book:
            return book
    return {}


Key = tuple[str, str, str, int]          # (fixture id, player id, stat, n)


@dataclass
class BookQuotes:
    """One book's player quotes: {(fixture, player, stat, n): {kind: price}}."""
    quotes: dict[Key, dict[str, float]] = field(default_factory=dict)
    # A statement quoted twice (both shapes): the first price is kept.
    duplicates: dict[str, int] = field(default_factory=dict)
    # Names carried on the price entries themselves, when the feed sends them.
    names: dict[str, str] = field(default_factory=dict)
    # Player-prop market ids posted with a price, read or not.
    player_markets: int = 0


def quotes(boards: dict[str, dict], index: dict, slug: str, *,
           fixtures: set[str] | None = None, player_markets: set[str] = frozenset()
           ) -> BookQuotes:
    """`slug`'s player quotes off the raw boards, for `fixtures` (all when None).
    A quote counts only when its entry is active and its price is above 1.0;
    player id "0" is the no-player entry of an ordinary market, never a player."""
    out = BookQuotes()
    for fid, fx in boards.items():
        if fixtures is not None and str(fid) not in fixtures:
            continue
        for mid, market in (book_odds(fx, slug).get("markets") or {}).items():
            outcomes = (market or {}).get("outcomes") or {}
            if str(mid) in player_markets and any(
                    _price({"players": {"0": e}})
                    for o in outcomes.values() for p, e in ((o or {}).get("players") or {}).items()
                    if str(p) != "0"):
                out.player_markets += 1
            sides = index.get(str(mid))
            if sides is None or (market or {}).get("marketActive") is False:
                continue
            for oid, outcome in outcomes.items():
                st = sides.get(str(oid))
                if st is None:
                    continue
                kind, stat, n = st
                for pid, entry in ((outcome or {}).get("players") or {}).items():
                    pid = str(pid)
                    if pid == "0" or not isinstance(entry, dict):
                        continue
                    price = _price({"players": {"0": entry}})
                    if price is None:
                        continue
                    name = display_name(entry.get("playerName"))
                    if name and pid not in out.names:
                        out.names[pid] = name
                    have = out.quotes.setdefault((str(fid), pid, stat, n), {})
                    if kind in have:
                        out.duplicates[stat] = out.duplicates.get(stat, 0) + 1
                        continue
                    have[kind] = price
    return out


@dataclass
class Bet:
    fixture_id: str
    player_id: str
    player: str
    stat: str
    n: int
    odds: float
    fair: float
    value: float
    pinnacle_margin: float

    @property
    def pick(self) -> str:
        return pick(self.player, self.stat, self.n)


def pick(player: str, stat: str, n: int) -> str:
    """How the page prints a selection: "Erling Haaland to score", "Bukayo
    Saka to be carded", "Erling Haaland 2+ shots on target"."""
    if stat == "goals":
        return f"{player} to score" if n == 1 else f"{player} {n}+ goals"
    if stat == "cards" and n == 1:
        return f"{player} to be carded"
    return f"{player} {n}+ {LABELS.get(stat, stat)}"


def empty_counts() -> dict[str, int]:
    return {"paddy": 0, "pinnacle_two_sided": 0, "matched": 0,
            **{r: 0 for r in REASONS}, "duplicates_paddy": 0, "duplicates_pinnacle": 0}


def join(paddy: BookQuotes, fair: BookQuotes, names: dict[str, str]
         ) -> tuple[list[Bet], dict[str, dict[str, int]]]:
    """Every Paddy `at_least` selection priced against Pinnacle's two sides at
    the same (fixture, player, stat, n), and the counts per stat: Paddy's
    selections, Pinnacle's two-sided ones, how many matched and why the rest
    did not. Every Paddy selection lands in exactly one of matched or a reason.
    `names` maps player id to the printed name; a player missing from it (and
    from the entries) is `unnamed`."""
    counts: dict[str, dict[str, int]] = {}

    def c(stat):
        return counts.setdefault(stat, empty_counts())

    fair_players = {(fid, pid, stat) for (fid, pid, stat, _n) in fair.quotes}
    for (fid, pid, stat, n), sides in fair.quotes.items():
        if AT_LEAST in sides and FEWER in sides:
            c(stat)["pinnacle_two_sided"] += 1
    for stat, k in paddy.duplicates.items():
        c(stat)["duplicates_paddy"] += k
    for stat, k in fair.duplicates.items():
        c(stat)["duplicates_pinnacle"] += k

    bets = []
    for key, sides in paddy.quotes.items():
        odds = sides.get(AT_LEAST)
        if odds is None:
            continue                     # a Paddy `fewer` quote is not a selection
        fid, pid, stat, n = key
        row = c(stat)
        row["paddy"] += 1
        have = fair.quotes.get(key) or {}
        if (fid, pid, stat) not in fair_players:
            row["no_fair_player"] += 1
            continue
        if not have:
            row["no_fair_line"] += 1
            continue
        if AT_LEAST not in have or FEWER not in have:
            # One side alone cannot have its margin removed.
            row["fair_one_sided"] += 1
            continue
        name = names.get(pid) or paddy.names.get(pid) or fair.names.get(pid)
        if not name:
            row["unnamed"] += 1
            continue
        fair_at_least, _fair_fewer, margin = devigd(have[AT_LEAST], have[FEWER])
        row["matched"] += 1
        bets.append(Bet(fixture_id=fid, player_id=pid, player=name, stat=stat, n=n,
                        odds=odds, fair=fair_at_least, value=odds / fair_at_least - 1,
                        pinnacle_margin=margin))
    return bets, counts
