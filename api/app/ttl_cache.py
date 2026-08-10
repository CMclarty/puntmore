"""A cache that actually forgets things.

Every cache in this app used to be a plain dict with a timestamp beside each
value. The timestamp decided whether a value could be REUSED — it never removed
the entry, so nothing was ever freed. A dict keyed by fixture id, fed by a loop
that runs every ten minutes forever, is not a cache; it is a list of every
fixture the app has ever seen, held in memory until the process dies. On a
512 MB box that ends one way, and it did: Render killed the instance.

Two bounds, because either alone leaks:

  TTL      an entry past its time is dropped, not merely ignored. Expired
           entries are swept on write, so a key that is never asked for again
           still goes away — which is the normal case here, since fixtures stop
           being queried the moment they kick off.

  MAXSIZE  a hard ceiling on entries, oldest evicted first. The TTL alone
           cannot bound a burst: a big enough sheet can insert faster than
           anything expires, and the ceiling is what makes the worst case a
           number you can multiply by the entry size and reason about.

Deliberately not `functools.lru_cache`: these hold values fetched over the
network with a freshness requirement, and lru_cache has no notion of time.
"""
from collections import OrderedDict
import time

# Returned by `get` on a miss, so a cached None (a fixture we know has no grid)
# is distinguishable from nothing cached. That distinction is load-bearing —
# without it every unfittable fixture is re-fetched and re-fitted every pass.
MISS = object()


class TTLCache:
    """Bounded, expiring key/value store. Not thread-safe by design.

    Callers here are the scheduler thread and request handlers, and the worst a
    race can do is a duplicate fetch — the same thing that happens on a miss.
    A lock would serialise every read through one mutex to prevent an outcome
    that costs one HTTP call.
    """

    def __init__(self, ttl: float, maxsize: int):
        self._ttl = ttl
        self._maxsize = maxsize
        self._data: OrderedDict = OrderedDict()

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key, ttl: float | None = None):
        """The live value for `key`, or MISS. `ttl` overrides this cache's own.

        An expired entry is deleted here as well as on write — a cache that is
        read but not written (every fetch failing, say) would otherwise hold its
        last contents for ever.
        """
        entry = self._data.get(key)
        if entry is None:
            return MISS
        stored_at, value = entry
        if time.monotonic() - stored_at >= (self._ttl if ttl is None else ttl):
            del self._data[key]
            return MISS
        self._data.move_to_end(key)
        return value

    def set(self, key, value) -> None:
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        self._sweep()

    def _sweep(self) -> None:
        now = time.monotonic()
        # Expired first: it frees the entries nobody wants before falling back
        # to evicting ones that are still live.
        for k in [k for k, (at, _) in self._data.items() if now - at >= self._ttl]:
            del self._data[k]
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)   # oldest touched

    def clear(self) -> None:
        self._data.clear()
