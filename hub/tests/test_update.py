"""The Hub's self-update (puntmore_hub/update.py) against a real git repo with a bare "origin"."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from puntmore_hub import aw26, aw26_loop, update
from puntmore_hub.store import Store, iso, utcnow


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, text: str = "x") -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    sh(repo, "add", name)
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
    return sh(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path: Path):
    """(box checkout, a second clone that pushes to origin, origin itself)."""
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    dev = tmp_path / "dev"
    sh(tmp_path, "clone", "-q", str(origin), str(dev))
    sh(dev, "checkout", "-q", "-b", "main")
    commit(dev, "hub/pyproject.toml", "[project]\nname='x'\n")
    sh(dev, "push", "-q", "origin", "main")
    box = tmp_path / "box"
    sh(tmp_path, "clone", "-q", str(origin), str(box))
    return box, dev, origin


def push(dev: Path, name: str, text: str = "x") -> str:
    sha = commit(dev, name, text)
    sh(dev, "push", "-q", "origin", "main")
    return sha


class Fakes:
    def __init__(self):
        self.calls: list = []

    def installer(self, repo):
        self.calls.append(("install", Path(repo)))

    def restarter(self, units):
        self.calls.append(("restart", list(units)))

    def units(self):
        return ["puntmore-link.service", "puntmore-heartbeat.timer"]

    def kw(self):
        return dict(installer=self.installer, restarter=self.restarter, units=self.units)


def add_bet(cfg, bet_id: str, status: str, minutes_ago: float = 0) -> None:
    st = Store(cfg.db_path)
    st.upsert_bet(bet_id, "acc1", {"stake": 1.0}, status)
    st.conn.execute("UPDATE bets SET updated=? WHERE bet_id=?", (iso(utcnow() - timedelta(minutes=minutes_ago)), bet_id))
    st.conn.commit()
    st.close()


def alerts_sent(cfg) -> list[str]:
    if not cfg.db_path.exists():
        return []
    st = Store(cfg.db_path)
    keys = [r["key"] for r in st.conn.execute("SELECT key FROM alerts").fetchall()]
    st.close()
    return keys


# ---- behind / ahead detection --------------------------------------------------------------------

def test_up_to_date_then_behind(repos):
    box, dev, _ = repos
    assert update.git_state(box).up_to_date
    assert update.check(box)[0] == 0
    new = push(dev, "a.txt")
    gs = update.git_state(box)
    assert (gs.behind, gs.ahead, gs.target, gs.fast_forward) == (1, 0, new, True)
    code, msg = update.check(box)
    assert code == 1 and "behind" in msg


def test_local_commit_ahead_is_up_to_date(repos):
    box, _, _ = repos
    commit(box, "local.txt")
    gs = update.git_state(box)
    assert (gs.behind, gs.ahead, gs.up_to_date) == (0, 1, True)


def test_check_reports_a_broken_remote(repos):
    box, _, _ = repos
    sh(box, "remote", "set-url", "origin", str(box.parent / "nowhere.git"))
    assert update.check(box)[0] == 2


# ---- a run ---------------------------------------------------------------------------------------

def test_fast_forward_installs_restarts_and_logs(repos, cfg):
    box, dev, _ = repos
    old = sh(box, "rev-parse", "HEAD")
    new = push(dev, "hub/puntmore_hub/new.py")
    f = Fakes()
    res = update.run(cfg, box, **f.kw())
    assert res.outcome == "updated", res.detail
    assert sh(box, "rev-parse", "HEAD") == new
    assert f.calls == [("install", box), ("restart", ["puntmore-link.service", "puntmore-heartbeat.timer"])]
    assert not (cfg.data_dir / "UPDATE_PENDING").exists()
    line = (cfg.data_dir / "update.log").read_text().splitlines()[-1]
    assert re.match(rf"^\d{{4}}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00 updated branch=main from={old[:12]} to={new[:12]} ", line)
    # the next tick has nothing to do, and says so in the log
    assert update.run(cfg, box, **f.kw()).outcome == "up_to_date"
    assert len(f.calls) == 2
    assert " up_to_date branch=main " in (cfg.data_dir / "update.log").read_text().splitlines()[-1]


def test_diverged_is_alerted_never_forced(repos, cfg):
    box, dev, _ = repos
    mine = commit(box, "local.txt")
    push(dev, "a.txt")
    f = Fakes()
    res = update.run(cfg, box, **f.kw())
    assert res.outcome == "failed" and "diverged" in res.detail
    assert sh(box, "rev-parse", "HEAD") == mine and f.calls == []
    assert "update:diverged" in alerts_sent(cfg)
    assert " failed " in (cfg.data_dir / "update.log").read_text()


def test_dirty_checkout_is_not_pulled(repos, cfg):
    box, dev, _ = repos
    (box / "hub" / "pyproject.toml").write_text("edited on the box\n")
    before = sh(box, "rev-parse", "HEAD")
    push(dev, "a.txt")
    res = update.run(cfg, box, **Fakes().kw())
    assert res.outcome == "failed" and "dirty" in res.detail
    assert sh(box, "rev-parse", "HEAD") == before
    assert (box / "hub" / "pyproject.toml").read_text() == "edited on the box\n"


def test_install_failure_is_alerted_once_and_left_pending(repos, cfg):
    box, dev, _ = repos
    new = push(dev, "a.txt")
    attempts = []

    def bad_install(repo):
        attempts.append(repo)
        raise update.UpdateError("install", "no network")

    f = Fakes()
    res = update.run(cfg, box, installer=bad_install, restarter=f.restarter, units=f.units)
    assert res.outcome == "failed" and len(attempts) == 1 and f.calls == []
    assert "update:install" in alerts_sent(cfg)
    assert (cfg.data_dir / "UPDATE_PENDING").read_text().strip() == new
    # next tick: checkout is current, but the pending marker finishes the job
    res = update.run(cfg, box, **f.kw())
    assert res.outcome == "updated" and [c[0] for c in f.calls] == ["install", "restart"]
    assert not (cfg.data_dir / "UPDATE_PENDING").exists()


# ---- in-flight bets ------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["placing", "received"])
def test_in_flight_bet_blocks_everything(repos, cfg, status):
    box, dev, _ = repos
    before = sh(box, "rev-parse", "HEAD")
    push(dev, "a.txt")
    add_bet(cfg, "b1", status)
    f = Fakes()
    res = update.run(cfg, box, **f.kw())
    assert res.outcome == "blocked" and "b1" in res.detail
    assert sh(box, "rev-parse", "HEAD") == before and f.calls == []
    assert alerts_sent(cfg) == []                       # fresh: just wait for the next tick
    # the bet finishes; the next tick goes through
    st = Store(cfg.db_path); st.set_bet_status("b1", "placed"); st.close()
    assert update.run(cfg, box, **f.kw()).outcome == "updated"


def test_finished_bets_do_not_block(repos, cfg):
    box, dev, _ = repos
    push(dev, "a.txt")
    for i, s in enumerate(["placed", "failed", "price_changed", "settled"]):
        add_bet(cfg, f"b{i}", s)
    assert update.run(cfg, box, **Fakes().kw()).outcome == "updated"


def test_stuck_in_flight_bet_is_alerted(repos, cfg):
    box, dev, _ = repos
    push(dev, "a.txt")
    add_bet(cfg, "stuck", "placing", minutes_ago=90)
    assert update.run(cfg, box, **Fakes().kw()).outcome == "blocked"
    assert alerts_sent(cfg) == ["update:blocked"]


def test_bet_arriving_during_install_defers_the_restart(repos, cfg):
    box, dev, _ = repos
    new = push(dev, "a.txt")
    f = Fakes()

    def install_then_bet(repo):
        f.calls.append(("install", repo))
        add_bet(cfg, "late", "placing")

    res = update.run(cfg, box, installer=install_then_bet, restarter=f.restarter, units=f.units)
    assert res.outcome == "blocked" and sh(box, "rev-parse", "HEAD") == new
    assert [c[0] for c in f.calls] == ["install"]
    assert (cfg.data_dir / "UPDATE_PENDING").exists()
    st = Store(cfg.db_path); st.set_bet_status("late", "placed"); st.close()
    assert update.run(cfg, box, **f.kw()).outcome == "updated"
    assert f.calls[-1][0] == "restart"


# ---- dry run -------------------------------------------------------------------------------------

def test_dry_run_changes_nothing(repos, cfg):
    box, dev, _ = repos
    before = sh(box, "rev-parse", "HEAD")
    new = push(dev, "a.txt")
    f = Fakes()
    res = update.run(cfg, box, dry_run=True, **f.kw())
    assert res.outcome == "would_update" and res.to_sha == new
    assert sh(box, "rev-parse", "HEAD") == before and f.calls == []
    assert not (cfg.data_dir / "update.log").exists() and alerts_sent(cfg) == []
    report = update.dry_run_report(res)
    assert "would run: git pull --ff-only origin main" in report
    assert "would run: systemctl --user restart puntmore-link.service puntmore-heartbeat.timer" in report


def test_dry_run_with_a_bet_in_flight_says_it_would_wait(repos, cfg):
    box, dev, _ = repos
    push(dev, "a.txt")
    add_bet(cfg, "b1", "placing")
    res = update.run(cfg, box, dry_run=True, **Fakes().kw())
    assert "wait: 1 bet(s) in flight" in update.dry_run_report(res)


def test_cli_check_and_dry_run(repos, cfg, capsys):
    from puntmore_hub.cli import build_parser
    box, dev, _ = repos
    push(dev, "a.txt")
    a = build_parser().parse_args(["update", "--check", "--repo", str(box)])
    with pytest.raises(SystemExit) as e:
        update.main(a, cfg)
    assert e.value.code == 1 and "behind" in capsys.readouterr().out
    a = build_parser().parse_args(["update", "--dry-run", "--repo", str(box)])
    update.main(a, cfg)
    assert "dry run: main" in capsys.readouterr().out


# ---- install command -----------------------------------------------------------------------------

def test_install_command_tolerates_a_missing_web_extra(tmp_path, monkeypatch):
    monkeypatch.setenv("PUNTMORE_UV", "/usr/bin/uv")
    (tmp_path / "hub").mkdir()
    (tmp_path / "hub" / "pyproject.toml").write_text("[project]\nname='x'\n[project.optional-dependencies]\ndev=['pytest']\n")
    cmd = update.install_command(tmp_path, Path("/venv/bin/python"))
    assert cmd == ["/usr/bin/uv", "pip", "install", "--quiet", "-e", str(tmp_path / "hub"), "--python", "/venv/bin/python"]
    (tmp_path / "hub" / "pyproject.toml").write_text("[project]\nname='x'\n[project.optional-dependencies]\nweb=['fastapi']\n")
    assert update.install_command(tmp_path, Path("/venv/bin/python"))[5] == f"{tmp_path / 'hub'}[web]"


def test_update_sh_parses():
    script = Path(__file__).resolve().parent.parent / "deploy" / "update.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)


# ---- the Scraper (scraper/) on the desktop --------------------------------------------------------

class ScraperFakes(Fakes):
    def units(self):
        return ["puntmore-link.service", update.SCRAPER_UNIT]

    def scraper_installer(self, repo):
        self.calls.append(("install-scraper", Path(repo)))

    def kw(self):
        return dict(super().kw(), scraper_installer=self.scraper_installer)


def test_scraper_is_an_updater_unit():
    assert update.SCRAPER_UNIT == "puntmore-scraper.service"
    assert update.SCRAPER_UNIT in update.enabled_units.__defaults__[0]


def test_a_scraper_change_restarts_the_scraper(repos, cfg):
    box, dev, _ = repos
    push(dev, "scraper/parsers/pp_most.py")
    f = ScraperFakes()
    res = update.run(cfg, box, **f.kw())
    assert res.outcome == "updated", res.detail
    assert f.calls == [("install", box), ("restart", ["puntmore-link.service", update.SCRAPER_UNIT])]


def test_a_hub_only_change_leaves_the_scraper_running(repos, cfg):
    """A Scraper restart reopens every kept-open page, out of the sites' budgets."""
    box, dev, _ = repos
    push(dev, "hub/puntmore_hub/new.py")
    f = ScraperFakes()
    assert update.run(cfg, box, **f.kw()).outcome == "updated"
    assert f.calls == [("install", box), ("restart", ["puntmore-link.service"])]


def test_new_scraper_requirements_are_installed_before_the_restart(repos, cfg):
    box, dev, _ = repos
    push(dev, "scraper/requirements.txt", "playwright==1.56.0\n")
    f = ScraperFakes()
    res = update.run(cfg, box, dry_run=True, **f.kw())
    assert any("-r " + str(box / "scraper" / "requirements.txt") in step for step in res.plan)
    assert update.run(cfg, box, **f.kw()).outcome == "updated"
    assert [c[0] for c in f.calls] == ["install", "install-scraper", "restart"]
    assert update.SCRAPER_UNIT in f.calls[-1][1]


def test_scraper_not_enabled_is_not_restarted(repos, cfg):
    box, dev, _ = repos
    push(dev, "scraper/browser.py")
    f = Fakes()                                   # the scraper unit is not enabled on this box
    res = update.run(cfg, box, **f.kw(), scraper_installer=lambda r: f.calls.append(("x", r)))
    assert res.outcome == "updated"
    assert f.calls == [("install", box), ("restart", ["puntmore-link.service", "puntmore-heartbeat.timer"])]


# ---- the Hermes runbooks -------------------------------------------------------------------------

def test_update_copies_the_runbook_into_its_profile(repos, cfg):
    box, dev, _ = repos
    push(dev, "hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md", "# v2\n")
    profile = cfg.hermes_home / "profiles" / "scraper-manager"
    stale = profile / "skills" / "puntmore-scraper-ops"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# v1\n")
    (stale / "old-note.md").write_text("gone after the sync\n")
    f = Fakes()
    res = update.run(cfg, box, **f.kw())
    assert res.outcome == "updated", res.detail
    assert f"runbooks: puntmore-scraper-ops -> {profile / 'skills'}" in res.detail
    assert (stale / "SKILL.md").read_text() == "# v2\n" and not (stale / "old-note.md").exists()
    # a second sync with nothing changed copies nothing
    assert update.sync_hermes_skills(box, cfg) == []


def test_a_profile_that_is_not_on_the_box_is_skipped(repos, cfg):
    box, dev, _ = repos
    push(dev, "hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md", "# v2\n")
    assert update.sync_hermes_skills(box, cfg) == []
    assert not (cfg.hermes_home / "profiles").exists()


def test_skill_targets(cfg):
    home = cfg.hermes_home
    assert update.skill_targets(cfg) == {
        "puntmore-scraper-ops": [home / "profiles" / "scraper-manager" / "skills"],
        "puntmore-aw26": [home / "profiles" / "betting-client-manager" / "skills", home / "skills"],
    }


def test_the_aw26_runbook_goes_to_the_reader_and_the_chief_of_staff(repos, cfg):
    box, dev, _ = repos
    push(dev, "hub/deploy/hermes-skills/puntmore-aw26/SKILL.md", "# aw26\n")
    reader = cfg.hermes_home / "profiles" / "betting-client-manager"
    reader.mkdir(parents=True)
    f = Fakes()
    res = update.run(cfg, box, **f.kw())
    assert res.outcome == "updated", res.detail
    assert update.sync_hermes_skills(box, cfg) == []          # already in: a second sync changes nothing
    for skills in (reader / "skills", cfg.hermes_home / "skills"):
        assert (skills / "puntmore-aw26" / "SKILL.md").read_text() == "# aw26\n"
        assert f"puntmore-aw26 -> {skills}" in res.detail


def test_a_missing_reader_profile_still_installs_for_the_chief_of_staff(repos, cfg):
    box, dev, _ = repos
    push(dev, "hub/deploy/hermes-skills/puntmore-aw26/SKILL.md", "# aw26\n")
    cfg.hermes_home.mkdir()
    sh(box, "pull", "-q")
    assert update.sync_hermes_skills(box, cfg) == [f"puntmore-aw26 -> {cfg.hermes_home / 'skills'}"]
    assert not (cfg.hermes_home / "profiles").exists()


def test_the_scraper_runbook_goes_only_to_its_profile(repos, cfg):
    box, dev, _ = repos
    push(dev, "hub/deploy/hermes-skills/puntmore-scraper-ops/SKILL.md", "# ops\n")
    push(dev, "hub/deploy/hermes-skills/puntmore-aw26/SKILL.md", "# aw26\n")
    sh(box, "pull", "-q")
    for p in ("scraper-manager", "betting-client-manager"):
        (cfg.hermes_home / "profiles" / p).mkdir(parents=True)
    update.sync_hermes_skills(box, cfg)
    installed = sorted(str(p.relative_to(cfg.hermes_home)) for p in cfg.hermes_home.rglob("SKILL.md"))
    assert installed == ["profiles/betting-client-manager/skills/puntmore-aw26/SKILL.md",
                         "profiles/scraper-manager/skills/puntmore-scraper-ops/SKILL.md",
                         "skills/puntmore-aw26/SKILL.md"]


# ---- the AW 26 runbook itself ---------------------------------------------------------------------

AW26_RUNBOOK = Path(__file__).resolve().parents[1] / "deploy" / "hermes-skills" / "puntmore-aw26" / "SKILL.md"


def test_the_aw26_runbook_is_the_skill_the_daemon_names():
    text = AW26_RUNBOOK.read_text()
    assert text.startswith("---\nname: puntmore-aw26\n")
    assert aw26_loop.SKILL == "puntmore-aw26"


def test_the_aw26_runbook_reads_first_and_keeps_the_upload_for_the_chief_of_staff():
    text = AW26_RUNBOOK.read_text()
    headings = re.findall(r"^## (.+)$", text, re.M)
    reading = next(i for i, h in enumerate(headings) if h.startswith("Reading"))
    telegram = next(i for i, h in enumerate(headings) if "Telegram" in h)
    assert reading < telegram == len(headings) - 1 and "chief of staff" in headings[telegram]
    guard = ("If you were given a photo id, you are the reader: go to Reading and never run "
             "`puntmore aw26 upload`.")
    assert guard in text and text.index(guard) < text.index("## ")
    assert text.count("puntmore aw26 upload") == 2          # the guard, and the chief of staff's step


def test_the_aw26_runbook_names_only_real_commands():
    named = set(re.findall(r"puntmore aw26 (\w+)", AW26_RUNBOOK.read_text()))
    assert named == {"fail", "board", "record", "upload"}


def test_the_aw26_worked_example_is_a_body_record_takes(cfg):
    blocks = re.findall(r"```json\n(.*?)```", AW26_RUNBOOK.read_text(), re.S)
    slip = json.loads(blocks[-1])
    assert set(slip) <= set(aw26.SLIP_KEYS) and "photo_id" in slip
    assert all(set(leg) <= set(aw26.LEG_KEYS) for leg in slip["legs"])
    body = aw26.record(cfg, slip, dry_run=True)["dry_run"]        # every printed time reads
    assert body["receipt"] == "B0000000000000-000000-0"
    assert body["offer"] == "boost" and len(body["legs"]) == 5
