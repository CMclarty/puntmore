"""Collect and summarise the counts model's measured errors (pricing/counts/shadow.py).

The beyond-ladder penalty (`extrapolation_edge_penalty`, 2% per count) was a
judgement, never measured. This measures it: each Pinnacle ladder the Paddy
sweep fits is queued ONCE per fixture and market per `REMEASURE_S`, the sweep
works the queue a few ladders per tick (outside the request path), and every
hidden-rung prediction is stored. `summary` reports the error by distance
beyond the ladder, split by the FAVOURITE side and the LONG side — the flat
penalty treats them alike, and in odds terms they are not: a 1.20 leg's price
barely moves for a probability error that moves its 6.0 opposite a lot.

Costs no vendor calls: it reuses ladders the sweep already fetched.
"""
import logging
import threading
from collections import deque
from datetime import timedelta
from statistics import mean, median

from ..models import LadderShadow
from ..pricing.counts.shadow import extrapolation_errors
from ..time_utils import utcnow
from ..ttl_cache import MISS, TTLCache

logger = logging.getLogger(__name__)

REMEASURE_S = 6 * 3600          # one measurement per fixture+market per 6 h
KEEP_DAYS = 30
PER_TICK = 12                   # ladders measured per sweep tick

_PENDING: deque = deque(maxlen=400)
_QUEUED = TTLCache(ttl=REMEASURE_S, maxsize=5000)
_LOCK = threading.Lock()


def _market(key: str) -> str:
    for word in ("corners", "cards", "goals"):
        if word in key:
            return word
    return key


def queue(ladder, market_key: str, config) -> None:
    """Called when the sweep fits a ladder; measured later by `flush`."""
    key = (ladder.fixture_id, market_key)
    with _LOCK:
        if _QUEUED.get(key) is not MISS:
            return
        _QUEUED.set(key, True)
        _PENDING.append((ladder, _market(market_key), config))


def flush(db, limit: int = PER_TICK) -> int:
    """Measure up to `limit` queued ladders, store their errors, prune old rows."""
    stored = 0
    for _ in range(limit):
        with _LOCK:
            if not _PENDING:
                break
            ladder, market, config = _PENDING.popleft()
        for e in extrapolation_errors(ladder, config):
            db.add(LadderShadow(
                market=market, fixture_id=str(ladder.fixture_id), line=e.line,
                beyond=e.beyond, p_pred=e.predicted, p_actual=e.actual,
            ))
            stored += 1
    db.query(LadderShadow).filter(
        LadderShadow.at < utcnow() - timedelta(days=KEEP_DAYS)
    ).delete(synchronize_session=False)
    db.commit()
    return stored


def _bucket(beyond: float) -> str:
    if beyond <= 0:
        return "inside"
    if beyond <= 1:
        return "1 out"
    if beyond <= 2:
        return "2 out"
    return "3+ out"


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def summary(db, days: int = 14) -> dict:
    """Error by market and distance beyond the ladder, in ODDS terms per side.

    For each hidden rung the favourite side is whichever Pinnacle makes more
    likely. `fav_err` / `long_err` are |model fair odds / Pinnacle fair odds - 1|
    on that side — directly comparable with the 2%-per-count penalty.
    `fav_bias` is signed: positive means the model prices the favourite SHORTER
    than Pinnacle (it would understate value on it).
    """
    since = utcnow() - timedelta(days=days)
    rows = db.query(LadderShadow).filter(LadderShadow.at >= since).all()
    groups: dict = {}
    for r in rows:
        a, p = r.p_actual, r.p_pred
        if not (0 < a < 1 and 0 < p < 1):
            continue
        fav_a, fav_p = (a, p) if a >= 0.5 else (1 - a, 1 - p)
        groups.setdefault((r.market, _bucket(r.beyond)), []).append((
            abs(fav_a / fav_p - 1),                 # odds ratio = p ratio, inverted
            abs((1 - fav_a) / (1 - fav_p) - 1),
            fav_p / fav_a - 1,
            abs(p - a),
        ))
    order = {"inside": 0, "1 out": 1, "2 out": 2, "3+ out": 3}
    out = []
    for (market, bucket), v in sorted(groups.items(), key=lambda kv: (kv[0][0], order[kv[0][1]])):
        fav, lng, bias, dp = zip(*v)
        out.append({
            "market": market, "distance": bucket, "n": len(v),
            "fav_err_median": round(median(fav), 4), "fav_err_p90": round(_pct(fav, 0.9), 4),
            "long_err_median": round(median(lng), 4), "long_err_p90": round(_pct(lng, 0.9), 4),
            "fav_bias_mean": round(mean(bias), 4),
            "prob_err_mean": round(mean(dp), 4),
        })
    return {
        "days": days, "rungs_measured": len(rows),
        "fixtures": len({r.fixture_id for r in rows}),
        "penalty_now": "2% of value per count beyond Pinnacle's ladder, both sides",
        "by_distance": out,
    }
