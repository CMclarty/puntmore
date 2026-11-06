"""Tipster emails -> stored tips queued for Render, through `mail.poll` with a fetcher that
hands over messages built here, so no test opens a socket. The `tip` messages it queues are
checked against schemas/protocol.schema.json, the same referee as test_protocol.py."""
import imaplib
import json
from email.message import EmailMessage
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from puntmore_hub import mail
from puntmore_hub.ids import tip_id_for
from puntmore_hub.schemas import Envelope

SCHEMA = Draft202012Validator(json.loads((Path(__file__).resolve().parent.parent / "schemas" / "protocol.schema.json").read_text()),
                              format_checker=Draft202012Validator.FORMAT_CHECKER)

TIPSTERS = """
[[tipster]]
name = "example-tipster"
from = "tips@example.com"
parser = "example_tipster"
trust_weight = 0.7

[[tipster]]
name = "domain-tipster"
from = "@tipsters.example.org"
parser = "example_tipster"

[[tipster]]
name = "quiet-tipster"
from = "quiet@example.com"
parser = "example_tipster"
enabled = false
"""

BODY = "Morning all\n\nArsenal v Chelsea | Match Result | Arsenal | 2.10\nLeeds v Derby | Over/Under 2.5 | Over 2.5 | 1.9\n\nGood luck\n"


def _mail(sender="Example Tips <tips@example.com>", subject="Tips for Saturday", text=BODY, html=None,
          message_id="<abc123@example.com>", date="Sat, 07 Nov 2026 08:00:00 +0000") -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "hub@example.com", subject
    if date:
        m["Date"] = date
    if message_id:
        m["Message-ID"] = message_id
    if text is not None:
        m.set_content(text)
    if html is not None:
        if text is not None:
            m.add_alternative(html, subtype="html")
        else:
            m.set_content(html, subtype="html")
    return bytes(m)


@pytest.fixture
def mcfg(cfg, tmp_path):
    (tmp_path / "tipsters.toml").write_text(TIPSTERS)
    return cfg.with_overrides(imap_host="imap.example.com", imap_user="hub", imap_password="x",
                              tipsters_file=tmp_path / "tipsters.toml")


def fetcher_of(*raws):
    calls = []

    def fetch(cfg, *, mark_seen):
        calls.append(mark_seen)
        return list(raws)
    fetch.calls = calls
    return fetch


def test_not_configured_is_a_no_op(cfg, store):
    r = mail.poll(store, cfg, fetcher=fetcher_of(_mail()))
    assert r["configured"] is False and "not configured" in r["error"] and r["messages"] == 0
    assert not cfg.mail_status_file.exists()
    assert mail.status(cfg)["state"] == "off"


def test_a_tipster_mail_becomes_tips_queued_for_render(mcfg, store):
    r = mail.poll(store, mcfg, fetcher=fetcher_of(_mail()))
    assert r["error"] is None
    assert {k: r[k] for k in ("messages", "tips", "new_tips", "rejected", "ignored", "escalations")} == \
        {"messages": 1, "tips": 2, "new_tips": 2, "rejected": 0, "ignored": 0, "escalations": 0}
    queued = [json.loads(row["body"]) for row in store.outbox_pending()]
    assert [row["type"] for row in store.outbox_pending()] == ["tip", "tip"]
    for body in queued:
        SCHEMA.validate(json.loads(Envelope(type="tip", client_id="owner", body=body).to_json()))
    first = queued[0]
    assert first["tip_id"] == tip_id_for("example-tipster", "Arsenal v Chelsea", "Match Result", "Arsenal", "2026-11-07T08:00:00+00:00")
    assert first["source_id"] == "example-tipster" and first["origin"] == "tipster" and first["source_scope"] == "owner"
    assert first["advised_price"] == 2.1 and first["tip_time"] == "2026-11-07T08:00:00+00:00"
    assert first["extra"]["trust_weight"] == 0.7
    stored = store.conn.execute("SELECT tip_id, source_id FROM tips ORDER BY first_seen").fetchall()
    assert {s["tip_id"] for s in stored} == {q["tip_id"] for q in queued}
    # the status file says what happened
    st = json.loads(mcfg.mail_status_file.read_text())
    assert st["new_tips"] == 2 and st["error"] is None and st["last_poll"]
    s = mail.status(mcfg)
    assert s["state"] == "up" and s["problems"] == [] and "new 2" in s["detail"]


def test_the_same_message_twice_stores_nothing_new(mcfg, store):
    raw = _mail()
    mail.poll(store, mcfg, fetcher=fetcher_of(raw))
    r = mail.poll(store, mcfg, fetcher=fetcher_of(raw))   # the \\Seen flag lost: the mailbox hands it over again
    assert r["messages"] == 1 and r["seen"] == 1 and r["new_tips"] == 0 and r["tips"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 2
    assert store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2
    # the same tips in a second email (another send of the same list) are deduplicated on tip_id
    r = mail.poll(store, mcfg, fetcher=fetcher_of(_mail(message_id="<def456@example.com>", subject="Tips again")))
    assert r["tips"] == 2 and r["new_tips"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 2


def test_an_unknown_sender_is_ignored_and_counted(mcfg, store):
    r = mail.poll(store, mcfg, fetcher=fetcher_of(
        _mail(sender="Newsletter <news@other.example.net>", message_id="<n1@other>"),
        _mail(sender="quiet@example.com", message_id="<q1@example.com>"),          # configured but disabled
        _mail(sender="Dom <picks@mail.tipsters.example.org>", message_id="<d1@x>"),  # the domain rule
    ))
    assert r["messages"] == 3 and r["ignored"] == 2 and r["new_tips"] == 2 and r["escalations"] == 0
    assert store.open_escalations() == []
    assert {row["source_id"] for row in store.conn.execute("SELECT source_id FROM tips")} == {"domain-tipster"}
    assert store.mail_seen("<n1@other>")                       # looked at, never re-read


def test_zero_rows_is_an_unhealthy_source_escalation(mcfg, store):
    r = mail.poll(store, mcfg, fetcher=fetcher_of(_mail(text="Nothing today, lads.\n", subject="No tips")))
    assert r["escalations"] == 1 and r["tips"] == 0
    esc = store.open_escalations()
    assert len(esc) == 1 and esc[0]["kind"] == "unhealthy_source" and esc[0]["ref"] == "example-tipster"
    assert "No tips" in esc[0]["detail"] and "0 rows" in esc[0]["detail"] and "lads" not in esc[0]["detail"]
    assert store.mail_seen("<abc123@example.com>")
    # polled again, the message is not escalated a second time
    r = mail.poll(store, mcfg, fetcher=fetcher_of(_mail(text="Nothing today, lads.\n", subject="No tips")))
    assert r["escalations"] == 0 and len(store.open_escalations()) == 1


def test_a_parser_that_raises_is_escalated_with_the_error(mcfg, store, monkeypatch):
    def broken(subject, text, html):
        raise KeyError("layout")
    monkeypatch.setitem(mail.PARSERS, "example_tipster", broken)
    r = mail.poll(store, mcfg, fetcher=fetcher_of(_mail()))
    assert r["escalations"] == 1 and r["new_tips"] == 0
    esc = store.open_escalations()[0]
    assert esc["kind"] == "unhealthy_source" and esc["ref"] == "example-tipster"
    assert "KeyError('layout')" in esc["detail"] and "Tips for Saturday" in esc["detail"]
    assert len(esc["detail"]) <= mail.DETAIL_MAX + 10


def test_a_bad_tipsters_file_reports_and_stores_nothing(mcfg, store):
    mcfg.tipsters_file.write_text(TIPSTERS + '\n[[tipster]]\nname = "example-tipster"\nfrom = "x@y.z"\nparser = "nope"\ntrust_weight = 7\n')
    fetch = fetcher_of(_mail())
    r = mail.poll(store, mcfg, fetcher=fetch)
    assert fetch.calls == []                                   # nothing polled
    assert "duplicate name" in r["error"] and "unknown parser 'nope'" in r["error"] and "trust_weight" in r["error"]
    assert store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM alerts WHERE key='mail:tipsters'").fetchone()[0] == 1
    assert mail.status(mcfg)["state"] == "down" and "tipsters file" in mail.status(mcfg)["problems"][0]
    mcfg.tipsters_file.unlink()
    assert "not found" in mail.poll(store, mcfg, fetcher=fetch)["error"]
    mcfg.tipsters_file.write_text("this is = not [toml")
    assert "tipsters file" in mail.poll(store, mcfg, fetcher=fetch)["error"]
    assert fetch.calls == []


def test_load_tipsters_validates_every_entry(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text(TIPSTERS)
    ts = mail.load_tipsters(p)
    assert [t.name for t in ts] == ["example-tipster", "domain-tipster", "quiet-tipster"]
    assert ts[0].trust_weight == 0.7 and ts[1].trust_weight == 0.5 and ts[1].sender == "tipsters.example.org"
    assert ts[2].enabled is False
    assert mail.tipster_for(ts, "TIPS@Example.com").name == "example-tipster"
    assert mail.tipster_for(ts, "a@sub.tipsters.example.org").name == "domain-tipster"
    assert mail.tipster_for(ts, "a@nottipsters.example.org") is None
    assert mail.tipster_for(ts, "") is None
    p.write_text('[[tipster]]\nname = "a"\nfrom = "a@b.c"\nparser = "example_tipster"\ntrust_wieght = 0.5\n')
    with pytest.raises(mail.TipstersError, match="unknown key"):
        mail.load_tipsters(p)
    p.write_text('[tipster]\nname = "a"\n')
    with pytest.raises(mail.TipstersError, match="expected"):
        mail.load_tipsters(p)


def test_dry_run_stores_nothing_and_marks_nothing_seen(mcfg, store):
    fetch = fetcher_of(_mail(), _mail(sender="nobody@else.example", message_id="<x@else>"),
                       _mail(text="no tips here", message_id="<z@example.com>"))
    r = mail.poll(store, mcfg, fetcher=fetch, dry_run=True)
    assert fetch.calls == [False]                              # the mailbox is opened without flagging
    assert r["messages"] == 3 and r["tips"] == 2 and r["ignored"] == 1 and r["escalations"] == 1
    assert [row["selection"] for row in r["rows"]] == ["Arsenal", "Over 2.5"]
    assert r["rows"][0]["tip_id"] == tip_id_for("example-tipster", "Arsenal v Chelsea", "Match Result", "Arsenal", "2026-11-07")
    assert r["problems"] == ["example-tipster: [mail] 'Tips for Saturday': parser example_tipster read 0 rows"]
    assert store.conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM mail_seen").fetchone()[0] == 0
    assert store.open_escalations() == [] and not mcfg.mail_status_file.exists()
    # the real poll afterwards sees everything as new
    r = mail.poll(store, mcfg, fetcher=fetch)
    assert fetch.calls == [False, True] and r["new_tips"] == 2 and r["seen"] == 0


def test_an_imap_failure_alerts_once_and_the_poll_returns(mcfg, store):
    def down(cfg, *, mark_seen):
        raise imaplib.IMAP4.error("LOGIN failed")
    for _ in range(2):
        r = mail.poll(store, mcfg, fetcher=down)
        assert "IMAP imap.example.com:993 INBOX" in r["error"] and "LOGIN failed" in r["error"]
    assert store.conn.execute("SELECT COUNT(*) FROM alerts WHERE key='mail:imap'").fetchone()[0] == 1
    assert store.open_escalations() == []                      # a mailbox problem is not a source problem
    assert mail.status(mcfg)["state"] == "down" and "last poll failed" in mail.status(mcfg)["problems"][0]

    def refused(cfg, *, mark_seen):
        raise ConnectionRefusedError(111, "Connection refused")
    assert "Connection refused" in mail.poll(store, mcfg, fetcher=refused)["error"]
    # a dry-run reports the failure without recording an alert
    assert mail.poll(store, mcfg, fetcher=down, dry_run=True)["error"]
    assert store.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


def test_bad_rows_are_rejected_not_guessed(mcfg, store):
    text = "A v B | Match Result | A | 5000\nC v D | Match Result | C | abc\nnot a tip at all\n | Match Result | X | 2.0\n"
    r = mail.poll(store, mcfg, fetcher=fetcher_of(_mail(text=text)))
    assert r["tips"] == 1 and r["rejected"] == 1 and r["new_tips"] == 1       # the price-5000 row is rejected
    row = store.conn.execute("SELECT event, advised_price FROM tips").fetchone()
    assert row["event"] == "C v D" and row["advised_price"] is None


def test_html_only_mail_and_no_message_id(mcfg, store):
    html = "<html><body><p>Hi</p><div>Arsenal v Chelsea | Match Result | Arsenal | 2.10</div><br>Leeds v Derby | Over/Under 2.5 | Over 2.5 | 1.9</body></html>"
    raw = _mail(text=None, html=html, message_id=None)
    m = mail.read_message(raw, default_date="2026-11-07T09:00:00+00:00")
    assert m.text == "" and m.html and m.message_id.startswith("sha1:") and m.date == "2026-11-07T08:00:00+00:00"
    r = mail.poll(store, mcfg, fetcher=fetcher_of(raw))
    assert r["new_tips"] == 2
    assert mail.poll(store, mcfg, fetcher=fetcher_of(raw))["seen"] == 1


def test_a_message_without_a_date_takes_the_poll_time(mcfg, store):
    raw = _mail(date=None)
    m = mail.read_message(raw, default_date="2026-11-07T09:00:00+00:00")
    assert m.date == "2026-11-07T09:00:00+00:00"
    r = mail.poll(store, mcfg, fetcher=fetcher_of(raw))
    assert r["new_tips"] == 2
    assert store.conn.execute("SELECT tip_time FROM tips").fetchone()[0].startswith("20")


def test_the_real_fetcher_against_a_fake_imap_server(mcfg, monkeypatch):
    """imap_fetch on a fake IMAP4_SSL: UNSEEN search, BODY.PEEK fetch, \\Seen per message and
    only when asked (mark), read-only folder under a dry-run, logout on close and on failure."""
    raw1, raw2 = _mail(), _mail(message_id="<two@example.com>")
    log = []

    class FakeImap:
        def __init__(self, host, port, timeout=None):
            log.append(("connect", host, port))
            self.fail_login = host == "bad.example.com"

        def login(self, user, pw):
            log.append(("login", user))
            if self.fail_login:
                raise imaplib.IMAP4.error("LOGIN failed")

        def select(self, folder, readonly=False):
            log.append(("select", folder, readonly))
            return "OK", [b"2"]

        def uid(self, cmd, *args):
            log.append(("uid", cmd, *args))
            if cmd == "SEARCH":
                return "OK", [b"7 9"]
            if cmd == "FETCH":
                return "OK", [(b"1 (UID 7 BODY[] {1})", raw1 if args[0] == b"7" else raw2), b")"]
            return "OK", [None]

        def logout(self):
            log.append(("logout",))

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    b = mail.imap_fetch(mcfg, mark_seen=True)
    assert b.messages == [raw1, raw2]
    assert ("select", "INBOX", False) in log and ("uid", "SEARCH", None, "UNSEEN") in log
    assert ("uid", "FETCH", b"7", "(BODY.PEEK[])") in log
    assert not [e for e in log if e[1:2] == ("STORE",)]          # nothing flagged until processed
    b.mark(1)
    assert log[-1] == ("uid", "STORE", b"9", "+FLAGS", r"(\Seen)")
    b.close()
    assert log[-1] == ("logout",)
    b.close()                                                    # idempotent
    assert log[-1] == ("logout",) and log.count(("logout",)) == 1
    log.clear()
    b = mail.imap_fetch(mcfg, mark_seen=False)
    assert b.messages == [raw1, raw2]
    b.mark(0)
    b.close()
    assert ("select", "INBOX", True) in log and not [e for e in log if e[1:2] == ("STORE",)]
    log.clear()
    with pytest.raises(imaplib.IMAP4.error):
        mail.imap_fetch(mcfg.with_overrides(imap_host="bad.example.com"), mark_seen=True)
    assert log[-1] == ("logout",)


def test_status_file_goes_stale_and_health_sees_it(mcfg, store):
    mail.poll(store, mcfg, fetcher=fetcher_of(_mail()))
    assert mail.status(mcfg)["state"] == "up"
    body = json.loads(mcfg.mail_status_file.read_text())
    body["last_poll"] = "2026-01-01T00:00:00+00:00"
    mcfg.mail_status_file.write_text(json.dumps(body))
    s = mail.status(mcfg)
    assert s["state"] == "down" and "expected every 10" in s["problems"][0]
    mcfg.mail_status_file.unlink()
    assert "never polled" in mail.status(mcfg)["problems"][0]


def test_cli_mail_and_health(mcfg, store, monkeypatch, capsys):
    from puntmore_hub import cli
    monkeypatch.setattr(cli, "settings", mcfg)
    monkeypatch.setattr(mail, "imap_fetch", fetcher_of(_mail()))
    cli.main(["mail", "--dry-run"])
    out = capsys.readouterr().out
    assert "Arsenal v Chelsea | Match Result | Arsenal | 2.1" in out and "dry-run: messages=1, tips=2" in out
    assert not mcfg.mail_status_file.exists()
    cli.main(["mail"])
    assert "new_tips=2" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["health"])                                   # the Scraper is not running in this test
    assert "mail: last poll" in capsys.readouterr().out
    cli.main(["status"])
    assert "mail: last poll" in capsys.readouterr().out


def test_a_message_is_flagged_read_only_after_it_is_recorded(mcfg, store):
    """The poll that dies half way: what it had processed is flagged and in mail_seen; what
    it had not is still UNSEEN for the next poll. So mark(i) follows mail_mark_seen, one
    message at a time, and close() comes last."""
    raws = [_mail(), _mail(message_id="<two@example.com>")]
    events = []

    class B(mail.Batch):
        def mark(self, i):
            events.append(("mark", i, store.mail_seen(mail.read_message(raws[i], default_date="x").message_id)))

        def close(self):
            events.append(("close",))

    r = mail.poll(store, mcfg, fetcher=lambda cfg, *, mark_seen: B(raws))
    assert r["messages"] == 2
    assert events == [("mark", 0, True), ("mark", 1, True), ("close",)]
    events.clear()
    mail.poll(store, mcfg, fetcher=lambda cfg, *, mark_seen: B(raws), dry_run=True)
    assert events == [("close",)]                                 # a dry-run flags nothing
