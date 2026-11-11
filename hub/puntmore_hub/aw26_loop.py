"""The daemon's AW 26 loop: one waiting slip photo at a time, read by HubAI.

Each pass (`read_once`) lists Render's photo queue, takes the oldest `waiting` photo, downloads
it under `<data>/aw26/` and starts Hermes on it (`hermes -p <PUNTMORE_HERMES_AW26_PROFILE> chat
--oneshot ... --image <photo> -s puntmore-aw26`), waiting for it to end. HubAI ends by
recording the slip (`puntmore aw26 record`) or failing the photo (`puntmore aw26 fail`); the
loop reads which off Render afterwards, and a photo still `reading` (HubAI crashed, hung past
its budget or stopped without doing either) is failed "Qwen did not finish", so it never sits
taken; one whose image could not be downloaded is failed "the Hub could not fetch the photo". When Render cannot be reached to check or
fail a photo the Hub took, the pass says so ("unchecked") and Render's release of a photo taken
15 min ago is the backstop. Hermes runs in its own session, its output to `<data>/aw26/<id>.txt`;
a run past its budget is killed with every tool it started. The photo is deleted after the run.

With no hermes binary nothing is taken: a photo is never taken that nobody will read (said once,
and again only when the binary comes back or goes missing again).

Hermes is started with PUNTMORE_AW26_PHOTO_ID=<id> in its environment: `puntmore aw26` then
refuses an upload and any other photo's slip, and fills in `photo_id` when the slip has none.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import Callable

from . import aw26
from .config import Settings

log = logging.getLogger("puntmore.aw26")

SKILL = "puntmore-aw26"
NOT_FINISHED = "Qwen did not finish"
NOT_FETCHED = "the Hub could not fetch the photo"
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
    """Run Hermes to the end in its own session, its output appended to `<data>/aw26/<id>.txt`,
    with PUNTMORE_AW26_PHOTO_ID=<id> added to its environment. Past `timeout` the whole process
    group is killed (Hermes and every tool it started) and TimeoutExpired raised."""
    def run(cmd: list[str], timeout: float) -> None:
        out_dir = cfg.data_dir / "aw26"
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / f"{int(photo_id)}.txt", "ab") as fh:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh,
                                    stderr=subprocess.STDOUT, start_new_session=True,
                                    env={**os.environ, aw26.PHOTO_ID_ENV: str(int(photo_id))})
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise
    return run


_hermes_missing = False      # the last pass found no hermes binary: said once, until it changes


def _hermes(cfg: Settings) -> bool:
    """The hermes binary is there. Its going missing, and its coming back, are logged once each."""
    global _hermes_missing
    present = bool(cfg.hermes_bin) and Path(cfg.hermes_bin).is_file()
    if not present and not _hermes_missing:
        log.warning("aw26: hermes binary not found at %r; no photo taken until it is", cfg.hermes_bin)
    elif present and _hermes_missing:
        log.info("aw26: hermes binary found again at %r", cfg.hermes_bin)
    _hermes_missing = not present
    return present


def _fail(cfg: Settings, photo_id: int, reason: str, opener: aw26.Opener | None) -> bool:
    """Fail a photo the Hub took, best effort: True when Render failed it. When it cannot,
    the photo stays `reading` until Render releases it 15 min after the take."""
    try:
        aw26.fail(cfg, photo_id, reason, opener=opener)
        return True
    except Exception as exc:  # noqa: BLE001 - Render down, or the photo moved on (409)
        log.error("aw26: could not fail photo #%s (%s): %s; Render releases it 15 min after "
                  "the take", photo_id, reason, exc)
        return False


def read_once(cfg: Settings, *, opener: aw26.Opener | None = None, run: Run | None = None) -> dict:
    """One pass: the oldest waiting photo taken, read by HubAI, and checked on Render after.
    {"photo": id | None, "outcome": ...}, the outcome one of "idle" (nothing taken), "recorded",
    "duplicate", "failed" (HubAI failed it), "not finished" (the Hub failed it after the run),
    "not fetched" (taken, its image not downloaded, failed) or "unchecked" (taken, and Render
    could not be reached to check or fail it: Render's 15-min release is the backstop).
    An error before the take (listing, or the take refused) is raised; the daemon logs it."""
    if not _hermes(cfg):
        return {"photo": None, "outcome": "idle"}
    waiting = [p for p in aw26.photos(cfg, opener=opener) if p.get("state") == "waiting"]
    if not waiting:
        return {"photo": None, "outcome": "idle"}
    photo_id = int(waiting[0]["id"])                 # Render lists oldest first
    aw26.claim(cfg, photo_id, opener=opener)
    # Taken: from here every way out fails the photo on Render, or says it could not.
    try:
        image = aw26.download(cfg, photo_id, cfg.data_dir / "aw26", opener=opener)
    except Exception as exc:  # noqa: BLE001
        log.error("aw26: could not fetch photo #%s: %s", photo_id, exc)
        failed = _fail(cfg, photo_id, NOT_FETCHED, opener)
        return {"photo": photo_id, "outcome": "not fetched" if failed else "unchecked"}
    try:
        log.info("aw26: photo #%s handed to hermes profile %s", photo_id, cfg.hermes_aw26_profile)
        (run or default_run(cfg, photo_id))(command(cfg, photo_id, image), cfg.aw26_run_budget_s + 60)
    except subprocess.TimeoutExpired:
        log.warning("aw26: hermes ran past its budget on photo #%s and was stopped", photo_id)
    except Exception as exc:  # noqa: BLE001 - a crash is checked like any other ending
        log.error("aw26: hermes failed on photo #%s: %s", photo_id, exc)
    finally:
        image.unlink(missing_ok=True)
    try:
        after = {int(p["id"]): p for p in aw26.photos(cfg, opener=opener)}
    except Exception as exc:  # noqa: BLE001
        log.error("aw26: could not check photo #%s after the run: %s; failing it", photo_id, exc)
        after = {photo_id: {"state": "reading"}}      # unknown: a fail is refused unless reading
    state = (after.get(photo_id) or {}).get("state")
    if state is None:                                 # done: Render lists only photos not done
        outcome = "recorded"
    elif state == "duplicate":
        outcome = "duplicate"
    elif state == "reading":
        outcome = "not finished" if _fail(cfg, photo_id, NOT_FINISHED, opener) else "unchecked"
    else:                                             # failed, or waiting again with tries left
        outcome = "failed"
    log.info("aw26: photo #%s %s", photo_id, outcome)
    return {"photo": photo_id, "outcome": outcome}
