"""AW 26 auto-results: setting a leg's result from OddsPapi before the daily
settle, so a slip settles itself overnight instead of being marked by hand.

The SHAPE of OddsPapi's /scores and /settlements is not guessed. Capture it
once on a FINISHED fixture with

    GET /api/aw26/results-check?fixture=<leg.fixture_id>

(the leg's fixture_id is on /api/aw26/slips), then write `result_for_leg` from
that real output and flip `AUTO_RESULTS_READY`. Until then this is WIRED BUT
DORMANT: `apply_results` runs in the daily job, makes NO billable calls, and
leaves every leg pending — the same way `parse_page` waits for a real dump.

A leg whose result cannot be determined ALWAYS stays pending; its slip stays
open. A result is never assumed.
"""
import logging

from ..models import AwSlip

logger = logging.getLogger("uvicorn.error")

# Flip to True once result_for_leg is written against real /scores + /settlements
# output. While False, apply_results fetches nothing (OddsPapi bills per request,
# and a stub reader would burn the quota resolving nothing).
AUTO_RESULTS_READY = False

RESULTS = ("won", "lost", "void")


def result_for_leg(leg, scores, settlements) -> str | None:
    """One leg's result — 'won' | 'lost' | 'void' — from OddsPapi's /scores and
    /settlements for its fixture, or None when it cannot be told.

    NOT WRITTEN YET — it needs the real shape of those two responses, and
    whether they even carry corners, cards, goals and (especially) goalkeeper
    SAVES, which a scores feed often does not. Capture it with
    /api/aw26/results-check?fixture=<leg.fixture_id> on a finished match; if the
    stats are not there, the honest answer is a web source, not a guess here.
    """
    raise NotImplementedError(
        "result_for_leg needs real /scores + /settlements output — run "
        "/api/aw26/results-check?fixture=<id> on a finished fixture first"
    )


def _pending_legs_with_fixture(db) -> list:
    legs = []
    for slip in db.query(AwSlip).filter(AwSlip.status == "open").all():
        for leg in slip.legs:
            if leg.result == "pending" and leg.fixture_id:
                legs.append(leg)
    return legs


def apply_results(db, cards_client, *, now=None) -> dict:
    """Set pending leg results from OddsPapi, ONE pair of calls per fixture.

    Dormant until AUTO_RESULTS_READY: it reports what it would check but sends
    nothing. When live, a fetch or parse that fails for a fixture is logged and
    skipped — its legs stay pending, never assumed."""
    legs = _pending_legs_with_fixture(db)
    summary = {"checked": len(legs), "fixtures": 0, "resolved": 0, "live": AUTO_RESULTS_READY}

    if not AUTO_RESULTS_READY:
        if legs:
            logger.info(
                "aw26 results: %d pending leg(s) across %d fixture(s) would be "
                "checked, but the OddsPapi results reader is not written yet "
                "(result_for_leg); results stay manual. See results-check.",
                len(legs), len({leg.fixture_id for leg in legs}),
            )
        return summary

    client = getattr(cards_client, "_client", None)
    if client is None or not hasattr(client, "get"):
        logger.warning("aw26 results: no OddsPapi client configured — leg "
                       "results stay manual (set ODDSPAPI_API_KEY).")
        return summary

    by_fixture: dict[str, list] = {}
    for leg in legs:
        by_fixture.setdefault(str(leg.fixture_id), []).append(leg)
    summary["fixtures"] = len(by_fixture)

    for fixture_id, fixture_legs in by_fixture.items():
        try:
            scores = client.get("/scores", fixtureId=fixture_id)
            settlements = client.get("/settlements", fixtureId=fixture_id)
        except Exception as exc:
            logger.warning("aw26 results: fetch failed for fixture %s: %s", fixture_id, exc)
            continue
        for leg in fixture_legs:
            try:
                result = result_for_leg(leg, scores, settlements)
            except Exception as exc:
                logger.warning("aw26 results: could not read leg %s of fixture %s: %s",
                               leg.id, fixture_id, exc)
                continue
            if result in RESULTS:
                leg.result = result
                summary["resolved"] += 1
    db.flush()
    logger.info("aw26 results: %d/%d pending leg(s) resolved across %d fixture(s)",
                summary["resolved"], summary["checked"], summary["fixtures"])
    return summary
