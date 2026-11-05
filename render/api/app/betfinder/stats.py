"""Per-source record off settled Bet Finder tips.

A tip is settled when the owner client's ledger bets on its selection are
(`pipeline.link_and_settle`). Per tip: the outcome and the return per pound
staked across its bets. Per source, over all its settled tips and over the
last `betfinder_stats_window` of them:

- `strike_rate` — won / (won + lost + partial); voids are left out.
- `roi`         — LEVEL-STAKE: mean of (returned / staked - 1) over the
                  non-void tips, so a source is judged on its picks, not on
                  how much the engine happened to stake on each.

Nothing here changes a trust weight. It is shown beside the weight so Cal
can set it.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BetFinderTip


def _block(tips: list[BetFinderTip]) -> dict:
    counted = [t for t in tips if t.outcome != "void" and t.staked]
    won = sum(1 for t in counted if t.outcome == "won")
    lost = sum(1 for t in counted if t.outcome == "lost")
    partial = sum(1 for t in counted if t.outcome == "partial")
    n = len(counted)
    roi = (sum((t.returned or 0.0) / t.staked - 1.0 for t in counted) / n) if n else None
    return {
        "settled": len(tips), "won": won, "lost": lost, "partial": partial,
        "void": sum(1 for t in tips if t.outcome == "void"),
        "strike_rate": round(won / n, 4) if n else None,
        "roi": round(roi, 4) if roi is not None else None,
        "staked": round(sum(t.staked or 0.0 for t in tips), 2),
        "returned": round(sum(t.returned or 0.0 for t in tips), 2),
    }


def source_stats(db: Session) -> dict[str, dict]:
    """{source: {"all": block, "rolling": block, "tips": {status: n}}}."""
    window = max(1, settings.betfinder_stats_window)
    settled: dict[str, list[BetFinderTip]] = {}
    for t in (db.query(BetFinderTip).filter(BetFinderTip.status == "settled")
              .order_by(BetFinderTip.settled_at.desc(), BetFinderTip.id.desc()).all()):
        settled.setdefault(t.source, []).append(t)
    counts: dict[str, dict[str, int]] = {}
    for source, status, n in (db.query(BetFinderTip.source, BetFinderTip.status, func.count())
                              .group_by(BetFinderTip.source, BetFinderTip.status).all()):
        counts.setdefault(source, {})[status] = n
    out = {}
    for source in set(settled) | set(counts):
        tips = settled.get(source, [])
        out[source] = {"all": _block(tips), "rolling": _block(tips[:window]),
                       "window": window, "tips": counts.get(source, {})}
    return out
