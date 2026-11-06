"""The kill switch is the file data/KILL: anyone turns it on, only Callum turns it off."""
from puntmore_hub import killswitch
from puntmore_hub.store import Store


def test_on_writes_the_file_and_escalates_off_removes_it(cfg):
    store = Store(cfg.db_path)
    assert not cfg.kill_switch_on()
    killswitch.turn_on(store, cfg, by="the console")
    assert cfg.kill_switch_on() and "from the console" in cfg.kill_switch_file.read_text()
    assert [(e["kind"], e["ref"]) for e in store.open_escalations()] == [("kill_switch", "global")]
    assert not hasattr(killswitch, "from_render")
    killswitch.turn_off(cfg)
    assert not cfg.kill_switch_on()
