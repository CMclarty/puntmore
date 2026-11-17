"""Props, the pure model: every row of the spec's statement table, both shapes
on the fair side, the join's refusals and the value. Hermetic."""
import pytest

from app.odds.utils import devigd
from app.props import model
from app.props.model import AT_LEAST, FEWER

PADDY, PIN = "paddypower", "pinnacle"


def market(mid, mtype, outcomes, *, line=0, period="result", prop=True, sport=10):
    return {"marketId": mid, "marketType": mtype, "period": period, "handicap": line,
            "playerProp": prop, "sportId": sport,
            "outcomes": [{"outcomeId": oid, "outcomeName": name} for oid, name in outcomes]}


CATALOGUE = [
    # players-<stat>, outcomes 0, 1, 1+, 2+ (exact counts are not read).
    market(100, "players-shotsongoal", [(1000, "0"), (1001, "1"), (1002, "1+"), (1003, "2+")]),
    market(110, "players-goals", [(1100, "0"), (1102, "1+"), (1103, "2+")]),
    market(120, "players-cards", [(1200, "Yes"), (1201, "No"), (1202, "0"), (1203, "1+")]),
    market(130, "players-anytimegoalscorer", [(1300, "Yes"), (1301, "No")], period="fulltime"),
    market(140, "players-firstgoalscorer", [(1400, "Yes")], period="fulltime"),
    market(150, "players-lastgoalscorer", [(1500, "Yes")], period="fulltime"),
    # playertotals-<stat> at x.5, and an x.0 line that is never read.
    market(200, "playertotals-shotsongoal", [(2000, "Over"), (2001, "Under")], line=1.5),
    market(201, "playertotals-shotsongoal", [(2010, "Over"), (2011, "Under")], line=0.5),
    market(202, "playertotals-shots", [(2020, "Over"), (2021, "Under")], line=2.0),
    market(203, "playertotals-tackles", [(2030, "Over"), (2031, "Under")], line=1.5),
    # Other periods, sports and non-props are not read.
    market(300, "players-goals", [(3000, "1+")], period="1h"),
    market(301, "players-goals", [(3010, "1+")], sport=11),
    market(302, "players-goals", [(3020, "1+")], prop=False),
    market(303, "players-passes", [(3030, "1+")]),
]


def test_every_row_of_the_statement_table():
    idx = model.statements(CATALOGUE)
    # players-<stat>: N+ is at_least(stat, N); 0 is fewer(stat, 1); exact counts unread.
    assert idx["100"] == {"1000": (FEWER, "shotsongoal", 1), "1002": (AT_LEAST, "shotsongoal", 1),
                          "1003": (AT_LEAST, "shotsongoal", 2)}
    assert idx["110"] == {"1100": (FEWER, "goals", 1), "1102": (AT_LEAST, "goals", 1),
                          "1103": (AT_LEAST, "goals", 2)}
    # players-cards: Yes / No as well as 0 / N+.
    assert idx["120"] == {"1200": (AT_LEAST, "cards", 1), "1201": (FEWER, "cards", 1),
                          "1202": (FEWER, "cards", 1), "1203": (AT_LEAST, "cards", 1)}
    # Anytime goalscorer is goals 1+.
    assert idx["130"] == {"1300": (AT_LEAST, "goals", 1), "1301": (FEWER, "goals", 1)}
    # playertotals at L (x.5): Over is at_least(L + 0.5), Under fewer(L + 0.5).
    assert idx["200"] == {"2000": (AT_LEAST, "shotsongoal", 2), "2001": (FEWER, "shotsongoal", 2)}
    assert idx["201"] == {"2010": (AT_LEAST, "shotsongoal", 1), "2011": (FEWER, "shotsongoal", 1)}
    assert idx["203"] == {"2030": (AT_LEAST, "tackles", 2), "2031": (FEWER, "tackles", 2)}
    # Not read: first/last scorer, an x.0 line, other periods, sports, non-props, other stats.
    for mid in ("140", "150", "202", "300", "301", "302", "303"):
        assert mid not in idx, mid


@pytest.mark.parametrize("stat", model.STATS)
def test_every_stat_reads_in_both_shapes(stat):
    idx = model.statements([
        market(1, f"players-{stat}", [(1, "0"), (2, "3+")]),
        market(2, f"playertotals-{stat}", [(3, "Over"), (4, "Under")], line=2.5),
    ])
    assert idx["1"] == {"1": (FEWER, stat, 1), "2": (AT_LEAST, stat, 3)}
    assert idx["2"] == {"3": (AT_LEAST, stat, 3), "4": (FEWER, stat, 3)}


def entry(price, active=True, **extra):
    return {"price": price, "active": active, **extra}


def board(books: dict) -> dict:
    """{book: {marketId: {outcomeId: {playerId: entry}}}} -> one raw fixture."""
    return {"fixtureId": "f1", "bookmakerOdds": {
        book: {"markets": {mid: {"outcomes": {oid: {"players": players}
                                              for oid, players in outs.items()}}
                           for mid, outs in markets.items()}}
        for book, markets in books.items()}}


IDX = model.statements(CATALOGUE)


def run(books, names=None):
    boards = {"f1": board(books)}
    props = model.player_market_ids(CATALOGUE)
    paddy = model.quotes(boards, IDX, PADDY, player_markets=props)
    fair = model.quotes(boards, IDX, "pinnacle+30", player_markets=props)
    bets, counts = model.join(paddy, fair, names if names is not None else {"7": "Erling Haaland"})
    return bets, counts, paddy, fair


def test_fair_in_the_playertotals_shape_prices_a_players_shape_paddy_line():
    bets, counts, _, _ = run({
        PADDY: {"100": {"1003": {"7": entry(3.6)}}},                       # 2+ SoT
        PIN: {"200": {"2000": {"7": entry(3.0)}, "2001": {"7": entry(1.38)}}},  # O/U 1.5
    })
    fair, _, margin = devigd(3.0, 1.38)
    [bet] = bets
    assert (bet.stat, bet.n, bet.odds, bet.player) == ("shotsongoal", 2, 3.6, "Erling Haaland")
    assert bet.fair == pytest.approx(fair) and bet.value == pytest.approx(3.6 / fair - 1)
    assert bet.pinnacle_margin == pytest.approx(margin)
    assert bet.pick == "Erling Haaland 2+ shots on target"
    assert counts["shotsongoal"]["matched"] == 1 and counts["shotsongoal"]["pinnacle_two_sided"] == 1


def test_fair_in_the_players_shape_0_against_1_plus():
    bets, counts, _, _ = run({
        PADDY: {"130": {"1300": {"7": entry(2.2)}}},                        # anytime scorer
        PIN: {"110": {"1102": {"7": entry(2.0)}, "1100": {"7": entry(1.8)}}},  # 1+ / 0
    })
    [bet] = bets
    assert (bet.stat, bet.n, bet.pick) == ("goals", 1, "Erling Haaland to score")
    assert bet.fair == pytest.approx(devigd(2.0, 1.8)[0])


def test_one_sided_fair_prices_nothing():
    bets, counts, _, _ = run({
        PADDY: {"100": {"1003": {"7": entry(3.6)}}},
        PIN: {"200": {"2000": {"7": entry(3.0)}}},
    })
    assert bets == [] and counts["shotsongoal"]["fair_one_sided"] == 1
    assert counts["shotsongoal"]["pinnacle_two_sided"] == 0


def test_a_suspended_or_unpriceable_quote_does_not_count():
    bets, counts, _, fair = run({
        PADDY: {"100": {"1003": {"7": entry(3.6)}}},
        PIN: {"200": {"2000": {"7": entry(3.0)}, "2001": {"7": entry(1.38, active=False)}}},
    })
    assert bets == [] and counts["shotsongoal"]["fair_one_sided"] == 1
    _, counts, paddy, _ = run({PADDY: {"100": {"1003": {"7": entry(1.0)}}}})
    assert paddy.quotes == {} and counts == {}


def test_an_x0_line_is_never_read():
    bets, counts, _, fair = run({
        PADDY: {"100": {"1003": {"7": entry(3.6)}}},
        PIN: {"202": {"2020": {"7": entry(1.9)}, "2021": {"7": entry(1.9)}}},
    })
    assert bets == [] and fair.quotes == {}
    # Pinnacle posted a player market, just not one that is read.
    assert fair.player_markets == 1
    assert counts["shotsongoal"]["no_fair_player"] == 1


def test_the_same_statement_twice_keeps_the_first_and_counts_the_clash():
    bets, counts, paddy, _ = run({
        # Anytime Yes and players-goals 1+ are both goals 1+.
        PADDY: {"130": {"1300": {"7": entry(2.2)}}, "110": {"1102": {"7": entry(2.1)}}},
        PIN: {"110": {"1102": {"7": entry(2.0)}, "1100": {"7": entry(1.8)}},
              "130": {"1300": {"7": entry(2.05)}}},
    })
    [bet] = bets
    assert bet.odds == 2.2
    assert counts["goals"]["duplicates_paddy"] == 1 and counts["goals"]["duplicates_pinnacle"] == 1
    assert counts["goals"]["paddy"] == 1


def test_an_unnamed_player_is_left_out_and_counted_and_a_name_on_the_entry_is_used():
    books = {
        PADDY: {"120": {"1200": {"7": entry(3.4), "8": entry(4.0, playerName="Saka, Bukayo")}}},
        PIN: {"120": {"1200": {"7": entry(3.0), "8": entry(3.5)},
                      "1201": {"7": entry(1.35), "8": entry(1.28)}}},
    }
    bets, counts, paddy, _ = run(books, names={})
    assert counts["cards"]["unnamed"] == 1 and counts["cards"]["matched"] == 1
    assert [b.player_id for b in bets] == ["8"]
    # "Surname, First" is printed "First Surname".
    assert paddy.names == {"8": "Bukayo Saka"} and bets[0].pick == "Bukayo Saka to be carded"


def test_no_fair_player_and_no_fair_line_are_told_apart():
    bets, counts, _, _ = run({
        PADDY: {"100": {"1002": {"7": entry(1.5), "9": entry(1.6)},
                        "1003": {"7": entry(3.6)}}},
        PIN: {"201": {"2010": {"7": entry(1.45)}, "2011": {"7": entry(2.6)}}},  # 7 at 1+ only
    })
    c = counts["shotsongoal"]
    assert (c["paddy"], c["matched"], c["no_fair_line"], c["no_fair_player"]) == (3, 1, 1, 1)
    assert sum(c[r] for r in model.REASONS) + c["matched"] == c["paddy"]


def test_player_0_is_never_a_player_and_paddy_fewer_quotes_are_not_selections():
    bets, counts, paddy, _ = run({
        PADDY: {"100": {"1003": {"0": entry(3.6)}, "1000": {"7": entry(1.2)}}},
    })
    assert ("f1", "0", "shotsongoal", 2) not in paddy.quotes
    assert counts.get("shotsongoal", {}).get("paddy", 0) == 0 and bets == []


def test_the_fair_book_is_found_under_a_variant_slug_either_way():
    fx = {"bookmakerOdds": {"pinnacle+30": {"markets": {"1": {}}}}}
    assert model.book_odds(fx, "pinnacle+30") == {"markets": {"1": {}}}
    assert model.book_odds(fx, "pinnacle") == {"markets": {"1": {}}}
    fx = {"bookmakerOdds": {"pinnacle": {"markets": {"2": {}}}}}
    assert model.book_odds(fx, "pinnacle+30") == {"markets": {"2": {}}}
    assert model.book_odds(fx, "paddypower") == {}


@pytest.mark.parametrize("stat,n,want", [
    ("goals", 1, "Declan Rice to score"),
    ("goals", 2, "Declan Rice 2+ goals"),
    ("cards", 1, "Declan Rice to be carded"),
    ("cards", 2, "Declan Rice 2+ cards"),
    ("assists", 1, "Declan Rice 1+ assists"),
    ("foulscommitted", 3, "Declan Rice 3+ fouls"),
    ("offsides", 1, "Declan Rice 1+ offsides"),
    ("saves", 4, "Declan Rice 4+ saves"),
    ("shots", 2, "Declan Rice 2+ shots"),
    ("shotsongoal", 2, "Declan Rice 2+ shots on target"),
    ("tackles", 2, "Declan Rice 2+ tackles"),
])
def test_pick_wording(stat, n, want):
    assert model.pick("Declan Rice", stat, n) == want
