"""Tipster emails -> stored tips queued for Render.

The Bet Finder's email tipsters (spec, "Tipsters by email") arrive in one dedicated mailbox
that this box polls over IMAP (`PUNTMORE_IMAP_*`); Render never polls a mailbox and the
Scraper never reads one. Each poll re-reads `tipsters.toml` (which senders are tipsters, which
parser reads each, their trust weight), fetches the messages the mailbox has not shown us
yet, and hands every message from a tipster to that tipster's parser
(`tipsters/<parser>.py`). The rows go down the same storage path as the Scraper's drops,
`inbox.ingest_rows`, so a tip is one thing wherever it came from.

Two things make a message idempotent. The mailbox's `\\Seen` flag keeps a poll from
re-reading the whole folder (the search is UNSEEN); it is set per message, AFTER that
message has been processed and recorded, so a poll that dies half way leaves the rest
unseen for the next one, never flagged-but-unread. The store's `mail_seen` table, keyed on
Message-ID, is the record that a message was read, so one is never turned into tips or an
escalation twice even if the flag is lost. The
`tip_id` of a mail tip is minted here, at first sight, with the same function the Scraper
uses, and travels unchanged from here on.

Nothing here guesses. A parser that reads zero rows, or raises, is an `unhealthy_source`
escalation for that tipster, with the subject and the error as its detail and never the
body. A message from no configured tipster is ignored and counted. A mailbox that cannot be
reached is one alert (de-duplicated), not an escalation, and the poll stops there. A
tipsters file that will not load is reported the same way and that poll does nothing.

The fetch is a callable (`imap_fetch` is the real one, on `imaplib`) returning a `Batch`:
the messages as raw bytes, `mark(i)` to flag one of them once it is processed, `close()` to
let the connection go. So the rest can be run on messages built in a test without a socket
(a plain list of bytes is accepted as a batch that marks nothing), and `--dry-run` can read a
real mailbox without flagging anything.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import imaplib
import json
import logging
import tomllib
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Callable

from . import alerts, escalation
from .config import Settings, settings as default_settings
from .ids import tip_id_for
from .inbox import ingest_rows, tips_from_rows
from .store import Store, iso, parse_iso, utcnow
from .tipsters import PARSERS

log = logging.getLogger("puntmore.mail")

# fetcher(cfg, mark_seen=bool) -> a Batch (or a plain list of raw RFC822 bytes). With
# mark_seen, Batch.mark(i) flags message i \Seen in the mailbox; a dry-run passes False and
# nothing is flagged.
Fetcher = Callable[..., "Batch | list[bytes]"]


class Batch:
    """The messages one fetch brought back, and how to flag each one read once it has been
    processed. The plain version (no connection) is what a list of bytes becomes."""

    def __init__(self, messages: list[bytes]):
        self.messages = messages

    def mark(self, i: int) -> None:   # noqa: ARG002 - nothing to flag
        return None

    def close(self) -> None:
        return None


def as_batch(fetched) -> Batch:
    return fetched if isinstance(fetched, Batch) else Batch(list(fetched))

DETAIL_MAX = 300           # an escalation's detail: the subject and the error, never the body
COUNT_KEYS = ("messages", "tips", "new_tips", "rejected", "ignored", "seen", "escalations")
STALE_POLLS = 3            # health: a last poll older than this many intervals is a dead poller


# ---- the tipsters file ---------------------------------------------------------------------------

class TipstersError(ValueError):
    """The tipsters file will not load. Every problem in it, so it is fixed in one go."""


@dataclass(frozen=True)
class Tipster:
    name: str              # the source id Render sees
    sender: str            # the `from` rule, lower-cased: an address, or a domain (suffix)
    parser: str            # a name in tipsters.PARSERS
    trust_weight: float = 0.5
    enabled: bool = True

    def matches(self, address: str) -> bool:
        address = address.lower()
        if "@" in self.sender:
            return address == self.sender
        domain = address.rpartition("@")[2]
        return domain == self.sender or domain.endswith("." + self.sender)

    @property
    def specificity(self) -> tuple[int, int]:
        """An exact address beats a domain; a longer domain beats a shorter one."""
        return (1 if "@" in self.sender else 0, len(self.sender))


KEYS = {"name", "from", "parser", "trust_weight", "enabled"}


def _sender_rule(raw: str) -> str:
    """`from` as written -> the rule: `Name <a@b.com>` is `a@b.com`; `@b.com` and `b.com` are the domain."""
    addr = email.utils.parseaddr(raw)[1] if "<" in raw else raw
    return addr.strip().lower().lstrip("@")


def load_tipsters(path: Path) -> list[Tipster]:
    """The `[[tipster]]` entries of `path`, validated. Raises TipstersError naming every
    problem (unknown parser, weight outside 0..1, duplicate name, unknown key) rather than
    taking a guess at any of them; disabled entries are returned so their mail is ignored
    rather than escalated."""
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise TipstersError(f"{path}: not found") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise TipstersError(f"{path}: {exc}") from None
    entries = data.get("tipster")
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise TipstersError(f"{path}: expected [[tipster]] entries")
    out, problems, names = [], [], set()
    for i, e in enumerate(entries, 1):
        before = len(problems)
        name = e.get("name")
        where = f"tipster {name!r}" if isinstance(name, str) and name.strip() else f"tipster #{i}"
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{where}: name missing")
        elif name in names:
            problems.append(f"{where}: duplicate name")
        names.add(name)
        unknown = sorted(set(e) - KEYS)
        if unknown:
            problems.append(f"{where}: unknown key(s) {', '.join(unknown)}")
        sender = e.get("from")
        if not isinstance(sender, str) or not _sender_rule(sender):
            problems.append(f"{where}: from missing (an address or a domain)")
        parser = e.get("parser")
        if not isinstance(parser, str) or parser not in PARSERS:
            problems.append(f"{where}: unknown parser {parser!r} (have {', '.join(sorted(PARSERS)) or 'none'})")
        weight = e.get("trust_weight", 0.5)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 1:
            problems.append(f"{where}: trust_weight must be 0..1, not {weight!r}")
        enabled = e.get("enabled", True)
        if not isinstance(enabled, bool):
            problems.append(f"{where}: enabled must be true or false, not {enabled!r}")
        if len(problems) == before:
            out.append(Tipster(name=name.strip(), sender=_sender_rule(sender), parser=parser,
                               trust_weight=float(weight), enabled=enabled))
    if problems:
        raise TipstersError(f"{path}: " + "; ".join(problems))
    return out


def tipster_for(tipsters: list[Tipster], address: str) -> Tipster | None:
    """The most specific tipster whose rule matches the sender address, or None."""
    hits = [t for t in tipsters if address and t.matches(address)]
    return max(hits, key=lambda t: t.specificity) if hits else None


# ---- one message ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Message:
    message_id: str
    sender: str
    subject: str
    date: str              # ISO, the Date header (UTC assumed when it has no zone), else the poll's time
    text: str
    html: str | None


def _part(msg, kind: str) -> str | None:
    part = msg.get_body(preferencelist=(kind,))
    if part is None:
        return None
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):   # a charset Python does not know
        return None
    return content if isinstance(content, str) else None


def read_message(raw: bytes, *, default_date: str) -> Message:
    """The parts of a raw RFC822 message a parser and the poll need. A message with no
    Message-ID gets one from its bytes so it is still read exactly once."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    mid = str(msg.get("Message-ID") or "").strip() or "sha1:" + hashlib.sha1(raw).hexdigest()
    sender = email.utils.parseaddr(str(msg.get("From") or ""))[1].lower()
    date = default_date
    try:
        dt = email.utils.parsedate_to_datetime(str(msg.get("Date")))
        date = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        pass
    return Message(message_id=mid, sender=sender, subject=str(msg.get("Subject") or "").strip(),
                   date=date, text=_part(msg, "plain") or "", html=_part(msg, "html"))


def _rows_for(tipster: Tipster, m: Message) -> list[dict]:
    """The parser's rows with what the source knows filled in: the email's Date as the
    tip_time of a row without one, `tipster` as the origin, and the tip_id minted here."""
    out = []
    for row in PARSERS[tipster.parser](m.subject, m.text, m.html):
        row = {**row, "tip_time": row.get("tip_time") or m.date, "origin": row.get("origin") or "tipster"}
        ev, mk, sel = (str(row.get(k) or "") for k in ("event", "market", "selection"))
        if ev and mk and sel:
            row["tip_id"] = tip_id_for(tipster.name, ev, mk, sel, str(row["tip_time"]))
        out.append(row)
    return out


# ---- the mailbox ---------------------------------------------------------------------------------

class ImapBatch(Batch):
    """The real batch: one IMAP connection held open until close(). Every UNSEEN message is
    fetched with BODY.PEEK (flags untouched); mark(i) flags that one message \\Seen, so the
    next poll's search starts after what was actually processed. Under a dry-run the folder
    is opened read-only and mark() does nothing."""

    def __init__(self, cfg: Settings, *, mark_seen: bool):
        self._conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port, timeout=60)
        self._mark = mark_seen
        self._uids: list[bytes] = []
        try:
            conn = self._conn
            conn.login(cfg.imap_user, cfg.imap_password)
            typ, _ = conn.select(cfg.imap_folder, readonly=not mark_seen)
            if typ != "OK":
                raise imaplib.IMAP4.error(f"cannot open folder {cfg.imap_folder!r}")
            typ, data = conn.uid("SEARCH", None, "UNSEEN")
            if typ != "OK":
                raise imaplib.IMAP4.error("search failed")
            uids = data[0].split() if data and data[0] else []
            out = []
            for uid in uids:
                typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                if typ != "OK" or not data or not isinstance(data[0], tuple):
                    raise imaplib.IMAP4.error(f"fetch of uid {uid!r} failed")
                out.append(data[0][1])
            self._uids = uids
            super().__init__(out)
        except BaseException:
            self.close()
            raise

    def mark(self, i: int) -> None:
        if self._mark and self._conn is not None:
            self._conn.uid("STORE", self._uids[i], "+FLAGS", r"(\Seen)")

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def imap_fetch(cfg: Settings, *, mark_seen: bool = True) -> Batch:
    """The real fetcher (see ImapBatch)."""
    return ImapBatch(cfg, mark_seen=mark_seen)


# ---- the poll ------------------------------------------------------------------------------------

def _write_status(cfg: Settings, counts: dict) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    body = {"last_poll": iso(utcnow()), **{k: counts[k] for k in COUNT_KEYS}, "error": counts["error"]}
    tmp = cfg.mail_status_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=1))
    tmp.replace(cfg.mail_status_file)


def poll(store: Store, cfg: Settings = default_settings, *, fetcher: Fetcher | None = None,
         dry_run: bool = False, notify: bool = True) -> dict:
    """One poll of the mailbox. Returns the counts (`COUNT_KEYS`), `configured`, and `error`
    (None, or why the poll stopped). A dry-run returns the `rows` it would store (each with
    its tip_id) and the `problems` it would escalate or reject, and changes nothing: no tip,
    no escalation, no alert, no status file, no message flagged or recorded as seen."""
    counts: dict = {"configured": cfg.mail_configured, **{k: 0 for k in COUNT_KEYS}, "error": None}
    if dry_run:
        counts.update(rows=[], problems=[])
    if not cfg.mail_configured:
        counts["error"] = "not configured (PUNTMORE_IMAP_HOST is blank)"
        return counts

    def stop(error: str, key: str) -> dict:
        counts["error"] = error
        log.error("mail: %s", error)
        if not dry_run:
            alerts.send(store, key, f"mail: {error}", level="error", cfg=cfg)
            _write_status(cfg, counts)
        return counts

    try:
        tipsters = load_tipsters(cfg.tipsters_file)
    except TipstersError as exc:
        return stop(f"tipsters file not loaded, nothing polled: {exc}", "mail:tipsters")
    try:
        batch = as_batch((fetcher or imap_fetch)(cfg, mark_seen=not dry_run))
    except (imaplib.IMAP4.error, OSError) as exc:
        return stop(f"IMAP {cfg.imap_host}:{cfg.imap_port} {cfg.imap_folder}: {exc!r}", "mail:imap")
    try:
        _process(store, cfg, tipsters, batch, counts, dry_run=dry_run, notify=notify)
    finally:
        batch.close()
    if not dry_run:
        _write_status(cfg, counts)
    return counts


def _flag(batch: Batch, i: int) -> None:
    """Flag message i read in the mailbox, after it is recorded here. A flag that fails
    costs one re-read next poll (mail_seen skips it), never a lost message."""
    try:
        batch.mark(i)
    except (imaplib.IMAP4.error, OSError) as exc:
        log.warning("mail: could not flag message %d as seen: %s", i, exc)


def _process(store: Store, cfg: Settings, tipsters, batch: Batch, counts: dict, *,
             dry_run: bool, notify: bool) -> None:
    now = iso(utcnow())
    for i, raw in enumerate(batch.messages):
        counts["messages"] += 1
        try:
            m = read_message(raw, default_date=now)
        except Exception as exc:  # noqa: BLE001 - one malformed message must not stop the poll
            log.warning("mail: unreadable message (%s); ignored", exc)
            counts["ignored"] += 1
            if not dry_run:
                store.mail_mark_seen("sha1:" + hashlib.sha1(raw).hexdigest())
                _flag(batch, i)
            continue
        if store.mail_seen(m.message_id):
            counts["seen"] += 1
            if not dry_run:
                _flag(batch, i)
            continue
        tipster = tipster_for(tipsters, m.sender)
        if tipster is None or not tipster.enabled:
            counts["ignored"] += 1
            log.info("mail: ignored %r from %s (%s)", m.subject, m.sender or "?",
                     "tipster disabled" if tipster else "no tipster")
        else:
            problem = None
            try:
                rows = _rows_for(tipster, m)
            except Exception as exc:  # noqa: BLE001 - a parser's failure is the tipster's escalation
                rows, problem = [], f"parser {tipster.parser} raised {exc!r}"
            if not rows and problem is None:
                problem = f"parser {tipster.parser} read 0 rows"
            if problem:
                detail = escalation.clean_detail(f"[mail] {m.subject!r}: {problem}", DETAIL_MAX)
                if dry_run:
                    counts["problems"].append(f"{tipster.name}: {detail}")
                else:
                    escalation.raise_escalation(store, "unhealthy_source", tipster.name, detail, cfg=cfg, notify=notify)
                counts["escalations"] += 1
            elif dry_run:
                tips, problems = tips_from_rows(tipster.name, rows, at=m.date, trust_weight=tipster.trust_weight)
                counts["rows"].extend({"tip_id": tid, **tip.to_dict()} for tid, tip in tips)
                counts["problems"].extend(f"{tipster.name}: rejected {p}" for p in problems)
                counts["tips"] += len(tips)
                counts["rejected"] += len(problems)
            else:
                got = ingest_rows(store, tipster.name, rows, at=m.date, trust_weight=tipster.trust_weight)
                for k in ("tips", "new_tips", "rejected"):
                    counts[k] += got[k]
                for p in got["problems"][:3]:
                    log.warning("mail %s: rejected %s", tipster.name, p)
        if not dry_run:
            store.mail_mark_seen(m.message_id)
            _flag(batch, i)


def status(cfg: Settings = default_settings) -> dict:
    """What the last poll did, from the status file, judged: `state` is `off` (not configured),
    `up`, or `down` with `problems` saying why (no poll yet, the last poll errored, or it is
    older than STALE_POLLS intervals, which means the poller is not running)."""
    out: dict = {"configured": cfg.mail_configured, "interval_min": cfg.imap_every_min, "last_poll": None,
                 "age_s": None, "counts": None, "error": None, "problems": [], "state": "off"}
    if not cfg.mail_configured:
        out["detail"] = "not configured"
        return out
    try:
        body = json.loads(cfg.mail_status_file.read_text())
        last = parse_iso(body.get("last_poll"))
    except (OSError, ValueError, TypeError):
        body, last = {}, None
    if last is None:
        out["problems"].append("mail: configured but never polled (is the daemon running?)")
    else:
        out["last_poll"] = body["last_poll"]
        out["age_s"] = int(utcnow().timestamp() - last.timestamp())
        out["counts"] = {k: body.get(k, 0) for k in COUNT_KEYS}
        out["error"] = body.get("error")
        if out["error"]:
            out["problems"].append(f"mail: last poll failed: {out['error']}")
        if out["age_s"] > STALE_POLLS * cfg.imap_every_min * 60:
            out["problems"].append(f"mail: last poll {out['age_s'] // 60} min ago, expected every {cfg.imap_every_min}")
    out["state"] = "down" if out["problems"] else "up"
    if out["counts"]:
        c = out["counts"]
        out["detail"] = (f"last poll {out['last_poll']} ({out['age_s']}s ago): messages {c['messages']}, tips {c['tips']}, "
                         f"new {c['new_tips']}, rejected {c['rejected']}, ignored {c['ignored']}, escalations {c['escalations']}"
                         + (f", error: {out['error']}" if out["error"] else ""))
    else:
        out["detail"] = "configured, no poll yet"
    return out
