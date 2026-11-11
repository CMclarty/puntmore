"""The daemon's AW 26 loop: one waiting slip photo at a time, read by HubAI.

Each pass (`read_once`) lists Render's photo queue, takes the oldest `waiting` photo, downloads
it under `<data>/aw26/` and starts Hermes on it (`hermes -p <PUNTMORE_HERMES_AW26_PROFILE> chat
--oneshot ... --image <photo> -s puntmore-aw26`), waiting for it to end. HubAI ends by
recording the slip (`puntmore aw26 record`) or failing the photo (`puntmore aw26 fail`); the
loop reads which off Render afterwards, and a photo still `reading` (HubAI crashed, hung past
its budget or stopped without doing either) is failed "Qwen did not finish", so it never sits
taken. Hermes's output goes to `<data>/aw26/<id>.txt`; the photo is deleted after the run.

With no hermes binary nothing is taken: a photo is never taken that nobody will read.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable

from . import aw26
from .config import Settings

log = logging.getLogger("puntmore.aw26")

SKILL = "puntmore-aw26"
NOT_FINISHED = "Qwen did not finish"
Run = Callable[[list[str], float], object]


def brief(photo_id: int, image: Path) -> str:
    """What HubAI is told about one photo."""
    return (
        f"AW 26 slip photo #{photo_id} is attached ({image}). Read it by following your {SKILL} "
        "skill.\n"
        "End with exactly one of these, and nothing else after it:\n"
        f"  puntmore aw26 record <file>   (the slip as JSON, with \"photo_id\": {photo_id})\n"
        f"  puntmore aw26 fail {photo_id} --reason \"<why no slip can be made of it>\""
    )


def command(cfg: Settings, photo_id: int, image: Path) -> list[str]:
    """The Hermes command that reads one photo."""
    return [cfg.hermes_bin, "-p", cfg.hermes_aw26_profile, "chat", "--oneshot",
            "-q", brief(photo_id, image), "--image", str(image),
            "-s", SKILL, "--run-budget", str(cfg.aw26_run_budget_s)]


def default_run(cfg: Settings, photo_id: int) -> Run:
    """Run Hermes to the end, its output appended to `<data>/aw26/<id>.txt`. A timeout kills it
    (subprocess.run) and raises TimeoutExpired."""
    def run(cmd: list[str], timeout: float) -> None:
        out_dir = cfg.data_dir / "aw26"
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / f"{int(photo_id)}.txt", "ab") as fh:
            subprocess.run(cmd, timeout=timeout, stdin=subprocess.DEVNULL, stdout=fh,
                           stderr=subprocess.STDOUT, check=False)
    return run


def _hermes(cfg: Settings) -> bool:
    return bool(cfg.hermes_bin) and Path(cfg.hermes_bin).is_file()


def read_once(cfg: Settings, *, opener: aw26.Opener | None = None, run: Run | None = None) -> dict:
    """One pass: the oldest waiting photo taken, read by HubAI, and checked on Render after.
    {"photo": id | None, "outcome": "recorded" | "duplicate" | "failed" | "not finished" | "idle"}.
    Render's errors and network errors are raised; the daemon logs them and carries on."""
    if not _hermes(cfg):
        log.warning("aw26: hermes binary not found at %r; no photo taken", cfg.hermes_bin)
        return {"photo": None, "outcome": "idle"}
    waiting = [p for p in aw26.photos(cfg, opener=opener) if p.get("state") == "waiting"]
    if not waiting:
        return {"photo": None, "outcome": "idle"}
    photo_id = int(waiting[0]["id"])                 # Render lists oldest first
    image = aw26.take(cfg, photo_id, cfg.data_dir / "aw26", opener=opener)
    try:
        log.info("aw26: photo #%s handed to hermes profile %s", photo_id, cfg.hermes_aw26_profile)
        try:
            (run or default_run(cfg, photo_id))(command(cfg, photo_id, image),
                                                 cfg.aw26_run_budget_s + 60)
        except subprocess.TimeoutExpired:
            log.warning("aw26: hermes ran past its budget on photo #%s and was stopped", photo_id)
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("aw26: hermes could not run on photo #%s: %s", photo_id, exc)
    finally:
        image.unlink(missing_ok=True)
    after = {int(p["id"]): p for p in aw26.photos(cfg, opener=opener)}
    state = (after.get(photo_id) or {}).get("state")
    if state is None:                                 # done: Render lists only photos not done
        outcome = "recorded"
    elif state == "duplicate":
        outcome = "duplicate"
    elif state == "reading":
        aw26.fail(cfg, photo_id, NOT_FINISHED, opener=opener)
        outcome = "not finished"
    else:                                             # failed, or waiting again with tries left
        outcome = "failed"
    log.info("aw26: photo #%s %s", photo_id, outcome)
    return {"photo": photo_id, "outcome": outcome}
