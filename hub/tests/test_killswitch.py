"""Render may turn the Hub's kill switch ON, never OFF (PROTOCOL.md, "Kill switches")."""
from puntmore_hub import killswitch
from puntmore_hub.store import Store


def test_render_turns_it_on_but_never_off(cfg):
    store = Store(cfg.db_path)
    assert not cfg.kill_switch_on()
    assert killswitch.from_render(store, {"on": False}, cfg) is False     # OFF while off: stays off, escalated
    assert not cfg.kill_switch_file.exists()
    assert killswitch.from_render(store, {"on": True}, cfg) is True
    assert cfg.kill_switch_file.exists() and "from Render" in cfg.kill_switch_file.read_text()
    assert killswitch.from_render(store, {"on": False}, cfg) is True      # OFF while on: still on
    assert cfg.kill_switch_file.exists()
    kinds = [(e["kind"], e["ref"]) for e in store.open_escalations()]
    assert kinds.count(("kill_switch", "render")) == 2 and ("kill_switch", "global") in kinds
    killswitch.turn_off(cfg)                                              # only Cal, by hand
    assert not cfg.kill_switch_on()
