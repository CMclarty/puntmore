from puntmore_hub.placement.adapters.base import PlacedResult, PlacementAdapter, PlacementFailed, PriceChanged
from puntmore_hub.placement.engine import ClientEngine
from puntmore_hub.schemas import Bet, BetLeg

ACCOUNTS = {"acc1": {"account_id": "acc1", "device": "phone-1", "bookmaker": "bet365"}}


def bet(bet_id="b1", account="acc1"):
    return Bet(bet_id=bet_id, account_id=account, legs=[BetLeg("ev1", "Match Result", "Home", 2.1)],
               stake=10.0, expected_price=2.1, bet_type="single")


SENT: dict[int, list] = {}


def reports(store):
    """What the engine reported, in order (the Bet Placer's own record; nothing goes to Render)."""
    return [body for type_, body in SENT.get(id(store), []) if type_ == "report"]


def engine(store, **kw):
    sent = SENT.setdefault(id(store), [])
    return ClientEngine(store, enqueue=lambda t, b: sent.append((t, b)), notify=False, sub_clients=ACCOUNTS, **kw)


def test_dry_run_places_and_reports(cfg, store):
    eng = engine(store, cfg=cfg)
    assert eng.handle_bet_list([bet()]) == ["placed"]
    rep = reports(store)
    assert rep[0]["kind"] == "placed" and rep[0]["dry_run"] is True and rep[0]["actual_price"] == 2.1
    assert store.open_exposure("acc1") == 10.0


def test_duplicate_bet_is_ignored(cfg, store):
    eng = engine(store, cfg=cfg)
    assert eng.handle_bet(bet()) == "placed"
    assert eng.handle_bet(bet()) == "duplicate"
    assert len(reports(store)) == 1


def test_kill_switch_fails_and_escalates(cfg, store):
    cfg.kill_switch_file.write_text("x")
    eng = engine(store, cfg=cfg)
    assert eng.handle_bet(bet()) == "failed"
    assert reports(store)[0]["kind"] == "failed"
    assert store.open_escalations()[0]["kind"] == "kill_switch"


def test_unknown_account_fails(cfg, store):
    eng = engine(store, cfg=cfg)
    assert eng.handle_bet(bet(account="nope")) == "failed"
    assert store.open_escalations()[0]["kind"] == "failed_bet"


class MovingPrice(PlacementAdapter):
    name = "moving"

    def place(self, b, account):
        raise PriceChanged(1.9)


class Broken(PlacementAdapter):
    name = "broken"

    def place(self, b, account):
        raise PlacementFailed("market suspended")


def test_price_change_reported_without_placing(cfg, store):
    live = cfg.with_overrides(dry_run=False)
    eng = engine(store, cfg=live, adapter=MovingPrice())
    assert eng.handle_bet(bet()) == "price_changed"
    assert reports(store)[0] == {**reports(store)[0], "kind": "price_changed", "new_price": 1.9}
    assert store.open_escalations() == []


def test_placement_failure_escalates_no_retry(cfg, store):
    live = cfg.with_overrides(dry_run=False)
    eng = engine(store, cfg=live, adapter=Broken())
    assert eng.handle_bet(bet()) == "failed"
    assert store.open_escalations()[0]["detail"].endswith("market suspended")
    assert len(reports(store)) == 1


def test_dry_run_overrides_live_adapter(cfg, store):
    eng = engine(store, cfg=cfg, adapter=Broken())
    assert eng.handle_bet(bet()) == "placed"
