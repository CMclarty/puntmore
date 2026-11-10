"""What a slip is worth, and what it paid — by the offer it was struck under.

Held here because a slip's EV is fixed on the server when it is recorded and
its result settled when its legs' results are entered, whoever sends them.
The boost is the SAME maths as the Paddy page's (render/frontend/src/
accaBoost.js) and the tests pin both to the same worked cases.

ONE CASH ECOSYSTEM (Callum, 10 Nov 2026): AW 26 counts real money only. A
refund's bonus is a free bet, not cash, so a refund slip is valued and settled
as the cash bet it is; the free bet is recorded as its own slip when it is
placed (offer `free`) and carries its own value and its own winnings. The
Paddy page's refund tab still values the bonus at 0.75 of the stake to choose
which slips to place: that is a forecast of the free bet, which AW 26 counts
when it happens.

Legs are independent; p_i = 1 / fair_i, the leg's true win chance.

  A      = ∏ o_i                          acca odds, void legs dropped
  P_win  = ∏ p_i

  refund  E = S·P_win·A                         the bonus is counted on its free bet
  boost   E = S·P_win·(1 + (A − 1)(1 + b))      b by fold size
  other   E = S·P_win·A
  free    E = S·P_win·(A − 1)                   the stake is never returned

  EV £ = E − S, except a free bet: EV £ = E (no cash was staked)

Terms, as Callum gave them for AW 26 (Oct 2026):
- REFUND: a 5-fold, every leg 1.20+, stake back as a free bet if EXACTLY one
  leg loses. The slip is then `refunded`, returning £0 cash. There is NO cap.
- BOOST: winnings +10% on a 4-fold, +20% on a 5-fold (+30% on a 6-fold, per the
  terms confirmed for the Paddy page — `BOOST_TIERS`), every leg 1.20+.
- OTHER: anything else, valued as a straight bet.
- FREE: a free bet (a refund's bonus): winnings only, the stake never comes
  back, and no cash was staked. No terms to meet.
A slip that does not meet its offer's terms is valued WITHOUT the offer and
says why — the refund or boost is never counted on a slip it would not pay on.
"""
from math import prod

REFUND_LEGS = 5
MIN_LEG_ODDS = 1.20
BOOST_TIERS = {4: 0.10, 5: 0.20, 6: 0.30}

OFFERS = ("refund", "boost", "other", "free")


def cash_staked(offer: str, stake: float | None) -> float:
    """The cash a slip put down: its stake, except a free bet's (none)."""
    return 0.0 if offer == "free" or stake is None else stake


def problems(offer: str, odds: list[float]) -> list[str]:
    """Why the slip does NOT get its offer (empty: it does)."""
    out = []
    if offer == "refund" and len(odds) != REFUND_LEGS:
        out.append(f"refund needs exactly {REFUND_LEGS} legs (has {len(odds)})")
    if offer == "boost" and len(odds) not in BOOST_TIERS:
        out.append(f"no boost for a {len(odds)}-fold")
    if offer in ("refund", "boost"):
        short = [i + 1 for i, o in enumerate(odds) if not o >= MIN_LEG_ODDS]
        if short:
            out.append(f"leg{'s' if len(short) > 1 else ''} {', '.join(map(str, short))} "
                       f"under {MIN_LEG_ODDS:.2f}")
    return out


def expected_value(offer: str, stake: float, odds: list[float], fair: list[float]) -> float:
    """EV in pounds. Every leg must carry a fair price — the caller checks, as a
    slip valued off the legs that DID price overstates it every time."""
    if len(odds) != len(fair) or not odds:
        raise ValueError("every leg needs its odds and its fair price")
    ps = [1.0 / f for f in fair]
    a = prod(odds)
    p_win = prod(ps)
    qualifies = not problems(offer, odds)
    if offer == "free":
        return stake * p_win * (a - 1)
    if offer == "boost" and qualifies:
        ret = stake * p_win * (1 + (a - 1) * (1 + BOOST_TIERS[len(odds)]))
    else:
        ret = stake * p_win * a
    return ret - stake


def settle(offer: str, stake: float, legs: list[tuple[float, str]]) -> tuple[str, float | None]:
    """(status, returns) from each leg's (odds, result).

    result is 'won' | 'lost' | 'void' | 'pending'. The slip stays 'open' while
    its outcome still depends on a pending leg; it is decided as soon as it no
    longer does (a second loser ends a refund slip; one ends any other).

    Qualification is judged on the slip AS STRUCK — a void leg does not take a
    5-fold out of the refund (the frontend's `voidQualifies` default). A void
    drops the leg from the odds, and a boost is paid at the tier of the legs
    that stood.
    """
    results = [r for _, r in legs]
    lost = results.count("lost")
    pending = results.count("pending")
    struck_odds = [o for o, _ in legs]
    qualifies = not problems(offer, struck_odds)

    if offer == "refund" and qualifies:
        if lost >= 2:
            return "lost", 0.0
        if pending:
            return "open", None
        if lost == 1:
            return "refunded", 0.0          # the stake comes back as a free bet
    else:
        if lost:
            return "lost", 0.0
        if pending:
            return "open", None

    standing = [o for o, r in legs if r == "won"]
    if not standing:
        # A void free bet returns no cash.
        return "void", 0.0 if offer == "free" else round(stake, 2)
    a = prod(standing)
    if offer == "free":
        return "won", round(stake * (a - 1), 2)
    if offer == "boost" and qualifies:
        b = BOOST_TIERS.get(len(standing), 0.0)
        return "won", round(stake * (1 + (a - 1) * (1 + b)), 2)
    return "won", round(stake * a, 2)
