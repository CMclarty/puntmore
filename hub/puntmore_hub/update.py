"""Self-update for the Hub: fast-forward the box's checkout to ``origin/main``, reinstall, restart.

Run by ``puntmore-update.timer`` every 10 minutes through ``deploy/update.sh`` (a thin wrapper
around ``puntmore update``). One run does each step at most once; a failure is logged to
``<data dir>/update.log`` and alerted through ``alerts.send``, never retried inside the run.

Rules:
  * Fast-forward only. Never merge, never reset. A checkout that has diverged from origin/main,
    has local edits to tracked files, or is on a detached HEAD is reported and left alone.
  * Never restart while a bet is in flight (status ``received`` or ``placing``: the client engine
    moves a bet ``received`` -> ``placing`` -> a final status). A blocked run changes nothing on
    disk and the next tick tries again.
  * If the code has moved but the install or restart did not finish (a failure, or a bet that
    arrived between the pull and the restart), ``<data dir>/UPDATE_PENDING`` remembers it, so the
    next tick reinstalls and restarts even though the checkout is already up to date.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable

from .config import ROOT, Settings, settings as default_settings
from .store import Store, iso, parse_iso, utcnow

REMOTE = "origin"
BRANCH = "main"
UNITS = ("puntmore-link.service", "puntmore-web.service", "puntmore-heartbeat.timer")
# The Scraper (scraper/) on the Hub's desktop. Restarted only when a pull changes scraper/: a
# restart closes and reopens every kept-open page, and those loads come out of the sites'
# budgets. (A pending update whose diff is not known restarts it too.)
SCRAPER_UNIT = "puntmore-scraper.service"
SCRAPER_DIR = "scraper"
IN_FLIGHT = ("received", "placing")        # placement/engine.py: not yet a final status
STALE_IN_FLIGHT = timedelta(minutes=30)    # a bet stuck this long is a crash, not a placement
VENV_PYTHON = Path.home() / ".venvs" / "puntmore" / "bin" / "python"


class UpdateError(Exception):
    """A step failed. ``kind`` names the step for the log and the alert key."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


# ---- git ----------------------------------------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise UpdateError("git", f"git {' '.join(args)}: timed out") from None
    if p.returncode != 0:
        raise UpdateError("git", f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()[:500]}")
    return p.stdout.strip()


@dataclass
class GitState:
    branch: str          # "" on a detached HEAD
    head: str
    target: str          # origin/main after the fetch
    behind: int          # commits on origin/main not in HEAD
    ahead: int           # commits in HEAD not on origin/main
    dirty: list[str] = field(default_factory=list)   # tracked files with local changes

    @property
    def up_to_date(self) -> bool:
        return self.behind == 0

    @property
    def fast_forward(self) -> bool:
        return self.behind > 0 and self.ahead == 0


def git_state(repo: Path, fetch: bool = True) -> GitState:
    if fetch:
        git(repo, "fetch", "--quiet", REMOTE, BRANCH)
    target_ref = f"refs/remotes/{REMOTE}/{BRANCH}"
    head = git(repo, "rev-parse", "HEAD")
    target = git(repo, "rev-parse", target_ref)
    ahead, behind = (int(n) for n in git(repo, "rev-list", "--left-right", "--count", f"HEAD...{target_ref}").split())
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    dirty = [line[3:] for line in git(repo, "status", "--porcelain", "--untracked-files=no").splitlines() if line]
    return GitState(branch="" if branch == "HEAD" else branch, head=head, target=target,
                    behind=behind, ahead=ahead, dirty=dirty)


# ---- the store ----------------------------------------------------------------------------------

def in_flight_bets(db_path: Path) -> list[dict]:
    """Bets the client engine has not finished with. Read-only; a missing database means none."""
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        q = f"SELECT bet_id, account_id, status, updated FROM bets WHERE status IN ({','.join('?' * len(IN_FLIGHT))})"
        return [dict(r) for r in conn.execute(q, IN_FLIGHT).fetchall()]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        conn.close()


# ---- install and restart ------------------------------------------------------------------------

def _has_extra(hub_dir: Path, extra: str) -> bool:
    try:
        data = tomllib.loads((hub_dir / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return extra in (data.get("project", {}).get("optional-dependencies") or {})


def install_command(repo: Path, python: Path = VENV_PYTHON) -> list[str]:
    """``uv pip install -e 'hub[web]' --python <venv>``; plain ``hub`` while there is no ``web`` extra.

    Falls back to the venv's own pip when uv is not on PATH (``PUNTMORE_UV`` names it explicitly).
    """
    hub_dir = repo / "hub"
    spec = f"{hub_dir}[web]" if _has_extra(hub_dir, "web") else str(hub_dir)
    uv = os.environ.get("PUNTMORE_UV") or shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--quiet", "-e", spec, "--python", str(python)]
    return [str(python), "-m", "pip", "install", "--quiet", "-e", spec]


def scraper_install_command(repo: Path, python: Path = VENV_PYTHON) -> list[str]:
    """``uv pip install -r scraper/requirements.txt --python <venv>``: the Scraper's deps, same venv."""
    req = str(repo / SCRAPER_DIR / "requirements.txt")
    uv = os.environ.get("PUNTMORE_UV") or shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--quiet", "-r", req, "--python", str(python)]
    return [str(python), "-m", "pip", "install", "--quiet", "-r", req]


def install_scraper(repo: Path, python: Path = VENV_PYTHON) -> None:
    _install(scraper_install_command(repo, python))


def install(repo: Path, python: Path = VENV_PYTHON) -> None:
    _install(install_command(repo, python))


def _install(cmd: list[str]) -> None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("install", f"{' '.join(cmd)}: {exc!r}") from None
    if p.returncode != 0:
        raise UpdateError("install", f"{' '.join(cmd)}: {(p.stderr or p.stdout).strip()[-800:]}")


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=60)


def enabled_units(units: tuple[str, ...] = UNITS + (SCRAPER_UNIT,)) -> list[str]:
    """The units that exist and are enabled (``is-enabled`` prints 'enabled' for exactly those)."""
    try:
        return [u for u in units if _systemctl("is-enabled", u).stdout.strip() == "enabled"]
    except (OSError, subprocess.TimeoutExpired):
        return []


def restart(units: list[str]) -> None:
    failed = []
    for u in units:   # each once; a failure is reported with the rest, not retried
        try:
            p = _systemctl("restart", u)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed.append(f"{u}: {exc!r}")
            continue
        if p.returncode != 0:
            failed.append(f"{u}: {(p.stderr or p.stdout).strip()[:200]}")
    if failed:
        raise UpdateError("restart", "; ".join(failed))


# ---- one run ------------------------------------------------------------------------------------

@dataclass
class Result:
    outcome: str        # up_to_date | updated | blocked | would_update | failed | busy
    branch: str = ""
    from_sha: str = ""
    to_sha: str = ""
    detail: str = ""
    plan: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome != "failed"

    def log_line(self) -> str:
        return (f"{iso(utcnow())} {self.outcome} branch={self.branch or '-'} "
                f"from={self.from_sha[:12] or '-'} to={self.to_sha[:12] or '-'}"
                + (f" {self.detail}" if self.detail else ""))


def default_repo() -> Path:
    return ROOT.parent   # hub/ sits at the top of the checkout


def write_log(cfg: Settings, line: str) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.data_dir / "update.log", "a") as fh:
        fh.write(line.replace("\n", " ") + "\n")


def _alert(cfg: Settings, key: str, message: str, level: str = "error") -> None:
    from . import alerts
    try:   # an alert that cannot be sent must not stop the run being logged
        st = Store(cfg.db_path)
        try:
            alerts.send(st, key, message, level=level, cfg=cfg)
        finally:
            st.close()
    except Exception as exc:  # noqa: BLE001
        write_log(cfg, f"{iso(utcnow())} alert_failed {key}: {exc!r}")


def run(cfg: Settings = default_settings, repo: Path | None = None, *, dry_run: bool = False,
        installer: Callable[[Path], None] | None = None,
        restarter: Callable[[list[str]], None] | None = None,
        units: Callable[[], list[str]] | None = None,
        scraper_installer: Callable[[Path], None] | None = None) -> Result:
    """One update pass. Logs the result (not in a dry run) and alerts on failure."""
    repo = Path(repo or default_repo())
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.data_dir / "update.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            res = Result("busy", detail="another update run holds the lock")
        else:
            res = _run(cfg, repo, dry_run, installer or install, restarter or restart, units or enabled_units,
                       scraper_installer or install_scraper)
    if not dry_run:
        write_log(cfg, res.log_line())
    return res


def _run(cfg: Settings, repo: Path, dry_run: bool, installer, restarter, units, scraper_installer) -> Result:
    pending_file = cfg.data_dir / "UPDATE_PENDING"
    res = Result("failed")
    try:
        gs = git_state(repo)
    except UpdateError as exc:
        return _fail(cfg, res, exc, dry_run)
    res.branch, res.from_sha, res.to_sha = gs.branch, gs.head, gs.head
    pending = pending_file.read_text().strip() if pending_file.exists() else ""

    if not gs.branch:
        return _fail(cfg, res, UpdateError("detached", "checkout is on a detached HEAD; not updating"), dry_run)
    if gs.behind and not gs.fast_forward:
        return _fail(cfg, res, UpdateError(
            "diverged", f"{gs.branch} has {gs.ahead} commit(s) not on {REMOTE}/{BRANCH} and is {gs.behind} "
                        "behind: not a fast-forward, left alone"), dry_run)
    if gs.behind and gs.dirty:
        return _fail(cfg, res, UpdateError(
            "dirty", f"local changes to tracked files ({', '.join(gs.dirty[:5])}); not pulling"), dry_run)
    if gs.up_to_date and not pending:
        res.outcome = "up_to_date"
        return res

    if gs.behind:
        res.to_sha = gs.target
    to_restart = units()
    # What the pull brings under scraper/; None when not known (finishing a pending update).
    try:
        scraper_changes = (git(repo, "diff", "--name-only", gs.head, gs.target, "--", SCRAPER_DIR).split()
                          if gs.behind else None)
    except UpdateError as exc:
        return _fail(cfg, res, exc, dry_run)
    if SCRAPER_UNIT in to_restart and scraper_changes == []:
        to_restart = [u for u in to_restart if u != SCRAPER_UNIT]
    scraper_deps = SCRAPER_UNIT in to_restart and (
        scraper_changes is None or f"{SCRAPER_DIR}/requirements.txt" in scraper_changes)
    res.plan = ([f"git pull --ff-only {REMOTE} {BRANCH}  ({gs.head[:12]} -> {gs.target[:12]}, {gs.behind} commit(s))"]
                if gs.behind else [f"finish the update left pending at {pending[:12]}"])
    res.plan.append(" ".join(install_command(repo)))
    if scraper_deps:
        res.plan.append(" ".join(scraper_install_command(repo)))
    res.plan += [("systemctl --user restart " + " ".join(to_restart)) if to_restart else "no enabled units to restart"]

    flying = in_flight_bets(cfg.db_path)
    if flying:
        res.outcome = "blocked"
        res.detail = f"{len(flying)} bet(s) in flight: " + ", ".join(f"{b['bet_id']}={b['status']}" for b in flying[:5])
        res.plan = [f"wait: {res.detail}; the next tick tries again"]
        oldest = min((parse_iso(b["updated"]) for b in flying if b["updated"]), default=None)
        if not dry_run and oldest and utcnow() - oldest > STALE_IN_FLIGHT:
            _alert(cfg, "update:blocked", f"hub update waiting since a bet went in flight at {iso(oldest)}: "
                   f"{res.detail}. A bet stuck this long needs a human.", level="warn")
        return res
    if dry_run:
        res.outcome = "would_update"
        return res

    try:
        if gs.behind:
            git(repo, "pull", "--ff-only", "--quiet", REMOTE, BRANCH)
            res.to_sha = git(repo, "rev-parse", "HEAD")
            pending_file.write_text(res.to_sha + "\n")
            changed = git(repo, "diff", "--name-only", gs.head, res.to_sha, "--", "hub/deploy/systemd",
                          f"{SCRAPER_DIR}/deploy/native/{SCRAPER_UNIT}").split()
            if changed:
                res.detail = ("unit files changed, copy them to ~/.config/systemd/user (the Scraper's: "
                              "run scraper/deploy/native/install.sh): " + " ".join(changed))
                _alert(cfg, f"update:units:{res.to_sha[:12]}", f"hub updated to {res.to_sha[:12]}; {res.detail}",
                       level="warn")
        installer(repo)
        if scraper_deps:
            scraper_installer(repo)
        flying = in_flight_bets(cfg.db_path)   # a bet may have arrived during the pull and the install
        if flying:
            res.outcome = "blocked"
            res.detail = (f"code at {res.to_sha[:12]} but {len(flying)} bet(s) in flight; "
                          "restart left pending for the next tick")
            return res
        restarter(to_restart)
    except UpdateError as exc:
        return _fail(cfg, res, exc, dry_run)
    pending_file.unlink(missing_ok=True)
    res.outcome = "updated"
    res.detail = (res.detail + "; " if res.detail else "") + "restarted " + (", ".join(to_restart) or "nothing")
    return res


def _fail(cfg: Settings, res: Result, exc: UpdateError, dry_run: bool) -> Result:
    res.outcome = "failed"
    res.detail = f"{exc.kind}: {exc.detail}"
    if not dry_run:
        _alert(cfg, f"update:{exc.kind}",
               f"hub update failed ({res.branch or '?'} at {res.from_sha[:12] or '?'}): {res.detail}")
    return res


def check(repo: Path | None = None) -> tuple[int, str]:
    """``--check``: 0 up to date, 1 behind, 2 could not tell."""
    try:
        gs = git_state(Path(repo or default_repo()))
    except UpdateError as exc:
        return 2, f"error: {exc.detail}"
    if gs.up_to_date:
        return 0, f"up to date: {gs.branch or 'HEAD'} at {gs.head[:12]}"
    return 1, (f"behind: {gs.branch or 'HEAD'} at {gs.head[:12]}, {REMOTE}/{BRANCH} at {gs.target[:12]} "
               f"({gs.behind} commit(s))")


def dry_run_report(res: Result) -> str:
    lines = [f"dry run: {res.branch or '?'} at {res.from_sha[:12] or '?'}"
             + (f", {REMOTE}/{BRANCH} at {res.to_sha[:12]}" if res.to_sha != res.from_sha else "")]
    if res.outcome == "failed":
        lines.append(f"  would stop and alert: {res.detail}")
    elif res.outcome == "up_to_date":
        lines.append("  up to date; nothing to do")
    else:
        lines += [f"  would run: {step}" if not step.startswith("wait") else f"  {step}" for step in res.plan]
    return "\n".join(lines)


def main(a, cfg: Settings = default_settings) -> None:
    """``puntmore update [--dry-run | --check] [--repo PATH]``."""
    repo = Path(a.repo) if a.repo else None
    if a.check:
        code, msg = check(repo)
        print(msg)
        sys.exit(code)
    res = run(cfg, repo, dry_run=a.dry_run)
    print(dry_run_report(res) if a.dry_run else res.log_line())
    if not res.ok:
        sys.exit(1)
