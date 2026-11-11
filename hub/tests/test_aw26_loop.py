"""The daemon's AW 26 loop (puntmore_hub/aw26_loop.py): one waiting slip photo at a time, taken
from Render and handed to HubAI with the photo attached; a run that ends without recording or
failing the photo fails it "Qwen did not finish"."""
import json
import subprocess
import threading
import time
import urllib.error

import pytest

from puntmore_hub import aw26_loop


class _Resp:
    def __init__(self, status, body, ctype="application/json"):
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body


def _photo(pid, state="waiting", tries=0):
    return {"id": pid, "state": state, "tries": tries, "reason": None}


class _Render:
    """A fake Render: `GET /photos` answers each listing in turn; take, image and fail are kept.
    A listing that is an exception is raised; `broken` maps a path ending ("/image", "/fail") to an
    exception raised or a response answered there."""

    def __init__(self, *listings, image=b"\xff\xd8jpeg", broken=None):
        self.listings, self.image, self.calls = list(listings), image, []
        self.broken = broken or {}

    def __call__(self, req, timeout):
        path = req.full_url.split("puntmore.example", 1)[1]
        self.calls.append((req.get_method(), path, json.loads(req.data) if req.data else None))
        for end, answer in self.broken.items():
            if path.endswith(end):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        if path == "/api/aw26/photos":
            listing = self.listings.pop(0)
            if isinstance(listing, Exception):
                raise listing
            return _Resp(200, listing)
        if path.endswith("/take"):
            return _Resp(200, _photo(int(path.split("/")[-2]), "reading"))
        if path.endswith("/image"):
            return _Resp(200, self.image, "image/jpeg")
        if path.endswith("/fail"):
            return _Resp(200, _photo(int(path.split("/")[-2]), "waiting", 1))
        raise AssertionError(f"unexpected {req.get_method()} {path}")

    def posted(self, suffix):
        return [(p, body) for m, p, body in self.calls if m == "POST" and p.endswith(suffix)]


@pytest.fixture
def cfg(cfg, tmp_path):
    hermes = tmp_path / "bin" / "hermes"
    hermes.parent.mkdir()
    hermes.write_text("#!/bin/sh\n")
    return cfg.with_overrides(render_http="https://puntmore.example", render_api_key="k",
                              hermes_bin=str(hermes))


class _Run:
    """Stands in for launching hermes: keeps the command, and checks the photo is on disk."""

    def __init__(self, raises=None):
        self.cmds, self.raises = [], raises

    def __call__(self, cmd, timeout):
        image = cmd[cmd.index("--image") + 1]
        with open(image, "rb") as fh:
            self.image = fh.read()
        self.cmds.append((cmd, timeout))
        if self.raises:
            raise self.raises


def test_only_the_oldest_waiting_photo_is_taken_and_attached(cfg):
    render = _Render([_photo(3, "failed", 3), _photo(5), _photo(9)], [_photo(9)])
    run = _Run()
    out = aw26_loop.read_once(cfg, opener=render, run=run)
    assert out == {"photo": 5, "outcome": "recorded"}
    assert render.posted("/take") == [("/api/aw26/photos/5/take", None)]
    [(cmd, timeout)] = run.cmds
    image = cmd[cmd.index("--image") + 1]
    assert image == str(cfg.data_dir / "aw26" / "5.jpg") and run.image == b"\xff\xd8jpeg"
    assert timeout == cfg.aw26_run_budget_s + 60
    assert not (cfg.data_dir / "aw26" / "5.jpg").exists()      # deleted after the run
    assert render.posted("/fail") == []


def test_the_command_starts_the_aw26_profile_with_the_skill_and_the_budget(cfg, tmp_path):
    image = tmp_path / "5.jpg"
    cmd = aw26_loop.command(cfg, 5, image)
    assert cmd == [cfg.hermes_bin, "-p", "betting-client-manager", "chat", "--oneshot",
                   "-q", aw26_loop.brief(5, image), "--image", str(image),
                   "-s", "puntmore-aw26", "--run-budget", "600"]
    text = aw26_loop.brief(5, image)
    assert "puntmore-aw26" in text and str(image) in text and "5" in text
    assert "puntmore aw26 record" in text and "photo_id" in text
    assert "puntmore aw26 fail 5 --reason" in text


def test_a_photo_still_reading_after_the_run_is_failed(cfg):
    render = _Render([_photo(5)], [_photo(5, "reading")])
    out = aw26_loop.read_once(cfg, opener=render, run=_Run())
    assert out == {"photo": 5, "outcome": "not finished"}
    assert render.posted("/fail") == [("/api/aw26/photos/5/fail", {"reason": "Qwen did not finish"})]


def test_a_run_that_times_out_is_failed(cfg):
    render = _Render([_photo(5)], [_photo(5, "reading")])
    run = _Run(raises=subprocess.TimeoutExpired(["hermes"], 960))
    out = aw26_loop.read_once(cfg, opener=render, run=run)
    assert out == {"photo": 5, "outcome": "not finished"}
    assert render.posted("/fail") == [("/api/aw26/photos/5/fail", {"reason": "Qwen did not finish"})]
    assert not (cfg.data_dir / "aw26" / "5.jpg").exists()


@pytest.mark.parametrize("after, outcome", [
    ([], "recorded"),
    ([_photo(5, "duplicate")], "duplicate"),
    ([_photo(5, "waiting", 1)], "failed"),          # HubAI failed it; Render put it back
    ([_photo(5, "failed", 3)], "failed"),
])
def test_what_hubai_did_is_read_back_from_render_and_nothing_more_is_posted(cfg, after, outcome):
    render = _Render([_photo(5)], after)
    assert aw26_loop.read_once(cfg, opener=render, run=_Run()) == {"photo": 5, "outcome": outcome}
    assert render.posted("/fail") == []


def test_nothing_waiting_is_idle(cfg):
    render = _Render([_photo(3, "failed", 3), _photo(4, "reading")])
    never = lambda cmd, timeout: pytest.fail("ran")
    assert aw26_loop.read_once(cfg, opener=render, run=never) == {"photo": None, "outcome": "idle"}
    assert render.posted("/take") == []


def test_a_missing_hermes_takes_nothing(cfg, caplog):
    render = _Render([_photo(5)])
    never = lambda cmd, timeout: pytest.fail("ran")
    gone = cfg.with_overrides(hermes_bin=str(cfg.data_dir / "no-hermes"))
    assert aw26_loop.read_once(gone, opener=render, run=never) == {"photo": None, "outcome": "idle"}
    assert render.posted("/take") == []
    assert "hermes" in caplog.text


def test_the_default_run_writes_hermes_output_under_data_aw26(cfg):
    # a shell stands in for hermes: hermes itself is never launched by a test
    aw26_loop.default_run(cfg, 5)(["sh", "-c", "echo read it; echo oops >&2"], 10)
    assert (cfg.data_dir / "aw26" / "5.txt").read_bytes() == b"read it\noops\n"


def _gone(pid):
    """The process has exited (a zombie waiting to be reaped counts)."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


def test_a_timeout_kills_everything_hermes_started(cfg):
    run = aw26_loop.default_run(cfg, 5)
    with pytest.raises(subprocess.TimeoutExpired):
        run(["sh", "-c", "sleep 30 & echo $! ; wait"], 0.5)
    child = int((cfg.data_dir / "aw26" / "5.txt").read_text().split()[0])
    deadline = time.monotonic() + 5
    while not _gone(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _gone(child), "a process hermes started outlived the timeout"


def test_the_budget_ends_before_render_releases_the_photo(cfg):
    assert cfg.aw26_run_budget_s == 600
    assert cfg.aw26_run_budget_s + 60 < 15 * 60      # render/api/app/aw26/photos.py STALE_AFTER


@pytest.mark.parametrize("image", [_Resp(500, {"detail": "boom"}), urllib.error.URLError("reset")])
def test_a_photo_taken_but_not_fetched_is_failed(cfg, image, caplog):
    render = _Render([_photo(5)], broken={"/image": image})
    never = lambda cmd, timeout: pytest.fail("ran")
    out = aw26_loop.read_once(cfg, opener=render, run=never)
    assert out == {"photo": 5, "outcome": "not fetched"}
    assert render.posted("/take") == [("/api/aw26/photos/5/take", None)]
    assert render.posted("/fail") == [("/api/aw26/photos/5/fail",
                                       {"reason": "the Hub could not fetch the photo"})]
    assert "could not fetch" in caplog.text


def test_a_run_that_crashes_is_still_checked_and_failed(cfg):
    render = _Render([_photo(5)], [_photo(5, "reading")])
    out = aw26_loop.read_once(cfg, opener=render, run=_Run(raises=ValueError("boom")))
    assert out == {"photo": 5, "outcome": "not finished"}
    assert render.posted("/fail") == [("/api/aw26/photos/5/fail", {"reason": "Qwen did not finish"})]
    assert not (cfg.data_dir / "aw26" / "5.jpg").exists()


def test_a_failed_read_back_still_fails_the_photo(cfg):
    render = _Render([_photo(5)], urllib.error.URLError("reset"))
    out = aw26_loop.read_once(cfg, opener=render, run=_Run())
    assert out == {"photo": 5, "outcome": "not finished"}
    assert render.posted("/fail") == [("/api/aw26/photos/5/fail", {"reason": "Qwen did not finish"})]


@pytest.mark.parametrize("after", [urllib.error.URLError("down"), [_photo(5, "reading")]])
def test_render_unreachable_after_the_run_is_unchecked(cfg, after, caplog):
    render = _Render([_photo(5)], after, broken={"/fail": OSError("down")})
    out = aw26_loop.read_once(cfg, opener=render, run=_Run())
    assert out == {"photo": 5, "outcome": "unchecked"}
    assert len(render.posted("/fail")) == 1
    assert "15 min" in caplog.text


def test_a_bad_pass_never_stops_the_loop(cfg, monkeypatch):
    from puntmore_hub import daemon
    stop, passes = threading.Event(), []

    def read_once(c):
        passes.append(c)
        if len(passes) == 1:
            raise OSError("Render down")
        stop.set()
        return {"photo": None, "outcome": "idle"}
    monkeypatch.setattr(aw26_loop, "read_once", read_once)
    daemon._aw26_loop(cfg, stop, lambda s: None)
    assert len(passes) == 2
    status = daemon.last_status(cfg)["aw26"]
    assert status["counts"] == {"photo": None, "outcome": "idle"} and status["error"] is None
