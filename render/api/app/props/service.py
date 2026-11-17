"""Player props: the feed and its diagnostics, off the boards the Paddy sweep
already holds (`app.state.cards_client`, an `OddsPapiPaddySource`).

NO ODDS REQUEST OF ITS OWN. Both books' raw boards (`.boards`, merged per
fixture) and the fixture list (`.fixtures`) come from the Paddy sweep, which
asks OddsPapi for each book's whole board per tournament chunk, player markets
included. Like the Paddy feed (`cards/feed.py::build_feed`), a read first calls
`get_events()`, which reads through the shared client's cache and waits for a
sweep already running, so the tool is never read off an empty board at boot.
The one request this tool adds is `/players` for names, cached a day by the
client and asked only when a priced selection's player has no name on its
price; a failure there is held 10 minutes (every attempt is billed) and costs
the feed only those players, counted `unnamed`.
"""
from datetime import datetime, timezone
import logging

from ..cards.oddspapi import FAILED_HOLD, _as_list, _first
from ..config import settings
from ..schemas import PropsBetOut, PropsFeedItem
from ..ttl_cache import MISS, TTLCache
from . import model

logger = logging.getLogger("uvicorn.error")

# A failed /players held per client, so a page polling every minute does not
# re-send a billed failure; and the last parse per client, so a cached
# response is not walked again on every request.
_NAMES_FAILED = TTLCache(ttl=FAILED_HOLD, maxsize=8)
_NAMES_PARSED = TTLCache(ttl=24 * 3600, maxsize=8)


def player_names(data) -> dict[str, str]:
    """{playerId: "First Surname"} from GET /players. The live answer is a list
    of rows each holding `players: [{playerId, playerName}]`; a flat list of
    players, a wrapped list and players keyed by id are read too."""
    if isinstance(data, dict) and "players" in data:
        rows = [data]
    else:
        rows = _as_list(data)
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        inner = row.get("players")
        for p in (_as_list(inner) if inner is not None else [row]):
            if not isinstance(p, dict):
                continue
            pid = _first(p, "playerId", "id", "_id")
            name = model.display_name(_first(p, "playerName", "name"))
            if pid is not None and name:
                out.setdefault(str(pid), name)
    return out


class Names:
    """Player names, fetched only when first asked for: when Pinnacle prices
    nothing, nothing is asked."""

    def __init__(self, client):
        self._client = client
        self._names: dict[str, str] | None = None
        self.error: str | None = None

    @property
    def fetched(self) -> bool:
        return self._names is not None

    def __len__(self) -> int:
        return len(self._names or {})

    def get(self, pid: str) -> str | None:
        if self._names is None:
            self._names = self._load()
        return self._names.get(str(pid))

    def _load(self) -> dict[str, str]:
        key = id(self._client)
        held = _NAMES_FAILED.get(key)
        if held is not MISS:
            self.error = held
            return {}
        try:
            raw = self._client.players()
        except Exception as exc:
            self.error = f"/players: {exc}"[:300]
            _NAMES_FAILED.set(key, self.error)
            logger.warning("props: player names failed, those players are left out: %s", exc)
            return {}
        hit = _NAMES_PARSED.get(key)
        if hit is not MISS and hit[0] is raw:
            return hit[1]
        names = player_names(raw)
        _NAMES_PARSED.set(key, (raw, names))
        return names


def fair_slug(source) -> str | None:
    """The fair book's slug as the Paddy sweep resolves it (`pinnacle+30` on a
    delayed plan), or None when ODDSPAPI_FAIR_BOOK is blank."""
    wanted = (settings.oddspapi_fair_book or "").strip()
    if not wanted:
        return None
    try:
        return source._client.resolve_book(wanted)
    except Exception:
        return wanted


class Board:
    """One read of the props board: the priced bets and everything the
    diagnostics report about how they were reached."""

    def __init__(self):
        self.bets: list[model.Bet] = []
        self.counts: dict[str, dict[str, int]] = {}
        self.fixtures: dict[str, dict] = {}
        self.paddy = model.BookQuotes()
        self.fair = model.BookQuotes()
        self.paddy_slug: str | None = None
        self.fair_slug: str | None = None
        self.names: Names | None = None
        self.read_markets = 0
        self.catalogue_error: str | None = None
        self.priced_at = None


def build(source, now: datetime | None = None) -> Board:
    """Price the props board off `source`'s last sweep (running one first, as
    the Paddy feed does). Never raises for a vendor failure: the board comes
    back empty and the diagnostics name why."""
    board = Board()
    if source is None or not hasattr(source, "boards"):
        return board
    try:
        source.get_events()
    except Exception as exc:              # the sweep logs and records its own failures
        logger.warning("props: the Paddy sweep failed: %s", exc)
    now = now or datetime.now(timezone.utc)
    board.priced_at = getattr(source, "priced_at", None)
    board.fixtures = {str(f["id"]): f for f in getattr(source, "fixtures", None) or []
                      if f.get("start") is not None and f["start"] > now}
    boards = getattr(source, "boards", None) or {}
    board.paddy_slug = getattr(source, "priced_book", None) or getattr(source, "book", None)
    board.fair_slug = fair_slug(source)
    board.names = Names(source._client)
    if not board.fixtures or not boards:
        return board
    try:
        # The sweep's own catalogue, cached a day by the client.
        catalogue = source._client.catalogue()
    except Exception as exc:
        board.catalogue_error = str(exc)[:300]
        return board
    index = model.statements(catalogue)
    props = model.player_market_ids(catalogue)
    board.read_markets = len(index)
    listed = set(board.fixtures)
    board.paddy = model.quotes(boards, index, board.paddy_slug, fixtures=listed,
                               player_markets=props)
    if board.fair_slug:
        board.fair = model.quotes(boards, index, board.fair_slug, fixtures=listed,
                                  player_markets=props)
    board.bets, board.counts = model.join(board.paddy, board.fair, board.names)
    return board


def _kickoff(start: datetime) -> str:
    return start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_feed(source, min_edge: float = 0.0, now: datetime | None = None) -> list[PropsFeedItem]:
    """One item per fixture still to kick off with a bet at or above
    `min_edge`, soonest first; each fixture's bets best value first."""
    board = build(source, now)
    groups: dict[str, list[model.Bet]] = {}
    for bet in board.bets:
        if bet.value >= min_edge and bet.fixture_id in board.fixtures:
            groups.setdefault(bet.fixture_id, []).append(bet)
    out = []
    for fid, bets in groups.items():
        fx = board.fixtures[fid]
        bets.sort(key=lambda b: -b.value)
        out.append(PropsFeedItem(
            event_id=fid,
            match=f"{fx['home']} vs {fx['away']}",
            home=fx["home"],
            away=fx["away"],
            commence_time=_kickoff(fx["start"]),
            priced_at=board.priced_at,
            best_value=round(bets[0].value, 6),
            bets=[PropsBetOut(
                player=b.player, player_id=b.player_id, stat=b.stat, n=b.n, pick=b.pick,
                odds=b.odds, fair=round(b.fair, 4), value=round(b.value, 6),
                pinnacle_margin=round(b.pinnacle_margin, 6),
            ) for b in bets],
        ))
    out.sort(key=lambda item: (board.fixtures[item.event_id]["start"], item.event_id))
    return out


def _totals(counts: dict[str, dict[str, int]]) -> dict[str, int]:
    total = model.empty_counts()
    for row in counts.values():
        for k, v in row.items():
            total[k] = total.get(k, 0) + v
    return total


def _reasons(total: dict[str, int]) -> str:
    parts = [f"{total[r]} {r}" for r in model.REASONS if total.get(r)]
    return ", ".join(parts) or "none"


def _verdict(source, board: Board, total: dict[str, int]) -> str:
    sweep = dict(getattr(source, "last_sweep", None) or {}) if source is not None else {}
    paddy, matched = total["paddy"], total["matched"]
    fixtures = len(board.fixtures)
    if source is None or not hasattr(source, "boards"):
        return ("OddsPapi is not configured: set ODDSPAPI_API_KEY. Neither book's "
                "prices are fetched.")
    if not getattr(source, "boards", None) and sweep.get("error"):
        return f"The Paddy sweep failed before any board arrived: {sweep['error']}"
    if not sweep and not getattr(source, "boards", None):
        return "No Paddy sweep has run yet. Open the feed again in a minute."
    if not fixtures:
        return "No fixture on the Paddy board is still to kick off, so there is nothing to price."
    if board.catalogue_error:
        return f"The market catalogue could not be read: {board.catalogue_error}"
    if not board.read_markets:
        return "The catalogue lists no football player market this tool reads."
    if not paddy:
        if board.paddy.player_markets:
            return (f"Paddy Power posts {board.paddy.player_markets} player market(s) on "
                    f"{fixtures} fixture(s), none in a shape this tool reads.")
        return (f"Paddy Power is posting no player markets on the {fixtures} fixture(s) "
                f"still to kick off.")
    if not board.fair_slug:
        return (f"No fair book: ODDSPAPI_FAIR_BOOK is blank, so Paddy's {paddy} "
                f"selection(s) have nothing to price against.")
    if not board.fair.player_markets:
        base = board.fair_slug.split("+")[0]
        errs = [e for e in sweep.get("errors") or [] if str(e).startswith(base)]
        return (f"Pinnacle is posting no player markets: Paddy's {paddy} selection(s) on "
                f"{fixtures} fixture(s) have nothing to price against."
                + (f" Its odds request failed: {errs[0][:240]}" if errs else ""))
    if not board.fair.quotes:
        return (f"Pinnacle posts {board.fair.player_markets} player market(s), none in a "
                f"shape this tool reads.")
    if not matched:
        return (f"Pinnacle prices players, but none of Paddy's {paddy} selection(s) at the "
                f"same line with both sides: {_reasons(total)}.")
    value = sum(1 for b in board.bets if b.value >= 0)
    return (f"{matched} of {paddy} Paddy selection(s) priced against Pinnacle, {value} at or "
            f"above fair. Left out: {_reasons(total)}.")


def diagnose(source, now: datetime | None = None) -> dict:
    """Why the props page shows what it shows, in one request: per stat, Paddy's
    selections, Pinnacle's two-sided ones, how many matched and why the rest
    did not, and a one-line verdict."""
    board = build(source, now)
    total = _totals(board.counts)
    sweep = dict(getattr(source, "last_sweep", None) or {}) if source is not None else {}
    return {
        "verdict": _verdict(source, board, total),
        "configured": source is not None and hasattr(source, "boards"),
        "books": {"paddy": board.paddy_slug, "fair": board.fair_slug},
        "fixtures_to_kick_off": len(board.fixtures),
        "priced_at": board.priced_at,
        "catalogue": {"markets_read": board.read_markets, "error": board.catalogue_error},
        "player_markets_posted": {"paddy": board.paddy.player_markets,
                                  "pinnacle": board.fair.player_markets},
        "names": {
            "fetched": board.names is not None and board.names.fetched,
            "known": len(board.names) if board.names is not None else 0,
            "error": board.names.error if board.names is not None else None,
        },
        "stats": {stat: board.counts[stat] for stat in model.STATS if stat in board.counts},
        "totals": total,
        "sweep": {k: sweep.get(k) for k in ("source", "fair_source", "fixtures_in_window",
                                            "failed", "errors", "error", "requests_today")
                  if k in sweep},
    }
