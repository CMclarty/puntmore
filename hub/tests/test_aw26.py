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
