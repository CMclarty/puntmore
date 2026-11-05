"""`extra_candidates`: the Bet Finder's rows for the 2-Up decision.

Only for the OWNER client (`HUB_OWNER_CLIENT_ID`, and the ledger must also
mark it `is_owner`), always `source_scope="owner"`. A row is offered when its
tip is priced (Match Result, matched to a feed fixture), its source is
ACTIVE, it has not expired or kicked off, its price was read within
`betfinder_max_price_age_minutes`, and the owner client has no live bet on
its selection in the ledger, from either row kind (a bet that came back
`price_changed` does not count, so the decision can re-price it as it does
a 2-Up row).

Note what rescue 0 implies: every priced tip is a selection the 2-Up feed
also prices, and the 2-Up row's edge is the tip's plus the rescue. At
`betfinder_min_edge` 0 the decision's one-row-per-selection rule therefore
keeps the 2-Up row whenever both pass. A tip is staked as a Bet Finder row
(advised units, trust weight) only where the 2-Up row fails its floor and
the tip clears `BETFINDER_MIN_EDGE` — which, with the rescue table's rates
above the 2-Up floor, needs a NEGATIVE `BETFINDER_MIN_EDGE`: backing a
tipster against Pinnacle. That is Cal's call, not a default. The tip's
record is kept either way (`pipeline.link_and_settle`).

Two sources on one selection make ONE candidate: the higher trust weight's
tip, then the earlier tip. `extra` names every tip and source behind it.

Stake mode, per source:
- `advised` — the tip's advised_stake_units x `betfinder_unit_stake`;
- `kelly`   — the engine's own fractional Kelly on p_win (rescue 0);
- `auto`    — advised when the tip carries units, else Kelly.
`advised` on a tip with no units falls back to Kelly, and says so. Both are
scaled by the source's trust weight and then bounded by every 2-Up cap
(`decision.target_stake`). The mode that applied is in `extra`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..models import BetFinderSource, BetFinderTip, TwoUpBet
from ..twoup import ledger
from ..twoup.model import Candidate


def _stake_terms(src: BetFinderSource, tip: BetFinderTip) -> dict:
    mode = src.stake_mode or "auto"
    units = tip.advised_stake_units
    out = {"stake_mode_setting": mode, "advised_stake_units": units,
           "unit_stake": settings.betfinder_unit_stake}
    if mode in ("advised", "auto") and units:
        out["stake_mode"] = "advised"
        out["fixed_stake"] = round(units * settings.betfinder_unit_stake, 2)
    else:
        out["stake_mode"] = "kelly"
        if mode == "advised":
            out["stake_mode_note"] = "advised, but the tip carries no units: Kelly"
    return out


def extra_candidates(db: Session, client_id: str, now: datetime | None = None) -> list[Candidate]:
    if not settings.enable_betfinder or client_id != settings.hub_owner_client_id:
        return []
    client = ledger.get_client(db, client_id)
    if client is None or not client.is_owner:
        return []
    now = now or datetime.now(timezone.utc)
    now_n = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)) \
        .astimezone(timezone.utc).replace(tzinfo=None)
    fresh = now_n - timedelta(minutes=settings.betfinder_max_price_age_minutes)
    rows = (db.query(BetFinderTip)
            .filter(BetFinderTip.status == "priced", BetFinderTip.priced_at >= fresh,
                    BetFinderTip.kickoff > now_n)
            .order_by(BetFinderTip.tip_time, BetFinderTip.id).all())
    rows = [t for t in rows if t.expiry is None or t.expiry > now_n]
    if not rows:
        return []
    active = {s.name: s for s in db.query(BetFinderSource)
              .filter(BetFinderSource.active.is_(True)).all()}
    rows = [t for t in rows if t.source in active]
    if not rows:
        return []
    live = {(b.event_id, b.selection) for b in db.query(TwoUpBet).filter(
        TwoUpBet.client_id == client_id, TwoUpBet.status != "price_changed",
        TwoUpBet.event_id.in_(sorted({t.event_id for t in rows}))).all()}

    groups: dict[tuple, list[BetFinderTip]] = {}
    for t in rows:
        if (t.event_id, t.selection) in live:
            continue
        groups.setdefault((t.event_id, t.selection), []).append(t)

    out = []
    for tips in groups.values():
        tips.sort(key=lambda t: (-active[t.source].trust_weight, t.tip_time or now_n, t.id))
        t = tips[0]
        src = active[t.source]
        extra = {
            "betfinder": True,
            "tip_id": t.tip_id,
            "tip_ids": [x.tip_id for x in tips],
            "sources": sorted({x.source for x in tips}),
            "origin": t.origin,
            "trust_weight": src.trust_weight,
            "stake_scale": src.trust_weight,
            "advised_price": t.advised_price,
            "min_edge": settings.betfinder_min_edge,
            "priced_at": t.priced_at.replace(tzinfo=timezone.utc).isoformat(),
            **_stake_terms(src, t),
        }
        out.append(Candidate(
            event_id=t.event_id, event=t.event, home=t.home, away=t.away,
            kickoff=t.kickoff.replace(tzinfo=timezone.utc), selection=t.selection,
            side=t.side, price=t.price, p_win=t.p_win, rescue=0.0, edge=t.edge,
            market="Match Result", competition=t.competition or "",
            fair_price=t.fair_price, source=src.name, source_scope="owner", extra=extra,
        ))
    return out
