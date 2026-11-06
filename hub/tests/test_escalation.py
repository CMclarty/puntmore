"""Escalations reach the Hermes profile that owns them: a Scraper source goes to the
scraper-manager, which has the Scraper-ops runbook; everything else to the default profile."""
import subprocess

from puntmore_hub import escalation


def test_source_escalations_go_to_the_scraper_manager(cfg):
    assert escalation.profile_for("unhealthy_source", cfg) == "scraper-manager"
    assert escalation.profile_for("blocked_source", cfg) == "scraper-manager"
    for kind in ("failed_bet", "kill_switch", "quarantined_item", "other"):
        assert escalation.profile_for(kind, cfg) == cfg.hermes_profile == "default"


def test_the_brief_names_the_runbook_only_for_a_source(cfg, store):
    a = escalation.raise_escalation(store, "unhealthy_source", "tips-a", "only 0 row(s)", cfg=cfg, notify=False)
    b = escalation.raise_escalation(store, "failed_bet", "b-1", "market suspended", cfg=cfg, notify=False)
    assert "puntmore-scraper-ops" in escalation.brief_for(store, a)
    assert "puntmore-scraper-ops" not in escalation.brief_for(store, b)


def test_hermes_is_launched_with_the_profile_for_the_kind(cfg, store, tmp_path, monkeypatch):
    fake = tmp_path / "hermes"
    fake.write_text("#!/bin/sh\n")
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    live = cfg.with_overrides(hermes_bin=str(fake))
    a = escalation.raise_escalation(store, "blocked_source", "generic", "HTTP 403", cfg=live, notify=False)
    b = escalation.raise_escalation(store, "failed_bet", "b-1", "x", cfg=live, notify=False)
    assert escalation.notify_hubai(store, a, cfg=live) and escalation.notify_hubai(store, b, cfg=live)
    assert [c[:3] for c in launched] == [[str(fake), "-p", "scraper-manager"], [str(fake), "-p", "default"]]


def test_escalate_to_hubai_reads_the_new_name_and_still_the_old(monkeypatch):
    from puntmore_hub.config import _env_bool_first
    names = ("PUNTMORE_ESCALATE_TO_HUBAI", "PUNTMORE_ESCALATE_TO_LOCAL_AI")
    for n in names:
        monkeypatch.delenv(n, raising=False)
    assert _env_bool_first(*names, default=True) is True
    monkeypatch.setenv("PUNTMORE_ESCALATE_TO_LOCAL_AI", "0")       # an old .env
    assert _env_bool_first(*names, default=True) is False
    monkeypatch.setenv("PUNTMORE_ESCALATE_TO_HUBAI", "1")          # the new name wins
    assert _env_bool_first(*names, default=True) is True
    # A blank new name counts as unset, as in _env_first: the old name is read.
    monkeypatch.setenv("PUNTMORE_ESCALATE_TO_HUBAI", "")
    monkeypatch.setenv("PUNTMORE_ESCALATE_TO_LOCAL_AI", "1")
    assert _env_bool_first(*names, default=False) is True
    monkeypatch.setenv("PUNTMORE_ESCALATE_TO_LOCAL_AI", " ")
    assert _env_bool_first(*names, default=True) is True
    from puntmore_hub.config import Settings
    assert "escalate_to_hubai" in {f.name for f in __import__("dataclasses").fields(Settings)}
