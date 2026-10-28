"""The watchdog ends a service whose tick is stuck, and leaves a live one be."""
import time

from scraper.__main__ import watchdog


def test_a_stuck_tick_exits_and_a_live_one_does_not():
    exits = []
    last_tick = [time.monotonic()]
    watchdog(last_tick, limit_s=0.2, every_s=0.05, exit=exits.append)
    for _ in range(6):                     # ticking: never older than the limit
        time.sleep(0.05)
        last_tick[0] = time.monotonic()
    assert exits == []
    time.sleep(0.4)                        # stuck
    assert exits and exits[0] == 1
