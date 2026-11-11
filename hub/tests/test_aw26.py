"""AW 26 on the Hub (puntmore_hub/aw26.py): the client HubAI reads slip photos through, and the
Paddy terminal's printed times turned to UTC."""
import json

import pytest

from puntmore_hub import aw26


class _Resp:
    def __init__(self, status, body, ctype="application/json"):
        self.status = status
        self._body = body if isinstance(body, bytes) else (
            body.encode() if isinstance(body, str) else json.dumps(body).encode())
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body


@pytest.fixture
def cfg(cfg):
    return cfg.with_overrides(render_http="https://puntmore.example/", render_api_key="k")


@pytest.fixture(autouse=True)
def _not_reading(monkeypatch):
    """No test runs as a reader unless it sets PUNTMORE_AW26_PHOTO_ID itself."""
    monkeypatch.delenv("PUNTMORE_AW26_PHOTO_ID", raising=False)


# ---- printed times ----------------------------------------------------------------------------

@pytest.mark.parametrize("printed, iso", [
    ("10/17/2026, 12:06:07 PM", "2026-10-17T11:06:07Z"),     # BST
    ("11/14/2026, 3:00 PM", "2026-11-14T15:00:00Z"),         # GMT
    ("10/12/2026, 3:00 PM", "2026-10-12T14:00:00Z"),         # 12 Oct, never 10 Dec
    ("10/17/2026 12:06 PM", "2026-10-17T11:06:00Z"),         # no comma, no seconds
    ("10/17/2026, 12:06:07 AM", "2026-10-16T23:06:07Z"),     # 12 AM is midnight
    ("2026-10-17T11:06:07Z", "2026-10-17T11:06:07Z"),
    ("2026-10-17T12:06:07+01:00", "2026-10-17T11:06:07Z"),
])
def test_a_printed_time_is_london_turned_to_utc(printed, iso):
    assert aw26.parse_printed(printed) == iso


@pytest.mark.parametrize("bad", ["12:30 PM", "17/10/2026 12:30", "10/17/2026, 13:00 PM",
                                 "13/01/2026, 1:00 PM", "yesterday", ""])
def test_an_unreadable_time_is_refused(bad):
    with pytest.raises(ValueError, match="unreadable time"):
        aw26.parse_printed(bad)


@pytest.mark.parametrize("bare", ["2026-10-17T11:06:07", "2026-10-17 11:06"])
def test_an_iso_time_with_no_offset_is_refused_not_read_as_utc(bare):
    with pytest.raises(ValueError, match="no offset"):
        aw26.parse_printed(bare)


# ---- the request helper -----------------------------------------------------------------------

def test_request_sends_a_bearer_key_and_answers_a_409_without_raising(cfg):
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["method"] = req.full_url, req.get_method()
        seen["auth"], seen["body"] = req.get_header("Authorization"), json.loads(req.data)
        seen["ctype"] = req.get_header("Content-type")
        return _Resp(409, b'{"detail":"x"}')
    out = aw26.request(cfg, "POST", "/api/aw26/slips/record", body={"a": 1}, opener=opener)
    assert out == (409, b'{"detail":"x"}', "application/json")
    assert seen == {"url": "https://puntmore.example/api/aw26/slips/record", "method": "POST",
                    "auth": "Bearer k", "body": {"a": 1}, "ctype": "application/json"}


def test_request_says_which_setting_is_missing_before_sending(cfg):
    never = lambda req, timeout: pytest.fail("sent")
    with pytest.raises(RuntimeError, match="PUNTMORE_RENDER_HTTP"):
        aw26.request(cfg.with_overrides(render_http=""), "GET", "/api/aw26/photos", opener=never)
    with pytest.raises(RuntimeError, match="PUNTMORE_RENDER_API_KEY"):
        aw26.request(cfg.with_overrides(render_api_key=" "), "GET", "/api/aw26/photos", opener=never)
    assert aw26.configured(cfg)
    assert not aw26.configured(cfg.with_overrides(render_http=""))
    assert not aw26.configured(cfg.with_overrides(render_api_key=""))


def test_request_builds_multipart_when_given_files(cfg):
    seen = {}

    def opener(req, timeout):
        seen["ctype"], seen["data"] = req.get_header("Content-type"), req.data
        return _Resp(200, [])
    aw26.request(cfg, "POST", "/api/aw26/photos", files=[("slip.jpg", b"\xff\xd8jpeg", "image/jpeg")],
                 form={"source": "telegram"}, opener=opener)
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    boundary = seen["ctype"].split("boundary=", 1)[1].encode()
    data = seen["data"]
    assert data.startswith(b"--" + boundary + b"\r\n") and data.endswith(b"--" + boundary + b"--\r\n")
    assert b'Content-Disposition: form-data; name="source"\r\n\r\ntelegram\r\n' in data
    assert (b'Content-Disposition: form-data; name="files"; filename="slip.jpg"\r\n'
            b'Content-Type: image/jpeg\r\n\r\n\xff\xd8jpeg\r\n') in data


def test_render_error_carries_status_and_detail():
    exc = aw26.RenderError(400, "no receipt number")
    assert isinstance(exc, RuntimeError) and (exc.status, exc.detail) == (400, "no receipt number")


# ---- the commands HubAI calls -----------------------------------------------------------------

SLIP = {"receipt": "B0000000000000-000000-0", "placed_at": "10/16/2026, 4:17:52 PM", "offer": "refund",
        "stake": 10.0, "max_return": 55.0, "total_odds": 5.5, "photo_id": 7,
        "legs": [{"event": "Everton v Chelsea", "market": "Total Corners", "selection": "Over 9.5",
                  "odds": 1.9, "kickoff": "10/17/2026, 12:30 PM", "fair_odds": 1.8, "returns": 0},
                 {"event": "Arsenal v Spurs", "selection": "Arsenal", "odds": 2.9, "kickoff": ""}]}

GAMES = [
    {"event_id": "e1", "match": "Everton v Chelsea", "home": "Everton", "away": "Chelsea",
     "commence_time": "2026-10-17T11:30:00Z", "sport_key": "soccer_epl", "best_value": 4.0,
     "bets": [{"pick": "Over 9.5 corners", "odds": 1.9, "fair": 1.8, "value": 5.5, "market": "corners",
               "line": 9.5, "side": "Over", "basis": "ladder"}], "fairs": [], "priced_at": None},
    {"event_id": "e2", "match": "Arsenal v Spurs", "home": "Arsenal", "away": "Spurs",
     "commence_time": "2026-10-17T14:00:00Z", "sport_key": "soccer_epl", "best_value": 3.0,
     "bets": [], "fairs": [], "priced_at": None},
]


class _Recorder:
    """A fake opener: answers each call in turn and keeps the requests."""

    def __init__(self, *answers):
        self.answers, self.reqs = list(answers), []

    def __call__(self, req, timeout):
        self.reqs.append(req)
        return self.answers.pop(0)


def test_record_posts_utc_times_and_never_a_printed_total(cfg):
    op = _Recorder(_Resp(200, {"id": 1, "status": "pending"}))
    assert aw26.record(cfg, SLIP, opener=op) == {"recorded": {"id": 1, "status": "pending"}}
    [req] = op.reqs
    assert req.full_url == "https://puntmore.example/api/aw26/slips/record" and req.get_method() == "POST"
    sent = json.loads(req.data)
    assert sent["placed_at"] == "2026-10-16T15:17:52Z"
    assert [leg["kickoff"] for leg in sent["legs"]] == ["2026-10-17T11:30:00Z", ""]
    assert "max_return" not in sent and "total_odds" not in sent and "returns" not in sent["legs"][0]
    assert (sent["receipt"], sent["stake"], sent["photo_id"]) == ("B0000000000000-000000-0", 10.0, 7)


def test_record_refuses_an_unreadable_time_before_any_request(cfg):
    never = lambda req, timeout: pytest.fail("sent")
    with pytest.raises(ValueError, match="placed_at"):
        aw26.record(cfg, {**SLIP, "placed_at": "yesterday"}, opener=never)
    with pytest.raises(ValueError, match=r"legs\[0\]\.kickoff"):
        aw26.record(cfg, {**SLIP, "legs": [{**SLIP["legs"][0], "kickoff": "Sat 3pm"}]}, opener=never)


def test_record_dry_run_posts_nothing(cfg):
    out = aw26.record(cfg, SLIP, dry_run=True, opener=lambda req, timeout: pytest.fail("sent"))
    assert out["dry_run"]["placed_at"] == "2026-10-16T15:17:52Z" and "max_return" not in out["dry_run"]


@pytest.mark.parametrize("receipt", [None, "", "   "])
def test_record_refuses_a_slip_with_no_receipt_before_any_request(cfg, receipt):
    slip = {k: v for k, v in SLIP.items() if k != "receipt"}
    if receipt is not None:
        slip["receipt"] = receipt
    never = lambda req, timeout: pytest.fail("sent")
    for dry_run in (False, True):
        with pytest.raises(ValueError, match="no receipt: fail the photo"):
            aw26.record(cfg, slip, dry_run=dry_run, opener=never)


def test_a_slip_with_no_offer_is_other(cfg):
    bare = {k: v for k, v in SLIP.items() if k != "offer"}
    assert aw26.record(cfg, bare, dry_run=True)["dry_run"]["offer"] == "other"   # never Render's refund
    assert aw26.record(cfg, {**bare, "offer": "boost"}, dry_run=True)["dry_run"]["offer"] == "boost"


def test_record_a_duplicate_is_success_and_any_other_refusal_raises(cfg):
    dup = _Resp(409, {"detail": "Slip B0000000000000-000000-0 is already in"})
    assert aw26.record(cfg, SLIP, opener=_Recorder(dup)) == \
        {"duplicate": "Slip B0000000000000-000000-0 is already in"}
    with pytest.raises(aw26.RenderError) as err:
        aw26.record(cfg, SLIP, opener=_Recorder(_Resp(400, {"detail": "no receipt number"})))
    assert (err.value.status, err.value.detail) == (400, "no receipt number")


def test_board_asks_for_the_utc_moment_and_keeps_the_named_game(cfg):
    op = _Recorder(_Resp(200, GAMES))
    out = aw26.board(cfg, "10/17/2026, 12:06:07 PM", match="Everton v Chelsea", opener=op)
    assert op.reqs[0].full_url == "https://puntmore.example/api/paddy/board-at?at=2026-10-17T11%3A06%3A07Z"
    assert out == [{"event_id": "e1", "match": "Everton v Chelsea", "commence_time": "2026-10-17T11:30:00Z",
                    "bets": [{"market": "corners", "line": 9.5, "side": "over", "pick": "Over 9.5 corners",
                              "odds": 1.9, "fair": 1.8}]}]      # side lower case, as a leg's
    assert [g["event_id"] for g in aw26.board(cfg, "2026-10-17T11:06:07Z", opener=_Recorder(_Resp(200, GAMES)))] \
        == ["e1", "e2"]
    assert aw26.board(cfg, "2026-10-17T11:06:07Z", match="v FC", opener=_Recorder(_Resp(200, GAMES))) == []


def test_take_takes_the_photo_then_saves_its_image(cfg, tmp_path):
    op = _Recorder(_Resp(200, {"id": 7, "state": "reading"}), _Resp(200, b"\xff\xd8jpeg", "image/jpeg"))
    path = aw26.take(cfg, 7, tmp_path / "photos", opener=op)
    assert path == tmp_path / "photos" / "7.jpg" and path.read_bytes() == b"\xff\xd8jpeg"
    assert [(r.get_method(), r.full_url.split(".example")[1]) for r in op.reqs] == \
        [("POST", "/api/aw26/photos/7/take"), ("GET", "/api/aw26/photos/7/image")]
    with pytest.raises(aw26.RenderError, match="409"):
        aw26.take(cfg, 7, tmp_path, opener=_Recorder(_Resp(409, {"detail": "Photo 7 is reading"})))


def test_photos_fail_and_upload(cfg, tmp_path):
    op = _Recorder(_Resp(200, [{"id": 7, "state": "waiting"}]))
    assert aw26.photos(cfg, opener=op) == [{"id": 7, "state": "waiting"}]
    assert op.reqs[0].full_url.endswith("/api/aw26/photos") and op.reqs[0].get_method() == "GET"

    op = _Recorder(_Resp(200, {"id": 7, "state": "waiting", "tries": 1}))
    assert aw26.fail(cfg, 7, "blurred", opener=op)["tries"] == 1
    assert op.reqs[0].full_url.endswith("/api/aw26/photos/7/fail") and json.loads(op.reqs[0].data) == {"reason": "blurred"}

    photo = tmp_path / "slip.png"
    photo.write_bytes(b"\x89PNG")
    op = _Recorder(_Resp(200, [{"id": 8, "state": "waiting", "source": "telegram"}]))
    assert aw26.upload(cfg, photo, opener=op) == {"id": 8, "state": "waiting", "source": "telegram"}
    req = op.reqs[0]
    assert req.full_url.endswith("/api/aw26/photos") and req.get_header("Content-type").startswith("multipart/form-data")
    assert b'name="source"\r\n\r\ntelegram\r\n' in req.data
    assert b'filename="slip.png"\r\nContent-Type: image/png\r\n\r\n\x89PNG' in req.data


@pytest.mark.parametrize("name, data, ctype", [
    ("slip", b"\xff\xd8\xff\xe0jpeg", "image/jpeg"),                  # no extension
    ("slip.bin", b"\x89PNG\r\n\x1a\n", "image/png"),                  # a name that is no image type
    ("file_17", b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image/webp"),
    ("slip", b"hello", "application/octet-stream"),                  # unknown stays as it is
    ("slip.bin", b"hello", "application/octet-stream"),
    ("slip.png", b"\xff\xd8\xff\xe0jpeg", "image/png"),                # the name's image type wins
])
def test_upload_sniffs_the_image_type_when_the_name_gives_none(cfg, tmp_path, name, data, ctype):
    photo = tmp_path / name
    photo.write_bytes(data)
    op = _Recorder(_Resp(200, [{"id": 8, "state": "waiting"}]))
    aw26.upload(cfg, photo, opener=op)
    assert f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() in op.reqs[0].data


# ---- the CLI ----------------------------------------------------------------------------------

def test_the_cli_records_a_duplicate_as_success(cfg, monkeypatch, tmp_path, capsys):
    from puntmore_hub import cli
    monkeypatch.setattr(cli, "settings", cfg)
    seen = []

    def urlopen(req, timeout):
        seen.append(req)
        return _Resp(409, {"detail": "Photo 7 is already a slip"})
    monkeypatch.setattr(aw26, "_open", urlopen)
    slip = tmp_path / "slip.json"
    slip.write_text(json.dumps(SLIP))
    cli.main(["aw26", "record", str(slip)])                       # no SystemExit: exit 0
    assert json.loads(capsys.readouterr().out) == {"duplicate": "Photo 7 is already a slip"}

    cli.main(["aw26", "record", str(slip), "--dry-run"])
    assert len(seen) == 1 and json.loads(capsys.readouterr().out)["dry_run"]["placed_at"] == "2026-10-16T15:17:52Z"

    slip.write_text(json.dumps({**SLIP, "placed_at": "yesterday"}))
    with pytest.raises(SystemExit) as out:
        cli.main(["aw26", "record", str(slip)])
    assert out.value.code == 2 and "placed_at" in capsys.readouterr().err and len(seen) == 1


def test_the_cli_exits_1_on_any_other_refusal(cfg, monkeypatch, tmp_path, capsys):
    from puntmore_hub import cli
    monkeypatch.setattr(cli, "settings", cfg)
    monkeypatch.setattr(aw26, "_open", lambda req, timeout: _Resp(400, {"detail": "no receipt number"}))
    slip = tmp_path / "slip.json"
    slip.write_text(json.dumps(SLIP))
    with pytest.raises(SystemExit) as out:
        cli.main(["aw26", "record", str(slip)])
    assert out.value.code != 0 and out.value.code != 2 and "no receipt number" in str(out.value.code)


def test_the_cli_lists_takes_fails_uploads_and_reads_the_board(cfg, monkeypatch, tmp_path, capsys):
    from puntmore_hub import cli
    cfg = cfg.with_overrides(data_dir=tmp_path)
    monkeypatch.setattr(cli, "settings", cfg)
    answers = {"/api/aw26/photos": _Resp(200, [{"id": 7, "state": "waiting"}]),
               "/api/aw26/photos/7/take": _Resp(200, {"id": 7, "state": "reading"}),
               "/api/aw26/photos/7/image": _Resp(200, b"img", "image/webp"),
               "/api/aw26/photos/7/fail": _Resp(200, {"id": 7, "state": "failed"}),
               "/api/paddy/board-at": _Resp(200, GAMES)}
    monkeypatch.setattr(aw26, "_open", lambda req, timeout: answers[req.full_url.split(".example")[1].split("?")[0]])
    cli.main(["aw26", "photos"])
    assert json.loads(capsys.readouterr().out) == [{"id": 7, "state": "waiting"}]
    cli.main(["aw26", "take", "7"])
    taken = json.loads(capsys.readouterr().out)
    assert taken["id"] == 7 and taken["path"].endswith("7.webp")
    cli.main(["aw26", "fail", "7", "--reason", "blurred"])
    assert json.loads(capsys.readouterr().out)["state"] == "failed"
    cli.main(["aw26", "board", "--at", "10/17/2026, 12:06:07 PM", "--match", "Arsenal v Spurs"])
    assert [g["event_id"] for g in json.loads(capsys.readouterr().out)] == ["e2"]
    photo = tmp_path / "slip.jpg"
    photo.write_bytes(b"jpeg")
    answers["/api/aw26/photos"] = _Resp(200, [{"id": 9, "source": "page"}])
    cli.main(["aw26", "upload", str(photo), "--source", "page"])
    assert json.loads(capsys.readouterr().out) == {"id": 9, "source": "page"}


def test_the_cli_refuses_a_slip_with_no_receipt(cfg, monkeypatch, tmp_path, capsys):
    from puntmore_hub import cli
    monkeypatch.setattr(cli, "settings", cfg)
    monkeypatch.setattr(aw26, "_open", lambda req, timeout: pytest.fail("sent"))
    slip = tmp_path / "slip.json"
    slip.write_text(json.dumps({k: v for k, v in SLIP.items() if k != "receipt"}))
    for extra in ([], ["--dry-run"]):
        with pytest.raises(SystemExit) as out:
            cli.main(["aw26", "record", str(slip), *extra])
        assert out.value.code == 2 and "no receipt" in capsys.readouterr().err


def test_a_reading_run_may_not_upload(cfg, monkeypatch, tmp_path, capsys):
    from puntmore_hub import cli
    monkeypatch.setattr(cli, "settings", cfg)
    monkeypatch.setattr(aw26, "_open", lambda req, timeout: pytest.fail("sent"))
    monkeypatch.setenv("PUNTMORE_AW26_PHOTO_ID", "42")
    photo = tmp_path / "slip.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    with pytest.raises(SystemExit) as out:
        cli.main(["aw26", "upload", str(photo)])
    err = capsys.readouterr().err
    assert out.value.code == 2 and "reading photo 42" in err and "must not upload" in err


def test_a_reading_run_records_its_own_photo(cfg, monkeypatch, tmp_path, capsys):
    from puntmore_hub import cli
    monkeypatch.setattr(cli, "settings", cfg)
    sent = []

    def urlopen(req, timeout):
        sent.append(json.loads(req.data))
        return _Resp(200, {"id": 1})
    monkeypatch.setattr(aw26, "_open", urlopen)
    monkeypatch.setenv("PUNTMORE_AW26_PHOTO_ID", "42")
    slip = tmp_path / "slip.json"
    slip.write_text(json.dumps({k: v for k, v in SLIP.items() if k != "photo_id"}))
    cli.main(["aw26", "record", str(slip), "--dry-run"])             # no photo_id: filled in
    assert json.loads(capsys.readouterr().out)["dry_run"]["photo_id"] == 42 and sent == []
    cli.main(["aw26", "record", str(slip)])
    assert sent[0]["photo_id"] == 42
    slip.write_text(json.dumps({**SLIP, "photo_id": 42}))            # its own id: sent as it is
    cli.main(["aw26", "record", str(slip)])
    assert sent[1]["photo_id"] == 42 and len(sent) == 2


def test_a_reading_run_refuses_another_photos_slip(cfg, monkeypatch, tmp_path, capsys):
    from puntmore_hub import cli
    monkeypatch.setattr(cli, "settings", cfg)
    monkeypatch.setattr(aw26, "_open", lambda req, timeout: pytest.fail("sent"))
    monkeypatch.setenv("PUNTMORE_AW26_PHOTO_ID", "42")
    slip = tmp_path / "slip.json"
    slip.write_text(json.dumps(SLIP))                                 # photo_id 7
    for extra in ([], ["--dry-run"]):
        with pytest.raises(SystemExit) as out:
            cli.main(["aw26", "record", str(slip), *extra])
        err = capsys.readouterr().err
        assert out.value.code == 2 and "7" in err and "42" in err


def test_without_a_photo_id_record_and_upload_are_as_before(cfg, monkeypatch, tmp_path, capsys):
    from puntmore_hub import cli
    monkeypatch.setattr(cli, "settings", cfg)
    slip = tmp_path / "slip.json"
    slip.write_text(json.dumps({k: v for k, v in SLIP.items() if k != "photo_id"}))
    cli.main(["aw26", "record", str(slip), "--dry-run"])
    assert "photo_id" not in json.loads(capsys.readouterr().out)["dry_run"]
    monkeypatch.setattr(aw26, "_open", lambda req, timeout: _Resp(200, [{"id": 9}]))
    photo = tmp_path / "slip.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    cli.main(["aw26", "upload", str(photo)])
    assert json.loads(capsys.readouterr().out) == {"id": 9}
