"""First-goalscorer value finder — bet365 vs de-vigged Pinnacle.

First goalscorer is a COMPLETE market: exactly one outcome wins (a given player
scores first, or nobody does), so the outcomes sum to a full book and de-vig
cleanly with the power method — the same maths AccaBonus uses on h2h, just with
N outcomes instead of 2.

For an event carrying both books' first-goalscorer prices:

1. Remove Pinnacle's margin across ALL its outcomes (players + "no goalscorer")
   to get fair odds per outcome (odds/utils.py::devigd_multi).
2. Fuzzy-match each bet365 player name to a Pinnacle one — the books spell
   names differently ("M. Salah" vs "Mohamed Salah") — and take edge =
   bet365 / fair - 1 (positive = bet365 is longer than fair — value at bet365).
   Matching is biased for precision: an unmatched bet365 player is dropped, not
   guessed, because a wrong pairing invents value that isn't there.
3. Return the player rows with edge >= min_edge, best-edge-first. The
   "no goalscorer" outcome participates in the de-vig but is never a value row.

⚠️ SCHEMA NOTE: the exact per-event player-prop shape (whether the player name
lands in outcome["description"] or outcome["name"]) has NOT been verified
against live data — first-goalscorer only posts near kickoff for major events,
so there was no in-season fixture to probe. _player_prices reads both fields;
verify against a real response before trusting the numbers (docs/
provider-verification.md).
"""
import difflib
from dataclasses import dataclass

from ..normalise import normalise_player
from ..odds.utils import devigd_multi

FIRST_GOALSCORER_MARKET = "player_first_goal_scorer"

# Default confidence floor for pairing a bet365 name to a Pinnacle name. Biased
# for PRECISION: a missed match just drops a row, but a false match invents fair
# odds for the wrong player and reads as value that isn't there. Provisional —
# needs calibration against real bet365/Pinnacle name pairs (see module note).
DEFAULT_NAME_MATCH_FLOOR = 0.8
# Below this a stored match is shown with a ⚠ — plausibly right, worth a look.
NAME_MATCH_CONFIDENT = 0.9

# Outcome labels that mean "nobody scores first" rather than a player. They
# count toward the complete market (so they belong in the de-vig) but are never
# reported as a value bet.
_NO_GOAL_LABELS = {"no goalscorer", "no goal", "no scorer", "none"}


@dataclass
class GoalscorerRow:
    event_id: str
    match_name: str          # "Home v Away"
    commence_time: str       # ISO8601 from the API
    player: str              # bet365's spelling (what you'd bet on)
    bet365_price: float
    pinnacle_price: float
    fair_price: float        # de-vigged Pinnacle price for this player
    edge: float              # bet365/fair - 1 (positive = value at bet365)
    pinnacle_margin: float   # Pinnacle's overround on the market
    matched_player: str      # Pinnacle's spelling this was paired to
    match_score: float       # name-match confidence (1.0 = exact after normalise)


def player_match_score(a: str, b: str) -> float:
    """Confidence that two bookmaker spellings are the same player, in [0, 1].

    Blends full-name similarity with SURNAME similarity (the surname carries the
    identity, so 'M. Salah' pairs with 'Mohamed Salah'), over accent-folded,
    punctuation-stripped names. difflib only — no external dependency, matching
    the fixture matcher's approach.
    """
    a, b = normalise_player(a), normalise_player(b)
    if not a or not b:
        return 0.0
    full = difflib.SequenceMatcher(None, a, b).ratio()
    surname = difflib.SequenceMatcher(None, a.split()[-1], b.split()[-1]).ratio()
    return round(max(full, 0.4 * full + 0.6 * surname), 4)


def best_player_match(
    name: str, candidates: list[str], floor: float
) -> tuple[str | None, float]:
    """Best candidate for `name` at or above `floor`, and its score.

    An exact match after normalisation short-circuits to score 1.0. Returns
    (None, best_score) when nothing clears the floor.
    """
    if not candidates:
        return None, 0.0
    scored = [(player_match_score(name, c), c) for c in candidates]
    best_score, best = max(scored, key=lambda s: s[0])
    if best_score < floor:
        return None, best_score
    return best, best_score


def _player_prices(event: dict, bookmaker_key: str) -> dict[str, float]:
    """{player_or_label: price} for one bookmaker's first-goalscorer market.

    The outcome's player identity is taken from `description` if present, else
    `name` — player-prop responses vary and this hasn't been pinned to live data
    yet (see module note). Returns {} if the book doesn't price the market.
    """
    for bm in event.get("bookmakers", []):
        if bm.get("key") != bookmaker_key:
            continue
        prices: dict[str, float] = {}
        for market in bm.get("markets", []):
            if market.get("key") != FIRST_GOALSCORER_MARKET:
                continue
            for outcome in market.get("outcomes", []):
                player = outcome.get("description") or outcome.get("name")
                price = outcome.get("price")
                if player and price:
                    prices[player] = price
        return prices
    return {}


def _is_no_goal(label: str) -> bool:
    return label.strip().lower() in _NO_GOAL_LABELS


def compute_goalscorer_value(
    event: dict, min_edge: float, name_match_floor: float = DEFAULT_NAME_MATCH_FLOOR
) -> list[GoalscorerRow]:
    """Value rows where bet365's first-goalscorer price beats de-vigged Pinnacle.

    bet365 and Pinnacle spell player names differently, so each bet365 player is
    fuzzy-matched to a Pinnacle outcome (best_player_match); a bet365 player with
    no Pinnacle name clearing `name_match_floor` is dropped rather than guessed.

    Returns [] unless BOTH books price the market — a de-vig needs Pinnacle's
    complete outcome set, and there is nothing to compare without bet365.
    """
    pin = _player_prices(event, "pinnacle")
    b365 = _player_prices(event, "bet365")
    if not pin or not b365:
        return []

    # De-vig over Pinnacle's whole book (players + no-goalscorer), in a stable
    # order so fair prices line up with their outcomes.
    labels = list(pin.keys())
    fair_list, margin = devigd_multi([pin[o] for o in labels])
    fair = dict(zip(labels, fair_list))

    # Candidates to match bet365 names against: Pinnacle's PLAYERS only, never
    # the no-goalscorer outcome.
    pin_players = [label for label in labels if not _is_no_goal(label)]

    home = event.get("home_team", "")
    away = event.get("away_team", "")
    match_name = f"{home} v {away}".strip(" v")

    rows: list[GoalscorerRow] = []
    for player, b_price in b365.items():
        if _is_no_goal(player):
            continue
        matched, score = best_player_match(player, pin_players, name_match_floor)
        if matched is None:
            continue
        edge = b_price / fair[matched] - 1
        if edge < min_edge:
            continue
        rows.append(GoalscorerRow(
            event_id=event.get("id", ""),
            match_name=match_name,
            commence_time=event.get("commence_time", ""),
            player=player,
            bet365_price=b_price,
            pinnacle_price=pin[matched],
            fair_price=round(fair[matched], 3),
            edge=round(edge, 4),
            pinnacle_margin=round(margin, 4),
            matched_player=matched,
            match_score=score,
        ))

    rows.sort(key=lambda r: r.edge, reverse=True)
    return rows
