"""AW 26 offer maths. The boost is pinned to the SAME worked cases as the
frontend's accaBoost.test.js, so the two cannot drift apart unnoticed. The
refund is not: AW 26 counts cash only (Callum, 10 Nov 2026), so a refund
slip is valued as the cash bet it is, and the free bet it earns is valued
on its own slip when it is placed (offer `free`)."""
import pytest

from app.aw26.offers import expected_value, problems, settle


@pytest.mark.parametrize("o, r", [(1.30, 1.00), (1.25, 1.00), (1.50, 1.10), (1.40, 0.90)])
def test_a_refund_slip_is_valued_as_the_cash_bet_it_is(o, r):
    # The free bet a refund earns is not counted here: it is valued on its own
    # slip when it is placed.
    fair = [o / r ** (1 / 5)] * 5               # p_i = r^(1/n) / o
    assert expected_value("refund", 50, [o] * 5, fair) == pytest.approx(
        expected_value("other", 50, [o] * 5, fair))
    assert expected_value("refund", 50, [o] * 5, fair) == pytest.approx(50 * (r - 1))


@pytest.mark.parametrize("n, ev_pct", [(4, 5.9), (5, 13.4), (6, 22.1)])
def test_boost_matches_the_offers_worked_cases(n, ev_pct):
    ev = expected_value("boost", 100, [1.25] * n, [1.25] * n)
    assert round(ev, 1) == ev_pct


def test_a_slip_off_its_offers_terms_is_valued_without_the_offer():
    four = [1.3] * 4
    assert problems("refund", four) == ["refund needs exactly 5 legs (has 4)"]
    # Fair odds and no refund: worth exactly nothing either way.
    assert expected_value("refund", 50, four, four) == pytest.approx(0)
    short = [1.15, 1.3, 1.3, 1.3, 1.3]
    assert problems("boost", short) == ["leg 1 under 1.20"]
    assert expected_value("boost", 50, short, short) == pytest.approx(0)
    assert problems("boost", [1.3] * 3) == ["no boost for a 3-fold"]


def test_other_is_a_straight_bet():
    assert expected_value("other", 10, [2.2], [2.0]) == pytest.approx(1.0)


def test_refund_settles_on_exactly_one_loser():
    legs = [(1.3, "won")] * 4
    # Refunded: the stake comes back as a free bet, not cash, so it returns
    # nothing here; the free bet's own slip carries what it pays.
    assert settle("refund", 50, legs + [(1.3, "lost")]) == ("refunded", 0.0)
    assert settle("refund", 50, legs[:3] + [(1.3, "lost"), (1.3, "lost")]) == ("lost", 0.0)
    # One down and one to play: still alive either way.
    assert settle("refund", 50, legs[:3] + [(1.3, "lost"), (1.3, "pending")]) == ("open", None)
    assert settle("refund", 50, [(1.3, "won")] * 5) == ("won", round(50 * 1.3 ** 5, 2))


def test_boost_and_other_end_on_the_first_loser():
    assert settle("boost", 50, [(1.3, "lost")] + [(1.3, "pending")] * 3) == ("lost", 0.0)
    assert settle("other", 10, [(2.0, "pending")]) == ("open", None)


def test_boost_pays_boosted_winnings_at_the_tier_of_the_legs_that_stood():
    status, ret = settle("boost", 100, [(1.5, "won")] * 5)
    assert status == "won" and ret == round(100 * (1 + (1.5 ** 5 - 1) * 1.2), 2)
    # A void drops a 5-fold to a 4-fold: 10%, on the four that stood.
    status, ret = settle("boost", 100, [(1.5, "won")] * 4 + [(1.5, "void")])
    assert ret == round(100 * (1 + (1.5 ** 4 - 1) * 1.1), 2)


def test_a_void_keeps_a_refund_slip_insured_and_all_void_returns_the_stake():
    legs = [(1.3, "won")] * 3 + [(1.3, "void"), (1.3, "lost")]
    assert settle("refund", 50, legs) == ("refunded", 0.0)
    assert settle("other", 20, [(2.0, "void")]) == ("void", 20.0)


# --- a free bet: the stake is not returned -------------------------------------


def test_a_free_bet_is_valued_cautiously():
    # Callum, 10 Nov 2026: err on the cautious side. A free bet's EV is the
    # lower of its full EV and its value at its own odds as if they were fair,
    # stake x (odds - 1) / odds: edge above the true value is never booked,
    # and a free bet picked worse than fair is still marked down.
    assert expected_value("free", 50, [3.0], [3.0]) == pytest.approx(50 * 2 / 3)    # fair: both agree
    assert expected_value("free", 50, [3.0], [2.5]) == pytest.approx(50 * 2 / 3)    # +EV: capped
    assert expected_value("free", 50, [3.0], [3.5]) == pytest.approx(50 / 3.5 * 2)  # -EV: the full EV
    a = 2.2 * 2.1
    assert expected_value("free", 50, [2.2, 2.1], [2.0, 2.0]) == pytest.approx(50 * (a - 1) / a)
    assert problems("free", [1.05]) == []        # no terms to meet


def test_a_free_bet_pays_its_winnings_and_never_its_stake():
    assert settle("free", 50, [(3.0, "won")]) == ("won", 100.0)
    assert settle("free", 50, [(3.0, "lost")]) == ("lost", 0.0)
    assert settle("free", 50, [(3.0, "pending")]) == ("open", None)
    assert settle("free", 50, [(2.0, "won"), (1.5, "void")]) == ("won", 50.0)
    assert settle("free", 50, [(3.0, "void")]) == ("void", 0.0)
