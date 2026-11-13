"""How the Scraper behaves: every limit, timing and click, in one place.

Read this file when the Scraper has done something it should not have. Each
setting in SETTINGS below is one thing it does, with its default, its unit,
the bounds a changed value must stay inside, and a line saying what it is.
The code that acts reads `behaviour.get(key)` at the moment it acts (a load,
a click, a tick), so nothing else in the Scraper holds a number of its own
for any of these; the one exception is the guard (guard.py), which takes a
copy of the sites' limits, the quiet hours and the cool-off when it is built.

Changing one. `python -m scraper behaviour set <key> <value>` (and `reset
<key>`, `reset --all`; `python -m scraper behaviour` lists them all, the
changed ones marked) writes <data>/behaviour.toml: flat `"key" = value` lines,
only the settings that differ from their default, each under a comment saying
its default. It is written only through set_() and reset() here: a file lock,
the value checked against its kind and bounds, an atomic save. A value outside
its bounds is refused with a message naming the setting. The service reads
the file when it starts (load()) and again within 60 s of any change to it,
without a restart (runner.py; the guard takes the new limits, and each value
that moved is logged to data/logs/behaviour.log); a file that is deleted is
every setting back to its default. A file that will not load keeps the
values already in use, and a key it does not know, or a value that is
refused, keeps that one setting's value - each is reported once per edit,
none stops the Scraper. A value already drawn from a range (a page's next
reload, the next round of clicks) keeps until its next draw.

Kinds:
  int, float  a number (int: a whole one).
  bool        true or false.
  range       a low-high pair, "20-45" or [20, 45], low <= high, both inside
              the bounds. Most are drawn from afresh each time (a random
              number between the two); `says` names the ones that are not.
  hours       a start-end pair of local hours, "1-7": from the start hour up
              to (not including) the end hour, past midnight when the start
              is later ("23-6"); equal hours are no hours at all.

Bounds are for sanity, not policy: wide enough that any setting can be
loosened or tightened, tight enough that a typo (a negative, a gap of a week)
is refused. 0 loads an hour or a day for a site means no loads for it.

Not here, on purpose: what a block means (stop, cool off, wait for `resume`;
sites.py), never answering a challenge, the hosts a site may load (config.py),
which pages are loaded (targets.toml), and anything about what the browser
shows a site - there are no user-agent, navigator or fingerprint settings and
none are added.
"""
import contextlib
import fcntl
import json
import math
import os
import pathlib
import re
import tempfile
import time
import tomllib
from dataclasses import dataclass

FILE = "behaviour.toml"
LOCK_TIMEOUT_S = 10.0
KINDS = ("int", "float", "bool", "range", "hours")


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    kind: str            # one of KINDS
    default: object      # int, float, bool, or a (low, high) / (start, end) tuple
    lo: float | None     # bounds for the value (each end of a pair); None for a bool
    hi: float | None
    unit: str
    says: str            # what it is, in a line


def _group(name, *rows) -> tuple[Setting, ...]:
    return tuple(Setting(key, name, kind, default, lo, hi, unit, says)
                 for key, kind, default, lo, hi, unit, says in rows)


def _site(site, name, per_hour, per_day, load_gap_s, quiet_hours):
    return (
        (f"{site}.per_hour", "int", per_hour, 0, 600, "loads",
         f"{name}: page loads in any hour, across all its jobs (0: none)"),
        (f"{site}.per_day", "int", per_day, 0, 14400, "loads",
         f"{name}: page loads in any 24 hours, across all its jobs (0: none)"),
        (f"{site}.load_gap_s", "float", load_gap_s, 0, 3600, "s",
         f"{name}: least gap between two of its page loads (times load.site_gap_x)"),
        (f"{site}.quiet_hours", "bool", quiet_hours, None, None, "",
         f"{name}: loads nothing and keeps its pages closed during quiet.hours"),
    )


SETTINGS: tuple[Setting, ...] = (
    *_group(
        "Games",
        ("games.within_h", "int", 48, 0, 720, "h",
         "Only games kicking off within this many hours are read (0: no limit)"),
        ("games.look_again_s", "int", 86400, 600, 604800, "s",
         "With no game that near, a job rests at most this long before looking at the fixtures again"),
    ),
    *_group(
        "Limits, per site",
        *_site("paddypower", "Paddy Power", 15, 120, 90.0, True),
        *_site("bookiebashing", "Bookie Bashing", 8, 120, 60.0, False),
        *_site("teamcasino", "Team Casino", 6, 30, 60.0, False),
        *_site("generic", "Any other site (Finder's sources)", 60, 400, 20.0, False),
    ),
    *_group(
        "Limits, all sites",
        ("quiet.hours", "hours", (1, 7), 0, 24, "h",
         "Quiet hours, local time: a site with quiet_hours on loads nothing and its pages close"),
        ("cooloff.hours", "float", 24.0, 0, 168, "h",
         "After a block on Paddy Power, Team Casino or a generic site, nothing loads for "
         "this long, and then only once `resume` has been run"),
        ("login.max_signins", "int", 3, 0, 20, "sign-ins",
         "Sign-ins sent to one site within login.window_h (each site counted on its own); "
         "the next logout waits for a person (0: never signs in)"),
        ("login.window_h", "int", 6, 1, 168, "h",
         "The window login.max_signins is counted over"),
        ("backoff.min", "range", (15, 120), 1, 1440, "min",
         "After a block on Bookie Bashing, the first wait before it tries again; it "
         "doubles each time up to the second (not drawn)"),
        ("backoff.jitter_x", "range", (0.9, 1.2), 0.1, 5, "x",
         "Each back-off wait is multiplied by a number drawn from this range"),
    ),
    *_group(
        "Spacing of page loads",
        ("load.any_gap_s", "range", (20, 45), 0, 3600, "s",
         "Between any two page loads, whatever the site, a gap drawn from this range"),
        ("load.site_gap_x", "range", (1.0, 1.8), 0, 10, "x",
         "Between two loads of one site, its load_gap_s times a number drawn from this range"),
        ("reload.jitter_x", "range", (0.75, 1.35), 0.1, 5, "x",
         "A job's timed reload (every_min), and a fetch job's next run, is its interval "
         "times a number drawn from this range, once per load"),
        ("relist.jitter_x", "range", (0.9, 1.3), 0.1, 5, "x",
         "A listing is loaded again after its relist_min times a number drawn from this range"),
        ("open_gap.jitter_x", "range", (1.0, 1.6), 0.1, 5, "x",
         "Between opening two match pages of a listing, its open_gap_min times a number "
         "drawn from this range"),
        ("recycle.hours", "float", 0.5, 0.1, 24, "h",
         "A kept-open page is swapped for a fresh one (a new load) about this often"),
        ("recycle.jitter_x", "range", (0.85, 1.2), 0.1, 5, "x",
         "That time is recycle.hours times a number drawn from this range, once per page"),
        ("fetch.default_min", "int", 60, 1, 1440, "min",
         "A fetch job with no every_min runs this often"),
        ("fetch.retry_s", "int", 300, 30, 3600, "s",
         "A fetch job whose load failed tries again after this"),
        ("restart.hours", "range", (1.5, 2.0), 0.25, 24, "h",
         "The service restarts itself (a fresh Chrome) after a time drawn from this range"),
    ),
    *_group(
        "Arriving on a page",
        ("arrive.wait_ms", "range", (2500, 5000), 0, 5000, "ms",
         "After a page loads, a wait for it to draw"),
        ("arrive.passes", "range", (1, 3), 0, 6, "passes",
         "Then it looks down the page this many times"),
        ("arrive.move_steps", "range", (8, 25), 1, 200, "steps",
         "Each look moves the pointer in this many steps"),
        ("arrive.scroll_px", "range", (250, 700), 0, 5000, "px",
         "Each look scrolls down this far"),
        ("arrive.pause_ms", "range", (700, 2200), 0, 5000, "ms",
         "Each look pauses this long"),
        ("arrive.back_up_px", "range", (800, 2000), 0, 20000, "px",
         "Then it scrolls back up this far"),
        ("arrive.after_consent_ms", "range", (600, 1500), 0, 5000, "ms",
         "After picking the minimum-cookies option on a consent banner, a pause"),
    ),
    *_group(
        "Clicks",
        ("click.every_s", "int", 600, 60, 21600, "s",
         "A kept page's market tabs are clicked again this often (clicks, not loads)"),
        ("click.every_jitter_x", "range", (0.8, 1.4), 0.1, 5, "x",
         "That time is click.every_s times a number drawn from this range, once per round"),
        ("click.before_move_ms", "range", (300, 900), 0, 5000, "ms",
         "Once the element is scrolled into view, a pause before the pointer moves"),
        ("click.move_steps", "range", (12, 30), 1, 200, "steps",
         "The pointer moves onto the element in this many steps"),
        ("click.before_press_ms", "range", (250, 900), 0, 5000, "ms",
         "A pause on the element before pressing"),
        ("click.press_ms", "range", (50, 140), 0, 2000, "ms",
         "How long the button is held down"),
        ("click.aim_x", "range", (0.3, 0.7), 0, 1, "of width",
         "Where across the element the click lands (0 left edge, 1 right)"),
        ("click.aim_y", "range", (0.35, 0.65), 0, 1, "of height",
         "Where down the element the click lands (0 top edge, 1 bottom)"),
        ("click.after_feed_ms", "range", (1500, 3000), 0, 5000, "ms",
         "Once a clicked tab's markets have arrived, a pause for their prices"),
    ),
    *_group(
        "Reading down a page",
        ("read.passes", "range", (2, 4), 0, 6, "passes",
         "After a market tab is clicked, it looks down what it showed this many times"),
        ("read.move_steps", "range", (8, 25), 1, 200, "steps",
         "Each look moves the pointer in this many steps"),
        ("read.scroll_px", "range", (300, 700), 0, 5000, "px",
         "Each look scrolls down this far"),
        ("read.pause_ms", "range", (900, 2500), 0, 5000, "ms",
         "Each look pauses this long"),
        ("read.back_up_px", "range", (2500, 4000), 0, 20000, "px",
         "Then it scrolls back up this far"),
        ("read.back_up_pause_ms", "range", (500, 1200), 0, 5000, "ms",
         "And pauses this long at the top"),
    ),
    *_group(
        "Bookie Bashing bet builder",
        ("bb.call_budget_s", "float", 40.0, 5, 300, "s",
         "Work per parse of the builder (the runner's tick waits on it)"),
        ("bb.update_every_s", "int", 1800, 60, 86400, "s",
         "The builder's data (Update Data, about 60 MB) is pulled at most this often"),
        ("bb.update_tries", "int", 3, 1, 10, "tries",
         "Update Data clicks in a row before the builder counts as not ready"),
        ("bb.xsaves_retries", "int", 3, 0, 20, "pulls",
         "Extra data pulls in a row when BB xSaves is not listed"),
        ("bb.settle_s", "float", 0.25, 0.05, 1.0, "s",
         "The Total Odds field counts as settled when it reads the same twice this far apart"),
    ),
    # When the Scraper says it is not working (standing.py; the stall: __main__.watchdog). One
    # alert when a problem has stood this long, one when it clears.
    *_group(
        "Alerts",
        ("alerts.stuck_s", "int", 180, 0, 590, "s",
         "Alert when the loop has made no progress for this long (0: off); the watchdog "
         "restarts it at 600 s"),
        ("alerts.failing_min", "int", 15, 0, 1440, "min",
         "Alert when a job's parser has failed the same way for this long (0: off)"),
        ("alerts.no_rows_min", "int", 60, 0, 1440, "min",
         "Alert when a running job with pages open has handed over no rows for this long (0: off)"),
    ),
)

_BY_KEY = {s.key: s for s in SETTINGS}
_live: dict[str, object] = {s.key: s.default for s in SETTINGS}


class BehaviourError(ValueError):
    """A value or change refused: unknown setting, wrong kind, out of bounds,
    a file that does not load, a busy lock. The message names the setting."""


# --- reading ---------------------------------------------------------------------
def get(key: str):
    """The value in use now."""
    try:
        return _live[key]
    except KeyError:
        raise KeyError(f"no behaviour setting {key!r}") from None


def setting(key: str) -> Setting:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise BehaviourError(f"{key}: no such setting (python -m scraper behaviour lists them)") from None


def changed() -> dict:
    """The settings in use that differ from their default."""
    return {s.key: _live[s.key] for s in SETTINGS if _live[s.key] != s.default}


def show(value) -> str:
    """A value as a person writes it: 15, 0.5, true, 20-45."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (tuple, list)):
        return "-".join(f"{x:g}" for x in value)
    return f"{value:g}"


def _plain(value):
    return list(value) if isinstance(value, tuple) else value


def listing() -> list[dict]:
    """Every setting with the value in use, in catalogue order, JSON-ready."""
    return [{"key": s.key, "group": s.group, "kind": s.kind, "value": _plain(_live[s.key]),
             "default": _plain(s.default), "lo": s.lo, "hi": s.hi, "unit": s.unit,
             "says": s.says, "changed": _live[s.key] != s.default} for s in SETTINGS]


# --- checking a value ------------------------------------------------------------
_PAIR = re.compile(r"^\s*(\d*\.?\d+)\s*[-–,]\s*(\d*\.?\d+)\s*$")
_TRUE, _FALSE = ("true", "yes", "on"), ("false", "no", "off")


def parse(key: str, value):
    """`value` (a value, or text from the command line or the Console) as
    the setting's kind, checked against its bounds; BehaviourError if not."""
    s = setting(key)
    if s.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in _TRUE + _FALSE:
            return value.strip().lower() in _TRUE
        raise BehaviourError(f"{key}: must be true or false, not {value!r}")
    if s.kind in ("int", "float"):
        return _bounded(s, _number(s, value, whole=s.kind == "int"))
    pair = _pair(s, value)
    whole = s.kind == "hours" or all(isinstance(x, int) for x in s.default)
    a, b = (_bounded(s, _number(s, x, whole)) for x in pair)
    if s.kind == "range" and a > b:
        raise BehaviourError(f"{key}: the low end ({a:g}) is above the high end ({b:g})")
    return (a, b)


def _number(s: Setting, value, whole: bool):
    n = value
    if isinstance(value, str):
        try:
            n = float(value.strip())
        except ValueError:
            n = None
    said = value if len(repr(value)) <= 40 else repr(value)[:37] + "..."
    try:
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n):
            raise BehaviourError(f"{s.key}: must be a {'whole ' if whole else ''}number, not {said!r}")
        if whole:
            if not float(n).is_integer():
                raise BehaviourError(f"{s.key}: must be a whole number, not {said!r}")
            return int(n)
        return float(n)
    except OverflowError:                     # an int too big to be a float
        raise BehaviourError(f"{s.key}: {said!r} is far too large") from None


def _bounded(s: Setting, n):
    if not s.lo <= n <= s.hi:
        raise BehaviourError(f"{s.key}: {n:g} is not between {s.lo:g} and {s.hi:g} {s.unit}")
    return n


def _pair(s: Setting, value) -> tuple:
    example = '"1-7"' if s.kind == "hours" else f'"{show(s.default)}"'
    said = value if len(repr(value)) <= 40 else repr(value)[:37] + "..."
    refused = BehaviourError(f"{s.key}: give a pair like {example}, not {said!r}")
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except (ValueError, RecursionError):
                raise refused from None
        else:
            m = _PAIR.match(text)
            if not m:
                raise refused
            return m.group(1), m.group(2)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise refused
    return tuple(value)


# --- the overrides file ----------------------------------------------------------
def path(data_dir) -> pathlib.Path:
    return pathlib.Path(data_dir) / FILE


def _flat(table: dict, prefix="") -> dict:
    """A hand-written `[paddypower] per_hour = 9` reads as "paddypower.per_hour"."""
    out = {}
    for k, v in table.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def _read(p: pathlib.Path) -> dict:
    """The file's key -> raw value; {} when there is none."""
    try:
        with open(p, "rb") as f:
            return _flat(tomllib.load(f))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise BehaviourError(f"{p} does not load: {exc}") from None


def load(data_dir) -> list[str]:
    """Make the file's values the ones in use: a setting it does not name is
    back to its default. Returns what was wrong, never raises: a file that
    does not load keeps every value in use; an unknown key, or a refused
    value, keeps that one."""
    p = path(data_dir)
    try:
        raw = _read(p)
    except BehaviourError as exc:
        return [f"{exc} - keeping the values in use"]
    except Exception as exc:                  # never stop the Scraper over this file
        return [f"{p} does not load: {exc.__class__.__name__}: {exc} - keeping the values in use"]
    new = {s.key: s.default for s in SETTINGS}
    problems = []
    for key, value in raw.items():
        if key not in _BY_KEY:
            problems.append(f"{p}: {key}: no such setting - ignored")
            continue
        try:
            new[key] = parse(key, value)
        except BehaviourError as exc:
            new[key] = _live[key]
            problems.append(f"{p}: {exc} - keeping {show(_live[key])}")
        except Exception as exc:              # never stop the Scraper over one line of this file
            new[key] = _live[key]
            problems.append(f"{p}: {key}: {exc.__class__.__name__} - keeping {show(_live[key])}")
    _live.update(new)
    return problems


def set_(data_dir, key: str, value, timeout: float | None = None):
    """Write one setting to the file (removed from it when it is the
    default). Returns the value as checked. The file only: the running
    service, and this process, take it up on their next load()."""
    v = parse(key, value)
    p = path(data_dir)
    with _locked(p, timeout):
        values = _read(p)
        values.pop(key, None)
        if v != setting(key).default:
            values[key] = v
        try:
            _save(p, values)
        except TypeError as exc:              # a hand-written value render() cannot write back
            raise BehaviourError(f"{key}: {p} holds something that cannot be written back ({exc}); "
                                 "`python -m scraper behaviour reset --all` mends it") from None
    return v


def reset(data_dir, key: str | None, timeout: float | None = None) -> list[str]:
    """Put one setting (or, with None, every one) back to its default by
    taking it out of the file. Returns the keys taken out. With None the file
    is rewritten empty even when it does not load, which mends it."""
    p = path(data_dir)
    with _locked(p, timeout):
        if key is None:
            try:
                gone = list(_read(p))
            except BehaviourError:
                gone = []
            _save(p, {})
            return gone
        values = _read(p)
        if key not in values:
            setting(key)                      # an unknown key is refused
            return []
        del values[key]
        _save(p, values)
        return [key]


HEADER = """\
# The Scraper's behaviour: only the settings changed from their default.
# WRITTEN BY scraper/behaviour.py - change it with `python -m scraper behaviour
# set <key> <value>` / `reset <key>` (or the Console). Every setting, its
# default, bounds and meaning: scraper/behaviour.py.
"""


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    raise TypeError(f"cannot write {v!r} to TOML")


def render(values: dict) -> str:
    """The file: catalogue order, each line under its default and meaning; a
    key the catalogue does not know (written by hand) is kept at the end."""
    out = [HEADER.rstrip("\n")]
    order = [s.key for s in SETTINGS if s.key in values] + [k for k in values if k not in _BY_KEY]
    for key in order:
        s = _BY_KEY.get(key)
        if s:
            out.append(f"\n# default {' '.join(filter(None, (show(s.default), s.unit)))} - {s.says}")
        else:
            out.append("\n# not a setting the Scraper knows; ignored")
        out.append(f"{json.dumps(key)} = {_toml(values[key])}")
    return "\n".join(out) + "\n"


def _save(p: pathlib.Path, values: dict) -> None:
    """Atomic: a temp file in the same directory, fsynced, renamed over the old."""
    text = render(values)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = p.stat().st_mode & 0o777 if p.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):
        dfd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


@contextlib.contextmanager
def _locked(p: pathlib.Path, timeout: float | None = None):
    """An exclusive flock on <file>.lock for one read-modify-write; a holder
    that does not let go within `timeout` seconds is an error, not a hang."""
    timeout = LOCK_TIMEOUT_S if timeout is None else timeout
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(p.name + ".lock"), "a+") as lf:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BehaviourError(f"{p} is being changed by something else "
                                         f"(lock held for over {timeout:g} s); try again") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
