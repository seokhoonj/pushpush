"""The transport itself: multipart framing, reply parsing, and HTTP errors.

These are the one place the real `urllib` path is exercised -- with `urlopen`
replaced by a stub, so still no network -- because everything else fakes the two
functions this module defines.
"""

import email.message
import io
import json
import urllib.error
import urllib.request

import pytest

from pushpush.http import (
    MultipartFile,
    _multipart_body,
    post_json,
    post_multipart,
)


class FakeResponse(io.BytesIO):
    def __init__(self, status, raw):
        super().__init__(raw)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _stub_urlopen(monkeypatch, *, status=200, raw=b"", capture=None):
    def fake(request, timeout=None):
        if capture is not None:
            capture.append(request)
        return FakeResponse(status, raw)

    monkeypatch.setattr("pushpush.http.urllib.request.urlopen", fake)


def test_post_json_sends_json_body(monkeypatch):
    captured: list[urllib.request.Request] = []
    _stub_urlopen(monkeypatch, raw=b'{"ok": true}', capture=captured)
    response = post_json("https://x/y", {"a": 1})
    sent = captured[0].data
    assert isinstance(sent, bytes)
    assert json.loads(sent) == {"a": 1}
    assert captured[0].get_header("Content-type") == "application/json"
    assert response.status == 200
    assert response.body == {"ok": True}


def test_post_json_passes_headers(monkeypatch):
    captured: list[urllib.request.Request] = []
    _stub_urlopen(monkeypatch, raw=b"{}", capture=captured)
    post_json("https://x", {}, headers={"Authorization": "Bearer t"})
    assert captured[0].get_header("Authorization") == "Bearer t"


def test_sends_pushpush_user_agent(monkeypatch):
    # Not urllib's default "Python-urllib/..." -- Discord's Cloudflare front
    # rejects that (error 1010), so identifying pushpush is mandatory.
    captured: list[urllib.request.Request] = []
    _stub_urlopen(monkeypatch, raw=b"{}", capture=captured)
    post_json("https://x", {})
    user_agent = captured[0].get_header("User-agent")
    assert user_agent is not None and user_agent.startswith("pushpush")


def test_caller_can_override_user_agent(monkeypatch):
    captured: list[urllib.request.Request] = []
    _stub_urlopen(monkeypatch, raw=b"{}", capture=captured)
    post_json("https://x", {}, headers={"User-Agent": "custom/1"})
    assert captured[0].get_header("User-agent") == "custom/1"


def test_post_multipart_sends_default_user_agent(monkeypatch):
    # The Discord media path goes out through post_multipart, so it needs the same
    # identifying User-Agent the JSON path has -- urllib's default is rejected.
    captured: list[urllib.request.Request] = []
    _stub_urlopen(monkeypatch, raw=b"{}", capture=captured)
    file = MultipartFile(filename="a.png", content=b"x", mime_type="image/png")
    post_multipart("https://x", fields={}, files={"f": file})
    user_agent = captured[0].get_header("User-agent")
    assert user_agent is not None and user_agent.startswith("pushpush")


def test_post_multipart_caller_can_override_user_agent(monkeypatch):
    captured: list[urllib.request.Request] = []
    _stub_urlopen(monkeypatch, raw=b"{}", capture=captured)
    file = MultipartFile(filename="a.png", content=b"x", mime_type="image/png")
    post_multipart(
        "https://x", fields={}, files={"f": file}, headers={"User-Agent": "custom/1"}
    )
    assert captured[0].get_header("User-agent") == "custom/1"


def test_empty_body_is_empty_mapping(monkeypatch):
    _stub_urlopen(monkeypatch, status=204, raw=b"")
    response = post_json("https://x", {})
    assert response.body == {}
    assert response.text == ""


def test_non_json_body_lands_in_text(monkeypatch):
    _stub_urlopen(monkeypatch, raw=b"ok")
    response = post_json("https://x", {})
    assert response.body == {}
    assert response.text == "ok"


def test_http_error_is_a_reply_not_a_raise(monkeypatch):
    reply_fp = io.BytesIO(b'{"description": "bad token"}')

    def fake(request, timeout=None):
        raise urllib.error.HTTPError(
            url="https://x", code=401, msg="Unauthorized",
            hdrs=email.message.Message(),
            fp=reply_fp,
        )

    monkeypatch.setattr("pushpush.http.urllib.request.urlopen", fake)
    response = post_json("https://x", {})
    assert response.status == 401
    assert response.body == {"description": "bad token"}
    assert reply_fp.closed  # the error reply's socket is closed, not left to the GC


def test_url_error_propagates(monkeypatch):
    def fake(request, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr("pushpush.http.urllib.request.urlopen", fake)
    with pytest.raises(urllib.error.URLError):
        post_json("https://x", {})


def test_post_multipart_frames_fields_and_files(monkeypatch):
    captured: list[urllib.request.Request] = []
    _stub_urlopen(monkeypatch, raw=b"{}", capture=captured)
    post_multipart(
        "https://x",
        fields = {"caption": "안녕"},
        files  = {"photo": MultipartFile("c.png", b"BYTES", "image/png")},
    )
    content_type = captured[0].get_header("Content-type")
    assert content_type is not None
    assert content_type.startswith("multipart/form-data; boundary=")


def test_multipart_body_layout():
    body = _multipart_body(
        "BOUND",
        fields = {"caption": "hi"},
        files  = {"photo": MultipartFile("c.png", b"BYTES", "image/png")},
    )
    assert b'Content-Disposition: form-data; name="caption"' in body
    assert b'filename="c.png"' in body
    assert b"Content-Type: image/png" in body
    assert b"BYTES" in body
    assert body.endswith(b"--BOUND--\r\n")


def test_multipart_caption_is_utf8():
    body = _multipart_body(
        "B", fields={"caption": "안녕"}, files={}
    )
    assert "안녕".encode() in body
