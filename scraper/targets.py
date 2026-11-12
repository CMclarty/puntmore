"""The only way targets.toml is changed.

The Console, the Hub CLI and HubAI's scraper-manager all drive the
Scraper through this module (or its CLI, `python -m scraper targets ...`), so
there is one config store and one set of rules. Every rule a target must
satisfy - sites, hosts, the clamps on every timing - stays in
config._target; this module only refuses an edit that those rules would
silently clamp, so a request for "every 2 minutes" is an error, not a
quiet 5.

Every change is read-modify-write under an flock on a sidecar
`targets.toml.lock`, validated as a whole, and written atomically (a temp
file in the same directory, fsynced, renamed over the old one), so the
running Scraper, which re-reads the file when its mtime moves, never sees a
half-written file. The file is rewritten whole: comments in a hand-written
file are lost on the first save, which keeps the original once as
`targets.toml.bak`.
"""
import builtins
import contextlib
import fcntl
import json
import os
import pathlib
import re
import shutil
import tempfile
import time
from dataclasses import asdict, fields

from . import behaviour
from . import config
from . import joblog
from .config import Matches, Target

LOCK_TIMEOUT_S = 10.0

HEADER = """\
# The Scraper's targets. WRITTEN BY scraper/targets.py - do not edit by hand
# while the Scraper runs. Change it with `python -m scraper targets ...`, the
# Console, or HubAI's scraper-manager; each change is validated against
# config._target (sites, hosts, clamps) and saved atomically.
#
# This file is rewritten whole on every change: comments are not kept. The
# hand-written file it replaced, if any, is kept once as targets.toml.bak.
# Field reference: scraper/targets.example.toml.
"""

# Fields a mapping may carry. Anything else is a typo, and is refused.
FIELDS = tuple(f.name for f in fields(Target))
MATCH_FIELDS = tuple(f.name for f in fields(Matches))
# Numbers config._target clamps: a value it would change is refused here.
_NUMERIC = ("every_min", "stale_min", "min_rows", "trust_weight", "near_min", "near_hours", "within_h")
_MATCH_NUMERIC = ("max_tabs", "open_gap_min", "tab_max_hours", "relist_min")
_STRINGS = ("name", "site", "url", "parser", "post", "mode", "wait_for", "window", "until")


class TargetsError(ValueError):
    """A change refused: bad value, unknown or duplicate name, busy lock."""


# --- reading and validating -------------------------------------------------
def load(path) -> list[Target]:
    """Every target in the file, disabled ones included, in file order."""
    try:
        return config.load_targets(pathlib.Path(path), include_disabled=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise TargetsError(f"{path} does not load: {_msg(exc)}") from exc


def _msg(exc) -> str:
    return f"missing field {exc}" if isinstance(exc, KeyError) else str(exc)


def to_mapping(t: Target) -> dict:
    """A Target as the plain dict validate() takes (and --json prints)."""
    d = asdict(t)
    d["days"] = list(t.days)
    if t.matches is not None:
        d["matches"]["market_tabs"] = list(t.matches.market_tabs)
        d["matches"]["listings"] = list(t.matches.listings)
    return d


def validate(mapping: dict) -> Target:
    """A Target from a mapping, by config._target's rules. Refuses unknown
    fields, wrong types, and any number config._target would clamp."""
    m = dict(mapping)
    unknown = sorted(builtins.set(m) - builtins.set(FIELDS))
    if unknown:
        raise TargetsError(f"unknown field(s): {', '.join(unknown)} (have {', '.join(FIELDS)})")
    for key in ("name", "site", "url"):
        if not m.get(key):
            raise TargetsError(f"{key} is required")
    name = m["name"]
    for key in _STRINGS:
        if m.get(key) is not None and not isinstance(m[key], str):
            raise TargetsError(f"target {name}: {key} must be text, not {m[key]!r}")
    days = m.get("days")
    if days is not None and (not isinstance(days, (list, tuple)) or not all(isinstance(x, str) for x in days)):
        raise TargetsError(str(config._days_refused(name, days)))
    if "enabled" in m and not isinstance(m["enabled"], bool):
        raise TargetsError(f"target {name}: enabled must be true or false, not {m['enabled']!r}")
    for key in (k for k in (*_NUMERIC, "priority") if m.get(k) is not None):
        if isinstance(m[key], bool) or not isinstance(m[key], (int, float)):
            raise TargetsError(f"target {name}: {key} must be a number, not {m[key]!r}")
    if m.get("matches") is not None:
        mm = m["matches"]
        if not isinstance(mm, dict):
            raise TargetsError(f"target {name}: matches must be a table")
        bad = sorted(builtins.set(mm) - builtins.set(MATCH_FIELDS))
        if bad:
            raise TargetsError(f"target {name}: unknown matches field(s): {', '.join(bad)}")
        tabs = mm.get("market_tabs", [])
        if not isinstance(tabs, (list, tuple)) or not all(isinstance(x, str) for x in tabs):
            raise TargetsError(f"target {name}: matches.market_tabs must be a list of tab names")
        try:
            re.compile(mm.get("link_re", Matches.link_re))
        except (re.error, TypeError) as exc:
            raise TargetsError(f"target {name}: matches.link_re is not a valid pattern: {exc}") from exc
    # None means "not set": config._target reads a missing key as its default.
    raw = {k: v for k, v in m.items() if v is not None}
    try:
        t = config._target(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise TargetsError(_msg(exc)) from exc
    _refuse_clamped(name, raw, t)
    return t


def _refuse_clamped(name, raw, t):
    """config._target holds the floors and ceilings; a value it moved is one
    the Scraper would not run as asked, so the edit is refused instead."""
    def check(label, asked, got):
        if float(asked) != float(got):
            raise TargetsError(f"target {name}: {label} = {asked} is outside what the Scraper "
                               f"allows (it would run as {got}; the limits are in config._target)")
    for key in _NUMERIC:
        if key in raw:
            check(key, raw[key], getattr(t, key))
    if "matches" in raw and t.matches is not None:
        for key in _MATCH_NUMERIC:
            if key in raw["matches"]:
                check(f"matches.{key}", raw["matches"][key], getattr(t.matches, key))


# --- writing -----------------------------------------------------------------
def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)     # a JSON string is a TOML basic string
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {v!r} to TOML")


_DEFAULTS = {f.name: f.default for f in fields(Target)}


def render(targets: list[Target]) -> str:
    """The whole file. name, site, url, mode and enabled are always written;
    other fields only when set and not the default, so a code default still
    applies to a target that never chose a value. [target.matches] is
    written in full."""
    out = [HEADER.rstrip("\n")]
    for t in targets:
        out.append("\n[[target]]")
        for f in FIELDS:
            v = getattr(t, f)
            if f == "matches" or v is None:
                continue
            if f not in ("name", "site", "url", "mode", "enabled") and v == _DEFAULTS[f]:
                continue
            out.append(f"{f} = {_toml_value(v)}")
        if t.matches is not None:
            out.append("  [target.matches]")
            for f in MATCH_FIELDS:
                out.append(f"  {f} = {_toml_value(getattr(t.matches, f))}")
    return "\n".join(out) + "\n"


def save(path, targets: list[Target]) -> None:
    """Write the whole file atomically: temp file in the same directory,
    fsync, rename. A failure leaves the old file as it was."""
    path = pathlib.Path(path)
    names = [t.name for t in targets]
    if len(names) != len(builtins.set(names)):
        raise TargetsError("target names must be unique")
    text = render(targets)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o644
    if path.exists():
        mode = path.stat().st_mode & 0o777
        bak = path.with_name(path.name + ".bak")
        if not bak.exists() and not path.read_text().startswith(HEADER):
            shutil.copy2(path, bak)           # the hand-written original, once
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)                  # mkstemp is 0600; the Scraper's container must read it
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


@contextlib.contextmanager
def locked(path, timeout: float | None = None):
    """Hold an exclusive flock on <path>.lock for one read-modify-write. The
    Console, the CLI and HubAI cannot interleave; a holder that does not let
    go within `timeout` seconds is an error, not a hang."""
    timeout = LOCK_TIMEOUT_S if timeout is None else timeout
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+") as lf:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TargetsError(f"{path} is being changed by something else "
                                       f"(lock held for over {timeout:g} s); try again") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


# --- operations --------------------------------------------------------------
def _find(targets, name) -> int:
    for i, t in enumerate(targets):
        if t.name == name:
            return i
    raise TargetsError(f"no target named {name!r} (have {', '.join(t.name for t in targets) or 'none'})")


def get(path, name) -> Target:
    targets = load(path)
    return targets[_find(targets, name)]


# Names whose log file is not a job's (runner.BEHAVIOUR_LOG).
RESERVED_NAMES = ("behaviour",)


def add(path, mapping: dict, timeout=None) -> Target:
    """A new target, appended. Refuses a name already in the file (disabled
    targets reserve their names too)."""
    t = validate(mapping)
    if t.name in RESERVED_NAMES:
        raise TargetsError(f"a target cannot be named {t.name!r}: data/logs/{t.name}.log is the "
                           "behaviour change log")
    with locked(path, timeout):
        targets = load(path)
        if any(x.name == t.name for x in targets):
            raise TargetsError(f"a target named {t.name!r} already exists")
        save(path, [*targets, t])
    return t


def set(path, name, /, timeout=None, **changes) -> Target:   # noqa: A001 - the API's name
    """Change fields of one target. The whole target is validated after the
    change, so nothing is written if any of it is refused. A value of None
    clears an optional field (back to its default). `listings` changes a
    matches job's further listing pages and keeps the rest of its matches
    table. The name cannot change: remove and add instead."""
    if "name" in changes and changes["name"] != name:
        raise TargetsError("a target cannot be renamed: remove it and add it again")
    with locked(path, timeout):
        targets = load(path)
        i = _find(targets, name)
        mapping = to_mapping(targets[i])
        if "listings" in changes:
            # A matches job's further listing pages, changed on their own: the
            # rest of its [target.matches] table is kept (`matches=` replaces it).
            changes = dict(changes)
            listings = changes.pop("listings")
            if mapping.get("matches") is None or "matches" in changes:
                raise TargetsError(f"target {name}: listings is a [target.matches] field: this job "
                                   "has no match listing" if mapping.get("matches") is None else
                                   f"target {name}: listings and matches cannot both be set")
            mapping["matches"] = {**mapping["matches"], "listings": list(listings or [])}
        mapping.update(changes)
        if "mode" not in changes and "site" in changes:
            mapping["mode"] = None              # let the new site choose its default mode
        t = validate(mapping)
        targets[i] = t
        save(path, targets)
    return t


def enable(path, name, timeout=None) -> Target:
    return set(path, name, timeout=timeout, enabled=True)


def disable(path, name, timeout=None) -> Target:
    return set(path, name, timeout=timeout, enabled=False)


def remove(path, name, timeout=None, data_dir=None) -> list[Target]:
    """Drop one target, and its log (joblog) under data_dir, which defaults to
    the targets file's directory; returns what is left."""
    with locked(path, timeout):
        targets = load(path)
        del targets[_find(targets, name)]
        save(path, targets)
    joblog.remove(pathlib.Path(path).parent if data_dir is None else data_dir, name)
    return targets


# --- what the console renders -------------------------------------------------
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def schedule_words(d: dict) -> str:
    """A target's schedule in words, from its mapping: "every 15 min · Fri, Sat
    · 07:00–01:00 · until 30 Nov 2026". Days in week order whatever order
    they were given in. A fetch job with no every_min runs every
    fetch.default_min (behaviour.py); a kept-open page with none reloads when it goes quiet
    (a mapping with no mode is the Target default, a kept-open page)."""
    if d["every_min"]:
        every = f"every {d['every_min']:g} min"
    elif d.get("mode", Target.mode) == "fetch":
        every = f"every {behaviour.get('fetch.default_min'):g} min"
    else:
        every = f"when quiet {d['stale_min']:g} min"
    days = ", ".join(x.title() for x in config.WEEKDAYS if x in (d["days"] or ())) or "every day"
    window = d["window"].replace("-", "–") if d["window"] else "all day"
    if d["until"]:
        y, mo, day = (int(x) for x in d["until"].split("-"))
        until = f"until {day} {_MONTHS[mo - 1]} {y}"
    else:
        until = "forever"
    return " · ".join((every, days, window, until))


STATUS_KEYS = ("state", "detail", "last_load", "last_parse", "rows_posted")


def summary(path, status_json_path=None) -> list[dict]:
    """One dict per target in the file (file order): every field (to_mapping),
    "schedule" (schedule_words), and "status": the running Scraper's last
    report on it from status.json (state, detail, last_load, last_parse,
    rows_posted, reported_at), or None when there is no status.json or it does not name the target. A disabled
    target is not loaded, so it has no status unless it was running when it
    was disabled (then "retired" until the Scraper restarts)."""
    path = pathlib.Path(path)
    status_json_path = pathlib.Path(status_json_path) if status_json_path else path.parent / "status.json"
    reported: dict = {}
    at = None
    try:
        body = json.loads(status_json_path.read_text())
        at = body.get("at")
        reported = {t["name"]: t for t in body.get("targets", []) if "name" in t}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    out = []
    for t in load(path):
        d = to_mapping(t)
        d["schedule"] = schedule_words(d)
        r = reported.get(t.name)
        d["status"] = None if r is None else {
            **{k: r.get(k) for k in STATUS_KEYS}, "reported_at": at}
        out.append(d)
    return out


def default_path() -> pathlib.Path:
    """What the Scraper reads: SCRAPER_TARGETS, else SCRAPER_DATA_DIR/targets.toml."""
    data = pathlib.Path(os.environ.get("SCRAPER_DATA_DIR", config.HERE / "data"))
    return pathlib.Path(os.environ.get("SCRAPER_TARGETS") or data / "targets.toml")


__all__ = ["load", "validate", "save", "add", "set", "enable", "disable", "remove",
           "get", "summary", "render", "to_mapping", "schedule_words", "locked", "TargetsError"]
