"""The fixtures board is fetched a day at a time across a horizon.

Regression cover for a live outage: the board was fetched in ONE request with
`starts_from=today` and `limit=1000`. On a busy weekend one day of soccer alone
approaches that cap, so the single response ran out ~1.5 days ahead — a coupon
for the coming weekend matched nothing and showed no fair odds at all. The fix
fetches one whole day per request (starts_from == starts_to) and merges them, so
the board reaches every day in the window.
"""
from datetime import datetime, timedelta, timezone

from app.providers import bettingiscool
from app.providers.bettingiscool import BettingIsCoolProvider, FIXTURE_HORIZON_DAYS

# A fixed reference instant so the expected day strings are deterministic.
NOW = datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc).timestamp()


def _fixture(event_id, day):
    return {
        "event_id": event_id,
        "league_name": "Test League",
        "runner_home": f"Home {event_id}",
        "runner_away": f"Away {event_id}",
        "starts": f"{day}T19:00:00",
    }


def _days(now=NOW):
    d0 = datetime.fromtimestamp(now, tz=timezone.utc).date()
    return [(d0 + timedelta(days=i)).isoformat() for i in range(FIXTURE_HORIZON_DAYS)]


class _RecordingProvider(BettingIsCoolProvider):
    """Records every /api/fixtures request and serves a day's rows per slice."""

    def __init__(self, per_day, fail_days=()):
        super().__init__("test-key")
        self._per_day = per_day          # {day_str: [rows]}
        self._fail_days = set(fail_days)
        self.requested = []              # list of (starts_from, starts_to)

    def _get(self, path, params):
        assert path == "/api/fixtures"
        self.requested.append((params["starts_from"], params["starts_to"]))
        assert params["starts_from"] == params["starts_to"]  # one whole day
        day = params["starts_from"]
        if day in self._fail_days:
            raise RuntimeError("boom")
        return self._per_day.get(day, [])


def _freeze(monkeypatch, now):
    monkeypatch.setattr(bettingiscool.time, "time", lambda: now)


def test_board_fetches_one_request_per_day_over_the_horizon(monkeypatch):
    _freeze(monkeypatch, NOW)
    days = _days()
    per_day = {d: [_fixture(i, d)] for i, d in enumerate(days)}
    p = _RecordingProvider(per_day)

    board = p._fixtures()

    # One request per day, each a single-day window (starts_from == starts_to).
    assert p.requested == [(d, d) for d in days]
    # Every day's fixture made it onto the board — including days well beyond the
    # first, which the old single-request board never reached.
    assert {f["event_id"] for f in board} == set(range(FIXTURE_HORIZON_DAYS))


def test_board_dedupes_events_seen_in_more_than_one_slice(monkeypatch):
    _freeze(monkeypatch, NOW)
    days = _days()
    # Same event_id returned by two slices — it must appear once.
    per_day = {days[0]: [_fixture(7, days[0])], days[1]: [_fixture(7, days[1])]}
    p = _RecordingProvider(per_day)

    board = p._fixtures()
    assert [f["event_id"] for f in board] == [7]


def test_one_failed_slice_does_not_wipe_the_board(monkeypatch):
    _freeze(monkeypatch, NOW)
    days = _days()
    per_day = {d: [_fixture(i, d)] for i, d in enumerate(days)}
    p = _RecordingProvider(per_day, fail_days={days[2]})

    ids = {f["event_id"] for f in p._fixtures()}
    # The failed day is missing, but every other day still priced.
    assert 2 not in ids
    assert ids == set(range(FIXTURE_HORIZON_DAYS)) - {2}


def test_total_wipeout_keeps_the_last_good_board(monkeypatch):
    days = _days()
    _freeze(monkeypatch, NOW)
    good = _RecordingProvider({d: [_fixture(1, d)] for d in days})
    board = good._fixtures()
    assert board  # primed

    # Every slice now fails; a stale-but-good board beats blanking every leg.
    all_fail = _RecordingProvider({}, fail_days=set(days))
    all_fail._fixtures_cache = good._fixtures_cache
    _freeze(monkeypatch, NOW + 10_000)  # past the cache TTL, forcing a refetch
    assert all_fail._fixtures() == board
