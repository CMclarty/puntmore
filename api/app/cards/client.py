"""Paddy Power corners/cards prices, from TheOddsApi's PER-EVENT endpoint.

This replaces a client that never returned a row in its life. The old one asked
the BULK /odds endpoint for `alternate_totals` — a market that endpoint rejects
outright (it serves featured markets only), and which is *goal* totals anyway,
not corners or cards. It then swallowed the rejection with `except Exception:
continue`, so the tool showed an empty feed for months and looked like a market
with no value in it. Both halves of that are fixed here: the right endpoint with
the right keys, and failures that are counted and logged.

THREE THINGS DRIVE THE COST, and only one of them is the refresh interval.

  Billing is (markets returned x regions), charged PER EVENT. So:

  REGIONS   Only Paddy Power is requested — `bookmakers=paddypower`, and no
            `regions` parameter at all. Paddy Power is a `uk` book; Pinnacle is
            `eu`, so asking for both here would span two regions. Pinnacle's
            side comes from OddsPapi instead (cards/oddspapi.py).
            NOTE regions describe where the BOOKMAKER is licensed, not where the
            league is — Paddy Power prices La Liga from the `uk` region fine.
            The league is chosen by sport key, and the fixture list is FREE.

  WINDOW    The expensive dial. Cost per fixture is sweeps x markets, so a wide
            window costs far more than a fast interval — and corners/cards are
            posted near kickoff, so early sweeps buy empty responses. Fixtures
            outside `cards_window_hours` are not called at all.

  CADENCE   Tiered, via the odds cache's per-read TTL rather than a second
            scheduler: a fixture far from kickoff is served from cache for
            `cards_slow_interval_minutes`, one inside the fast window for
            `cards_fast_interval_minutes`. The sweep can then run every 5
            minutes and only the fixtures that matter actually cost anything.

A HARD DAILY CEILING sits on top, because the exact credit cost of a per-event
call is not confirmed (docs/provider-verification.md says markets x regions;
routers/accabonus.py says naming bookmakers bills as one — a 2x difference).
The sweep counts its own calls, reads the real remaining quota back off the
response headers, and STOPS at the ceiling. Finding out the true rate on the
first matchday is fine; finding out when the month's quota is gone is not.
"""
import logging
from datetime import datetime, timedelta, timezone

from ..config import settings

logger = logging.getLogger("uvicorn.error")

PADDY_POWER = "paddypower"

# The markets this tool values, as `value.py` expects them. These are the REAL
# corners/cards keys — the old value was `alternate_totals`, which is goal
# totals, and which the bulk endpoint rejects anyway.
CARDS_MARKET_KEYS = [
    k.strip() for k in settings.cards_market_keys.split(",") if k.strip()
]


def _parse_commence(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class CardsApiClient:
    """Paddy Power corners/cards, swept per fixture within the kickoff window."""

    def __init__(self, client, sport_keys=None, market_keys=None):
        self._client = client
        self._sport_keys = sport_keys or [
            k.strip() for k in settings.cards_sport_keys.split(",") if k.strip()
        ]
        self._market_keys = market_keys or settings.cards_market_keys
        # Per-UTC-day call counter, so the ceiling survives a long-running
        # process without needing a scheduler of its own.
        self._spent_on = None
        self._spent = 0
        # What the last sweep saw, for /cards/diagnostics. An empty screen has
        # several possible causes and they are indistinguishable from the
        # outside — this is what tells them apart without reading Render logs.
        self.last_sweep: dict = {}

    # --- budget ---

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self._spent_on != today:
            self._spent_on, self._spent = today, 0

    @property
    def calls_today(self) -> int:
        self._roll_day()
        return self._spent

    def _budget_left(self) -> int:
        """Calls still permitted: the tighter of the day's cap and the reserve.

        Two guards, deliberately, because they fail differently. The daily cap
        is local arithmetic and stops one bad day spending a month. The reserve
        is the vendor's OWN remaining-quota header, so it is right even if the
        per-call cost is not what this code assumes — which it may not be, since
        that figure is still unmeasured.
        """
        daily = max(settings.cards_daily_call_budget - self.calls_today, 0)

        remaining = getattr(self._client, "quota_remaining", None)
        if remaining is None:
            return daily
        spare = remaining - settings.cards_quota_reserve
        if spare <= 0:
            logger.error(
                "cards: TheOddsApi quota down to %s, at or below the %s reserve "
                "— the sweep is stopping so the reserve is left for the coupon "
                "fallback and the goalscorer scans.",
                remaining, settings.cards_quota_reserve,
            )
            return 0
        # A per-call cost of at least 1; the header tells us the real one once a
        # call has been made, so use it rather than assuming.
        cost = max(getattr(self._client, "last_call_cost", None) or 1, 1)
        return min(daily, spare // cost)

    # --- the sweep ---

    def _due_fixtures(self) -> list[tuple[str, dict, int]]:
        """(sport_key, fixture, ttl_seconds) for every fixture worth calling.

        The fixture list endpoint is FREE, so this filtering costs nothing —
        which is exactly why the window is the right place to control spend.
        """
        now = datetime.now(timezone.utc)
        window = timedelta(hours=settings.cards_window_hours)
        far = timedelta(hours=settings.cards_far_window_hours)
        fast = timedelta(hours=settings.cards_fast_window_hours)
        due = []
        # What the FREE fixture lists held before the window cut them down.
        # "Nothing within 48h" has two very different causes — a quiet spell,
        # or a key list that does not cover the games being played — and only
        # the listed count and the next kickoff tell them apart.
        self._listed = 0
        self._next_kickoff = None
        for sport_key in self._sport_keys:
            try:
                fixtures = self._client.list_events(sport_key)
            except Exception as exc:
                # Counted, never swallowed — this is what hid the old failure.
                logger.error("cards: fixture list failed for %s: %s", sport_key, exc)
                continue
            for fixture in fixtures:
                starts = _parse_commence(fixture.get("commence_time"))
                if starts is None:
                    continue
                to_ko = starts - now
                self._listed += 1
                if to_ko > timedelta(0) and (
                    self._next_kickoff is None or starts < self._next_kickoff
                ):
                    self._next_kickoff = starts
                # Already kicked off, or too far out for the markets to be up.
                if to_ko <= timedelta(0) or to_ko > window:
                    continue
                # Three tiers by time to kickoff. The cadence is carried as a
                # cache TTL, so a 5-minute sweep re-fetches only the fixtures
                # whose tier has expired and serves the rest from cache.
                if to_ko <= fast:
                    minutes = settings.cards_fast_interval_minutes
                elif to_ko <= far:
                    minutes = settings.cards_slow_interval_minutes
                else:
                    minutes = settings.cards_far_interval_minutes
                due.append((sport_key, fixture, minutes * 60))
        # Soonest kickoff first, so a budget cut-off drops the fixtures that
        # matter least rather than whichever came back first.
        due.sort(key=lambda row: row[1].get("commence_time", ""))
        return due

    def _inactive_keys(self) -> list[str] | None:
        """Configured keys TheOddsApi is not currently listing.

        `list_events` answers an unknown key with a 404 that reads as "no
        fixtures", so a misspelled key would sit in the list returning nothing
        for ever, indistinguishable from a quiet week. Checked against the
        FREE /v4/sports list. It names in-season sports only, so a key here is
        either out of season or misspelled — both worth seeing. None when the
        check itself could not run.
        """
        try:
            active = {s.get("key") for s in self._client.list_sports()}
        except Exception as exc:
            logger.warning("cards: could not check sport keys: %s", exc)
            return None
        return [k for k in self._sport_keys if k not in active]

    def get_events(self) -> list[dict]:
        """Paddy Power's corners/cards markets for every fixture now due.

        Returns TheOddsApi event dicts, so `value.py` consumes them unchanged.
        """
        if self._client is None:
            return []

        due = self._due_fixtures()
        budget = self._budget_left()
        if len(due) > budget:
            logger.warning(
                "cards: %d fixture(s) due but only %d call(s) left in today's "
                "budget of %d — sweeping the soonest %d and skipping the rest.",
                len(due), budget, settings.cards_daily_call_budget, budget,
            )
            due = due[:budget]

        events, priced, failed = [], 0, 0
        for sport_key, fixture, ttl in due:
            try:
                event = self._client.fetch_event(
                    sport_key, fixture["id"], self._market_keys,
                    bookmakers=PADDY_POWER, ttl=ttl,
                )
            except Exception as exc:
                failed += 1
                logger.error(
                    "cards: per-event fetch failed for %s/%s: %s",
                    sport_key, fixture.get("id"), exc,
                )
                continue
            self._roll_day()
            self._spent += 1
            if not event:
                continue
            # An event with no Paddy Power book is a fixture whose markets are
            # not posted yet — normal this far from kickoff, not a failure.
            if any(bm.get("key") == PADDY_POWER for bm in event.get("bookmakers", [])):
                priced += 1
                events.append(event)

        next_ko = getattr(self, "_next_kickoff", None)
        self.last_sweep = {
            "sport_keys": len(self._sport_keys),
            "inactive_keys": self._inactive_keys(),
            "fixtures_listed": getattr(self, "_listed", 0),
            "next_kickoff": next_ko.isoformat() if next_ko else None,
            "window_hours": settings.cards_window_hours,
            "fixtures_in_window": len(due),
            "near_kickoff": sum(1 for _, _, ttl in due
                                if ttl <= settings.cards_slow_interval_minutes * 60),
            "called": len(due) - failed,
            "carried_paddy_prices": priced,
            "failed": failed,
            "calls_today": self.calls_today,
            "call_budget": settings.cards_daily_call_budget,
            # The vendor's own figures, which are the ones that actually bind.
            "quota_remaining": getattr(self._client, "quota_remaining", None),
            "quota_reserve": settings.cards_quota_reserve,
            "last_call_cost": getattr(self._client, "last_call_cost", None),
        }
        log = logger.info if not failed else logger.warning
        log(
            "cards sweep: %d fixture(s) due, %d called, %d carried Paddy Power "
            "prices, %d failed (%d/%d calls used today)",
            len(due), len(due) - failed, priced, failed,
            self.calls_today, settings.cards_daily_call_budget,
        )
        return events
