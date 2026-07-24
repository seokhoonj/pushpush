"""The public send(): config to route to secret to receipt, end to end."""

import pytest

from pushpush import (
    MediaUnsupportedError,
    MissingSecretError,
    SendReceipt,
    send,
)
from pushpush.config import load_config
from tests.conftest import write_config


def _telegram_route(config_home, monkeypatch):
    write_config(
        config_home,
        '[routes.alerts]\nprovider = "telegram"\ndestination = "123"\n',
    )
    monkeypatch.setenv("PUSHPUSH_SECRET_ALERTS", "bot-token")


def test_send_returns_receipt(config_home, transport, monkeypatch):
    _telegram_route(config_home, monkeypatch)
    receipt = send("hello", to="alerts")
    assert isinstance(receipt, SendReceipt)
    assert receipt.route == "alerts"
    assert receipt.provider == "telegram"
    assert receipt.message_id == "42"


def test_send_uses_default_route(config_home, transport, monkeypatch):
    _telegram_route(config_home, monkeypatch)
    receipt = send("hello")  # no `to`; single route is the default
    assert receipt.route == "alerts"


def test_send_reaches_the_provider(config_home, transport, monkeypatch):
    _telegram_route(config_home, monkeypatch)
    send("hello", to="alerts")
    assert transport.last_json.url == "https://api.telegram.org/botbot-token/sendMessage"
    assert transport.last_json.payload["chat_id"] == "123"


def test_send_media_goes_multipart(config_home, transport, monkeypatch, tmp_path):
    _telegram_route(config_home, monkeypatch)
    chart = tmp_path / "c.png"
    chart.write_bytes(b"\x89PNG")
    send(media=str(chart), caption="today", to="alerts")
    assert transport.multipart_calls
    assert transport.last_multipart.fields["caption"] == "today"


def test_send_without_secret_is_reported(config_home, transport):
    write_config(
        config_home, '[routes.alerts]\nprovider = "telegram"\ndestination = "1"\n'
    )
    with pytest.raises(MissingSecretError):
        send("hello", to="alerts")


def test_send_receipt_response_is_read_only(config_home, transport, monkeypatch):
    _telegram_route(config_home, monkeypatch)
    receipt = send("hello", to="alerts")
    with pytest.raises(TypeError):
        receipt.response["x"] = 1  # type: ignore[index]


def test_media_on_slack_route_is_refused_before_send(
    config_home, transport, monkeypatch, tmp_path
):
    write_config(config_home, '[routes.team]\nprovider = "slack"\n')
    monkeypatch.setenv("PUSHPUSH_SECRET_TEAM", "https://hooks.slack.com/services/x")
    chart = tmp_path / "c.png"
    chart.write_bytes(b"\x89PNG")
    with pytest.raises(MediaUnsupportedError):
        send(media=str(chart), to="team")
    # Refused up front: nothing was sent.
    assert not transport.json_calls
    assert not transport.multipart_calls


def test_send_accepts_an_explicit_config(config_home, transport, monkeypatch):
    _telegram_route(config_home, monkeypatch)
    config = load_config()
    receipt = send("hi", to="alerts", config=config)
    assert receipt.provider == "telegram"
