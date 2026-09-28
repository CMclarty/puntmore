"""Joining the cards tool's two books into one event.

Paddy Power's corners/cards come from OddsPapi (`cards/oddspapi.py`), and so,
by default, does Pinnacle's fair side — on the same fixture id, so those events
arrive already joined. `merge_pinnacle` is what remains of the two-vendor
design: for any event WITHOUT a Pinnacle book it looks the fixture up on the
bettingiscool board (`cards/pinnacle.py`) by name.

TheOddsApi's per-event Paddy Power sweep that used to live here was removed
(Sept 2026): TheOddsApi does not carry Paddy's corners or cards at all — a
probe hours from a Nations League kickoff returned LeoVegas alone.
"""
import logging

logger = logging.getLogger("uvicorn.error")

PADDY_POWER = "paddypower"


def merge_pinnacle(events: list[dict], provider) -> list[dict]:
    """Attach bettingiscool's Pinnacle prices to Paddy Power's events.

    The two halves come from different vendors, but `value.py` wants them on
    ONE event the way TheOddsApi would have delivered them — so they are joined
    here rather than by teaching the value logic about two sources. That keeps
    the pricing maths untouched and vendor-agnostic, which is the point of the
    seam: swapping either side back is a change in this function alone.

    An event whose Pinnacle side is missing is left as it is. `value.py` already
    skips any event without both books, so a fixture with no fair price is
    dropped rather than valued against nothing.
    """
    # Events whose fair side already came from OddsPapi (same fixture id, no
    # name match) need nothing more; only the rest go to bettingiscool.
    events_needing = [e for e in events or []
                      if not any(b.get("key") == "pinnacle" for b in e.get("bookmakers", []))]
    if not events_needing:
        return events
    if provider is None:
        return events
    # The coupon odds provider is only bettingiscool when a key is configured;
    # it degrades to TheOddsApi or the mock otherwise, and neither carries a
    # board of derived corners fixtures. Say so once rather than raising — this
    # is the same silent-degradation trap the coupon tracker had, where a
    # missing key produced an empty feed that looked like a market with no
    # value in it.
    if not (hasattr(provider, "board") and hasattr(provider, "odds_for")):
        logger.warning(
            "cards: the coupon odds provider is %s, which carries no Pinnacle "
            "corners/cards board — every Paddy Power price will go unvalued. "
            "The fair side needs bettingiscool (set BETTINGISCOOL_API_KEY).",
            type(provider).__name__,
        )
        return events
    from .pinnacle import pinnacle_books

    books = pinnacle_books(provider, events_needing)
    for event in events_needing:
        book = books.get(event.get("id"))
        if book:
            event.setdefault("bookmakers", []).append(book)
    return events
