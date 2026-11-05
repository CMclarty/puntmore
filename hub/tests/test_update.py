"""The Hub's self-update (puntmore_hub/update.py) against a real git repo with a bare "origin"."""
from __future__ import annotations

import re
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from puntmore_hub import update
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
