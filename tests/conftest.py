"""One fake transport, shared by every test that sends.

pushpush touches the network in exactly two functions -- `post_json` and
`post_multipart` in `pushpush.http` -- so the whole suite stays offline by
replacing those two where the providers call them. The fake records what it was
handed and returns a canned reply the test controls, which is enough to assert on
the request a provider frames and on how it reads each service's answer.

A real send goes through `dev/smoke_send.py`, outside the suite.
"""

import types

import pytest

from pushpush.http import HTTPResponse

# A Telegram-style success, the reply the fake returns unless a test sets another.
TELEGRAM_OK = HTTPResponse(
    status = 200,
    body   = {"ok": True, "result": {"message_id": 42}},
    text   = '{"ok": true, "result": {"message_id": 42}}',
)


class FakeTransport:
    """Records the requests the providers make, and answers with a set reply.

    Set `json_reply` / `multipart_reply` in a test to steer how the provider reads
    the answer -- a refusal, an empty webhook ack, a bot-token error. Read
    `json_calls` / `multipart_calls` back to assert on the request that was framed.
    """

    def __init__(self) -> None:
        self.json_calls: list[types.SimpleNamespace] = []
        self.multipart_calls: list[types.SimpleNamespace] = []
        self.bytes_calls: list[types.SimpleNamespace] = []
        self.json_reply = TELEGRAM_OK
        self.multipart_reply = TELEGRAM_OK
        self.bytes_reply = HTTPResponse(status=200, body={}, text="")
        # For a multi-call flow (Slack file upload) a test maps a URL fragment to
        # the reply that call should get; anything unmatched gets multipart_reply.
        self.multipart_reply_by_url: dict[str, HTTPResponse] = {}

    def post_json(self, url, payload, *, headers=None, timeout=None):
        self.json_calls.append(
            types.SimpleNamespace(url=url, payload=payload, headers=headers)
        )
        return self.json_reply

    def post_multipart(self, url, *, fields, files, headers=None, timeout=None):
        self.multipart_calls.append(
            types.SimpleNamespace(url=url, fields=fields, files=files, headers=headers)
        )
        for fragment, reply in self.multipart_reply_by_url.items():
            if fragment in url:
                return reply
        return self.multipart_reply

    def post_bytes(self, url, content, *, headers=None, timeout=None):
        self.bytes_calls.append(
            types.SimpleNamespace(url=url, content=content, headers=headers)
        )
        return self.bytes_reply

    @property
    def last_json(self) -> types.SimpleNamespace:
        return self.json_calls[-1]

    @property
    def last_multipart(self) -> types.SimpleNamespace:
        return self.multipart_calls[-1]


@pytest.fixture
def transport(monkeypatch):
    """Install one FakeTransport behind the two HTTP functions and hand it back."""
    fake = FakeTransport()
    monkeypatch.setattr("pushpush.provider.post_json", fake.post_json)
    monkeypatch.setattr("pushpush.provider.post_multipart", fake.post_multipart)
    monkeypatch.setattr("pushpush.provider.post_bytes", fake.post_bytes)
    return fake


@pytest.fixture
def config_dir(monkeypatch, tmp_path):
    """Point config and credentials at tmp, and clear any inherited secret.

    So a test neither reads nor writes the operator's real setup, and a
    `PUSHPUSH_SECRET` left in the developer's shell cannot leak into a run.
    """
    monkeypatch.setenv("PUSHPUSH_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("PUSHPUSH_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.delenv("PUSHPUSH_SECRET", raising=False)
    return tmp_path


def write_config(config_dir, toml_text: str) -> None:
    """Write a config.toml into the redirected config directory."""
    (config_dir / "config.toml").write_text(toml_text, encoding="utf-8")
