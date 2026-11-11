"""What every Hub component is doing, one function each.

Each collector returns a plain dict: ``state`` (up | warn | down | off | unknown), ``checked_at``
(ISO, UTC), ``detail`` (one line for the status strip) and the component's own rows. A collector
never raises: a missing source (no Scraper data dir, no llama-server, no systemd) comes back as
``unknown`` or ``off`` with a detail saying why.

The rule for the two grey states: ``off`` is a thing that is not configured or not running and is
allowed not to be (Render's health URL not set, llama-server not answering); ``unknown``
is when the console cannot tell (no systemctl, no Scraper data dir).

The Scraper tab's ``config`` is ``scraper.targets.summary()`` (``scraper_targets.view``): targets.toml
joined to status.json, read through the Scraper's own library.

Nothing here writes, loads a page or talks to a bookmaker. Everything read from the Scraper's files,
tips, escalations and logs is untrusted text and is returned as data for the page to insert with
``textContent``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import inbox
from ..config import Settings
from ..store import Store, iso, parse_iso, utcnow
from . import scraper_targets

STATES = ("up", "warn", "down", "off", "unknown")
HERMES_HOME = Path.home() / ".hermes"
ALERTS_TAIL = 100
TIPS_ROWS = 100
BETS_ROWS = 100
JOURNAL_LINES = 50
LATEST_ROWS_MAX = 500
DAEMON_UNIT = "puntmore-link.service"   # runs `puntmore link`, the same command as `puntmore daemon`
INBOX_STALE_S = 300          # no inbox pass for 5 min (one every 30 s) means the daemon is not working
TIPS_QUIET_H = 24            # no new tip for a day: the sources are probably not running


def _now() -> str:
    return iso(utcnow())


def _result(state: str, detail: str, **rows: Any) -> dict[str, Any]:
    assert state in STATES
    return {"state": state, "checked_at": _now(), "detail": detail, **rows}


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str] | None:
    """Run a read-only command. None when it is missing or timed out."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.returncode, p.stdout


def unit_state(unit: str, run: Callable[..., tuple[int, str] | None] | None = None) -> str | None:
    """``systemctl --user is-active <unit>``: active, inactive, failed, activating, ... or None
    when systemctl is not there (or hung)."""
    res = (run or _run)(["systemctl", "--user", "is-active", unit], timeout=5.0)
    if res is None:
        return None
    out = res[1].strip().splitlines()
    return out[0] if out else ("active" if res[0] == 0 else "unknown")


def _tail(path: Path, lines: int, max_bytes: int = 256_000) -> list[str]:
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        data = fh.read().decode("utf-8", errors="replace")
    out = data.splitlines()
    if size > max_bytes and out:
        out = out[1:]          # the first line is probably cut
    return out[-lines:]


def _safe(s: str) -> str:
    """The Scraper's own file-name rule (scraper/runner.py `_safe`)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:120]


def _ts_iso(v: Any) -> str | None:
    """The Scraper writes epoch seconds; show ISO."""
    try:
        return iso(_from_epoch(float(v))) if v is not None else None
    except (TypeError, ValueError):
        return None


def _from_epoch(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _guard(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a collector never takes the page down
        return _result("unknown", f"{name}: could not read ({exc.__class__.__name__}: {str(exc)[:160]})")


# ---- Scraper ----------------------------------------------------------------------------------

def latest_rows(cfg: Settings, target: str) -> dict[str, Any] | None:
    """``latest/<target>.json`` as the Scraper wrote it: ``{at, url, rows}``. None if absent."""
    latest = cfg.scraper_data_dir / "latest"
    path = latest / f"{_safe(target)}.json"
    if path.parent != latest or not path.is_file():
        return None
    body = json.loads(path.read_text())
    rows = body.get("rows") or []
    return {"target": target, "at": _ts_iso(body.get("at")), "url": body.get("url"),
            "count": len(rows), "truncated": len(rows) > LATEST_ROWS_MAX, "rows": rows[:LATEST_ROWS_MAX]}


def _latest_count(cfg: Settings, target: str) -> int | None:
    lr = latest_rows(cfg, target)
    return lr["count"] if lr else None


def resume_command(cfg: Settings, site: str) -> str:
    """The exact command that ends a site's cool-off (the Scraper's own CLI; the console never runs it)."""
    return (f"cd {shlex.quote(str(cfg.scraper_src))} && SCRAPER_DATA_DIR={shlex.quote(str(cfg.scraper_data_dir))} "
            f"{shlex.quote(sys.executable)} -m scraper resume {shlex.quote(site)}")


def scraper(cfg: Settings) -> dict[str, Any]:
    def go():
        d = cfg.scraper_data_dir
        config = scraper_targets.view(cfg, lambda name: _latest_count(cfg, name), _ts_iso)
        if not d.is_dir():
            return _result("unknown", f"Scraper data dir {d} not found", heartbeat_age_s=None, targets=[], sites=[],
                           alerts=[], status_at=None, data_dir=str(d), config=config)
        w = inbox.scraper_status(cfg)
        status_at = None
        try:
            status_at = _ts_iso(json.loads((d / "status.json").read_text()).get("at"))
        except (OSError, ValueError):
            pass
        targets = []
        for t in w["targets"]:
            rows_parsed = None
            try:
                lr = latest_rows(cfg, str(t.get("name") or ""))
                rows_parsed = lr["count"] if lr else None
            except (OSError, ValueError):
                pass
            targets.append({"name": t.get("name"), "site": t.get("site"), "mode": t.get("mode"),
                            "state": t.get("state"), "last_load": _ts_iso(t.get("last_load")),
                            "last_ok": _ts_iso(t.get("last_parse")), "rows_last_parsed": rows_parsed,
                            "rows_posted": t.get("rows_posted"), "detail": t.get("detail"), "url": t.get("url")})
        alerts_path = d / "alerts.log"
        alerts = _tail(alerts_path, ALERTS_TAIL) if alerts_path.is_file() else []
        age = w["heartbeat_age_s"]
        n = len(targets)
        if age is None:
            state, detail = "down", "no heartbeat file"
        elif not w["running"]:
            state, detail = "down", f"heartbeat {age}s old: not running"
        elif w["blocked"] or w["unhealthy"]:
            state = "warn"
            detail = "; ".join(x for x in (w["blocked"] and "blocked: " + ", ".join(w["blocked"]),
                                           w["unhealthy"] and "unhealthy: " + ", ".join(w["unhealthy"])) if x)
        else:
            state, detail = "up", f"{n} target{'s' if n != 1 else ''}, heartbeat {age}s ago"
        sites = [{**s, "resume_command": resume_command(cfg, str(s.get("site")))} if isinstance(s, dict) else s
                 for s in w.get("sites", [])]
        return _result(state, detail, heartbeat_age_s=age, running=w["running"], blocked=w["blocked"],
                       unhealthy=w["unhealthy"], targets=targets, sites=sites, alerts=alerts,
                       status_at=status_at, data_dir=str(d), config=config)
    return _guard("scraper", go)


# ---- Daemon ----------------------------------------------------------------------------------

def daemon(cfg: Settings, store: Store, run=None) -> dict[str, Any]:
    """The Hub daemon (`puntmore daemon`, unit puntmore-link.service): the last inbox pass over the
    Scraper's drop files, the last tipster-mailbox poll, and the drop files still waiting (tips,
    events, and price drops not yet posted to Render)."""
    from .. import daemon as hub_daemon, mail

    def go():
        svc = unit_state(DAEMON_UNIT, run)
        last_inbox = hub_daemon.last_status(cfg).get("inbox")
        last_mail = mail.status(cfg)
        drops = {}
        for sub in ("tips", "events", "prices"):
            p = cfg.inbox_dir / sub
            drops[sub] = len(list(p.glob("*.json"))) if p.is_dir() else None
        at = parse_iso(last_inbox["at"]) if last_inbox and last_inbox.get("at") else None
        age = int(utcnow().timestamp() - at.timestamp()) if at else None
        seen = f"last inbox pass {age}s ago" if age is not None else "no inbox pass recorded"
        prices_waiting = ((last_inbox or {}).get("counts") or {}).get("price_waiting") or 0
        if svc is None:
            state, detail = "unknown", f"systemctl not available; {seen}"
        elif svc != "active":
            state, detail = "down", f"{DAEMON_UNIT} {svc}"
        elif last_inbox and last_inbox.get("error"):
            state, detail = "warn", f"last inbox pass failed: {last_inbox['error'][:120]}"
        elif age is None or age > INBOX_STALE_S:
            state, detail = "warn", seen
        elif prices_waiting > 0:
            state, detail = "warn", f"{seen}; {prices_waiting} price drop(s) waiting to go to Render"
        elif last_mail.get("problems"):
            state, detail = "warn", f"{seen}; mail: {last_mail['detail']}"
        else:
            state, detail = "up", f"active, {seen}"
        return _result(state, detail, service=svc, last_inbox=last_inbox, inbox_age_s=age,
                       last_mail=last_mail, inbox_drops=drops, inbox_dir=str(cfg.inbox_dir))
    return _guard("daemon", go)


# ---- Placement -------------------------------------------------------------------------------

def placement(cfg: Settings, store: Store) -> dict[str, Any]:
    def go():
        kill = cfg.kill_switch_on()
        kill_note = None
        if kill:
            try:
                kill_note = cfg.kill_switch_file.read_text()[:200].strip()
            except OSError:
                pass
        bets = []
        for r in store.recent_bets(BETS_ROWS):
            try:
                body = json.loads(r["body"] or "{}")
            except ValueError:
                body = {}
            bets.append({"bet_id": r["bet_id"], "account": r["account_id"], "status": r["status"],
                         "stake": body.get("stake"), "price": body.get("expected_price"),
                         "actual_price": r["actual_price"], "updated": r["updated"]})
        accounts = [{"account": a, "open_exposure": store.open_exposure(a), "balance": store.latest_balance(a)}
                    for a in store.accounts()]
        failed = store.summary_since(utcnow() - timedelta(hours=24))["bets_failed"]
        mode = "dry-run" if cfg.dry_run else "LIVE"
        if kill:
            state, detail = "warn", f"KILL SWITCH ON ({mode})"
        elif failed:
            state, detail = "warn", f"{mode}, {failed} failed in 24h"
        else:
            state, detail = "up", f"{mode}, kill switch off"
        return _result(state, detail, dry_run=cfg.dry_run, kill_switch=kill, kill_switch_note=kill_note,
                       kill_switch_file=str(cfg.kill_switch_file), bets=bets, accounts=accounts, failed_24h=failed)
    return _guard("placement", go)


# ---- Escalations -----------------------------------------------------------------------------

def escalations(cfg: Settings, store: Store) -> dict[str, Any]:
    def go():
        cols = ("id", "kind", "ref", "created", "detail", "outcome", "resolved")
        open_ = [{c: r[c] for c in cols} for r in store.open_escalations()][::-1]   # newest open first
        done = [{c: r[c] for c in cols} for r in store.resolved_escalations()]
        n = len(open_)
        state, detail = ("warn", f"{n} open") if n else ("up", "none open")
        return _result(state, detail, open=open_, resolved=done)
    return _guard("escalations", go)


# ---- Tips ------------------------------------------------------------------------------------

def tips(cfg: Settings, store: Store) -> dict[str, Any]:
    def go():
        rows = [dict(r) for r in store.recent_tips(TIPS_ROWS)]
        sources = [{"source": s, **store.source_record(s)} for s in store.tip_sources()]
        last = parse_iso(rows[0]["first_seen"]) if rows else None
        day = store.summary_since(utcnow() - timedelta(hours=24))["tips"]
        if last is None:
            state, detail = "off", "no tips yet"
        elif (utcnow() - last).total_seconds() > TIPS_QUIET_H * 3600:
            state, detail = "warn", f"none for {int((utcnow() - last).total_seconds() // 3600)}h"
        else:
            state, detail = "up", f"{day} in 24h, {len(sources)} source{'s' if len(sources) != 1 else ''}"
        return _result(state, detail, tips=rows, sources=sources, last_tip=iso(last), tips_24h=day)
    return _guard("tips", go)


# ---- Model (llama-server) --------------------------------------------------------------------

def _get_json(url: str, timeout: float) -> tuple[int | None, Any, str | None]:
    """GET url -> (status, parsed body or None, error). Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": "puntmore-console"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000)
            status = resp.status
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(200_000)
        except Exception:  # noqa: BLE001
            raw = b""
        status = exc.code
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{exc.__class__.__name__}: {str(exc)[:160]}"
    try:
        return status, json.loads(raw), None
    except ValueError:
        return status, None, raw[:300].decode("utf-8", "replace")


def model(cfg: Settings, timeout: float = 3.0) -> dict[str, Any]:
    def go():
        base = cfg.llama_url.rstrip("/")
        code, body, err = _get_json(base + "/health", timeout)
        health = {"status_code": code, "body": body if isinstance(body, dict) else None, "error": err}
        if code is None:
            return _result("off", f"llama-server not answering at {base}", url=base, health=health,
                           props=None, slots=None)
        props = slots = None
        pc, pb, perr = _get_json(base + "/props", timeout)
        if pc == 200 and isinstance(pb, dict):
            dgs = pb.get("default_generation_settings") or {}
            props = {"model_alias": pb.get("model_alias"),
                     "model": Path(str(pb.get("model_path") or "")).name or None,
                     "n_ctx": dgs.get("n_ctx") or pb.get("n_ctx"), "total_slots": pb.get("total_slots"),
                     "modalities": pb.get("modalities"), "build_info": pb.get("build_info")}
        else:
            props = {"unavailable": perr or f"HTTP {pc}"}
        sc, sb, serr = _get_json(base + "/slots", timeout)
        if sc == 200 and isinstance(sb, list):
            slots = []
            for s in sb:
                if not isinstance(s, dict):
                    continue
                busy = s.get("is_processing")
                if busy is None and "state" in s:
                    busy = s.get("state") != 0
                slots.append({"id": s.get("id"), "n_ctx": s.get("n_ctx"), "busy": bool(busy), "id_task": s.get("id_task")})
        else:
            slots = {"unavailable": serr or f"HTTP {sc}"}
        status = (body or {}).get("status") if isinstance(body, dict) else None
        bits = []
        if isinstance(props, dict) and props.get("model_alias"):
            bits.append(str(props["model_alias"]))
        if isinstance(slots, list):
            bits.append(f"{sum(1 for s in slots if s['busy'])}/{len(slots)} slots busy")
        if code == 200:
            state, detail = "up", ", ".join(bits) or (status or "ok")
        elif code == 503:
            state, detail = "warn", f"loading ({(body or {}).get('error', {}).get('message', 'HTTP 503') if isinstance(body, dict) else 'HTTP 503'})"
        else:
            state, detail = "down", f"/health HTTP {code}"
        return _result(state, detail, url=base, health=health, props=props, slots=slots)
    return _guard("model", go)


# ---- Hermes ----------------------------------------------------------------------------------

def hermes(cfg: Settings, run=None, home: Path | None = None) -> dict[str, Any]:
    """The gateway's unit state, its journal tail and the profile names. Never reads ~/.hermes/.env
    or anything else inside a profile: only directory names are listed."""
    def go():
        r = run or _run
        svc = unit_state("hermes-gateway.service", r)
        res = r(["journalctl", "--user", "-u", "hermes-gateway", "-n", str(JOURNAL_LINES), "--no-pager", "-o", "cat"],
                timeout=5.0)
        journal = res[1].splitlines()[-JOURNAL_LINES:] if res and res[0] == 0 else None
        h = home or HERMES_HOME
        pdir = h / "profiles"
        profiles = sorted(p.name for p in pdir.iterdir() if p.is_dir()) if pdir.is_dir() else []
        if svc is None:
            state, detail = "unknown", "systemctl not available"
        elif svc == "active":
            state, detail = "up", f"gateway active, {len(profiles)} profile{'s' if len(profiles) != 1 else ''}"
        elif svc == "failed":
            state, detail = "down", "hermes-gateway.service failed"
        else:
            state, detail = "off", f"hermes-gateway.service {svc}"
        return _result(state, detail, service=svc, journal=journal,
                       journal_error=None if journal is not None else "journalctl unavailable or failed",
                       profiles=profiles)
    return _guard("hermes", go)


# ---- Render ----------------------------------------------------------------------------------

def render(cfg: Settings, timeout: float = 5.0) -> dict[str, Any]:
    def go():
        base = cfg.render_http.rstrip("/")
        if not base:
            return _result("off", "not configured (PUNTMORE_RENDER_HTTP)", url=None, ready=None)
        t0 = time.monotonic()
        code, body, err = _get_json(base + "/api/ready", timeout)
        ms = int((time.monotonic() - t0) * 1000)
        ready = {"status_code": code, "body": body, "error": err, "ms": ms}
        if code == 200:
            state, detail = "up", f"/api/ready 200 in {ms} ms"
        elif code is None:
            state, detail = "down", f"unreachable: {err}"
        else:
            state, detail = "down", f"/api/ready HTTP {code}"
        return _result(state, detail, url=base, ready=ready)
    return _guard("render", go)


# ---- all at once -----------------------------------------------------------------------------

COMPONENTS = ("scraper", "daemon", "placement", "escalations", "tips", "model", "hermes", "render")


def collect(name: str, cfg: Settings, store_factory: Callable[[], Store]) -> dict[str, Any]:
    """One component by name. Opens (and closes) its own Store, so it is safe in a thread."""
    if name in ("scraper", "model", "hermes", "render"):
        return {"scraper": scraper, "model": model, "hermes": hermes, "render": render}[name](cfg)
    if not cfg.db_path.exists():
        # Reads never create the database: that is `puntmore init`'s (or the daemon's) job.
        return _result("unknown", f"no hub database at {cfg.db_path} (puntmore init)")
    try:
        store = store_factory()
    except Exception as exc:  # noqa: BLE001
        return _result("unknown", f"hub database unreadable: {exc.__class__.__name__}: {str(exc)[:160]}")
    try:
        return {"daemon": daemon, "placement": placement, "escalations": escalations, "tips": tips}[name](cfg, store)
    finally:
        store.close()
