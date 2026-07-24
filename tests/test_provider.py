"""Each provider frames its own request and reads its own answer.

These call the providers directly with a secret and destination, the way `send`
does once a route is resolved, so the request shaping and the reply reading are
tested per service without a config file in the way.
"""

import json

import pytest

from pushpush import (
    DISCORD,
    SLACK,
    TELEGRAM,
    InvalidPushError,
    MarkupUnsupportedError,
    MediaError,
    MediaTooLargeError,
    MediaUnsupportedError,
    Push,
    SendFailedError,
)
from pushpush.http import HTTPResponse
from pushpush.provider import read_media


def _png(tmp_path):
    path = tmp_path / "chart.png"
    path.write_bytes(b"\x89PNG\r\n")
    return path


def _pdf(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4")
    return path


# -- Telegram ---------------------------------------------------------------


def test_telegram_text_request(transport):
    TELEGRAM.send_text(secret="TOK", destination="123", push=Push(text="hi"))
    call = transport.last_json
    assert call.url == "https://api.telegram.org/botTOK/sendMessage"
    assert call.payload == {
        "chat_id": "123",
        "text": "hi",
        "disable_notification": False,
    }


def test_telegram_plain_sets_no_parse_mode(transport):
    TELEGRAM.send_text(secret="TOK", destination="1", push=Push(text="hi"))
    assert "parse_mode" not in transport.last_json.payload


def test_telegram_markdown_and_html_parse_modes(transport):
    TELEGRAM.send_text(
        secret="T", destination="1", push=Push(text="*x*", markup="markdown")
    )
    assert transport.last_json.payload["parse_mode"] == "Markdown"
    TELEGRAM.send_text(
        secret="T", destination="1", push=Push(text="<b>x</b>", markup="html")
    )
    assert transport.last_json.payload["parse_mode"] == "HTML"


def test_telegram_silent(transport):
    TELEGRAM.send_text(secret="T", destination="1", push=Push(text="q", silent=True))
    assert transport.last_json.payload["disable_notification"] is True


def test_telegram_returns_result_and_message_id(transport):
    response = TELEGRAM.send_text(secret="T", destination="1", push=Push(text="hi"))
    assert response == {"message_id": 42}
    assert TELEGRAM.message_id_of(response) == "42"


def test_telegram_refusal_raises_with_reason(transport):
    transport.json_reply = HTTPResponse(
        200, {"ok": False, "description": "chat not found"}, ""
    )
    with pytest.raises(SendFailedError, match="chat not found"):
        TELEGRAM.send_text(secret="T", destination="1", push=Push(text="hi"))


def test_telegram_photo_for_image(transport, tmp_path):
    TELEGRAM.send_media(secret="T", destination="1", push=Push(media=_png(tmp_path)))
    call = transport.last_multipart
    assert call.url.endswith("/sendPhoto")
    assert set(call.files) == {"photo"}
    assert call.files["photo"].mime_type == "image/png"


def test_telegram_document_for_non_image(transport, tmp_path):
    TELEGRAM.send_media(secret="T", destination="1", push=Push(media=_pdf(tmp_path)))
    call = transport.last_multipart
    assert call.url.endswith("/sendDocument")
    assert set(call.files) == {"document"}


def test_telegram_media_fields(transport, tmp_path):
    TELEGRAM.send_media(
        secret="T", destination="55",
        push=Push(media=_png(tmp_path), caption="today", silent=True),
    )
    fields = transport.last_multipart.fields
    assert fields["chat_id"] == "55"
    assert fields["caption"] == "today"
    assert fields["disable_notification"] == "true"


def test_telegram_needs_destination(transport):
    with pytest.raises(InvalidPushError, match="destination"):
        TELEGRAM.validate(secret="T", destination=None, push=Push(text="hi"))


# -- Discord ----------------------------------------------------------------

WEBHOOK = "https://discord.com/api/webhooks/1/abc"


def test_discord_appends_wait(transport):
    transport.json_reply = HTTPResponse(200, {"id": "9"}, "")
    DISCORD.send_text(secret=WEBHOOK, destination=None, push=Push(text="hi"))
    assert transport.last_json.url == f"{WEBHOOK}?wait=true"


def test_discord_content_and_silent_flag(transport):
    transport.json_reply = HTTPResponse(200, {"id": "9"}, "")
    DISCORD.send_text(
        secret=WEBHOOK, destination=None, push=Push(text="hi", silent=True)
    )
    assert transport.last_json.payload == {"content": "hi", "flags": 4096}


def test_discord_message_id(transport):
    transport.json_reply = HTTPResponse(200, {"id": "555"}, "")
    response = DISCORD.send_text(secret=WEBHOOK, destination=None, push=Push(text="hi"))
    assert DISCORD.message_id_of(response) == "555"


def test_discord_refusal_raises(transport):
    transport.json_reply = HTTPResponse(401, {"message": "Invalid Webhook Token"}, "")
    with pytest.raises(SendFailedError, match="Invalid Webhook Token"):
        DISCORD.send_text(secret=WEBHOOK, destination=None, push=Push(text="hi"))


def test_discord_media_multipart(transport, tmp_path):
    transport.multipart_reply = HTTPResponse(200, {"id": "7"}, "")
    DISCORD.send_media(
        secret=WEBHOOK, destination=None, push=Push(media=_png(tmp_path), caption="c")
    )
    call = transport.last_multipart
    assert set(call.files) == {"files[0]"}
    assert json.loads(call.fields["payload_json"]) == {"content": "c"}


def test_discord_media_too_large(transport, tmp_path, monkeypatch):
    monkeypatch.setattr(DISCORD, "max_media_bytes", 4)
    with pytest.raises(MediaTooLargeError):
        DISCORD.validate(
            secret=WEBHOOK, destination=None, push=Push(media=_png(tmp_path))
        )


# -- Slack ------------------------------------------------------------------

SLACK_WEBHOOK = "https://hooks.slack.com/services/T/B/x"
SLACK_BOT = "xoxb-123-abc"


def test_slack_webhook_text(transport):
    transport.json_reply = HTTPResponse(200, {}, "ok")
    SLACK.send_text(secret=SLACK_WEBHOOK, destination=None, push=Push(text="hi"))
    assert transport.last_json.url == SLACK_WEBHOOK
    assert transport.last_json.payload == {"text": "hi"}


def test_slack_webhook_failure(transport):
    transport.json_reply = HTTPResponse(200, {}, "no_service")
    with pytest.raises(SendFailedError, match="no_service"):
        SLACK.send_text(secret=SLACK_WEBHOOK, destination=None, push=Push(text="hi"))


def test_slack_bot_posts_with_channel_and_auth(transport):
    transport.json_reply = HTTPResponse(200, {"ok": True, "ts": "1.2"}, "")
    response = SLACK.send_text(
        secret=SLACK_BOT, destination="#alerts", push=Push(text="hi")
    )
    call = transport.last_json
    assert call.url == "https://slack.com/api/chat.postMessage"
    assert call.headers == {"Authorization": "Bearer xoxb-123-abc"}
    assert call.payload["channel"] == "#alerts"
    assert SLACK.message_id_of(response) == "1.2"


def test_slack_bot_mrkdwn_flag(transport):
    transport.json_reply = HTTPResponse(200, {"ok": True, "ts": "1"}, "")
    SLACK.send_text(
        secret=SLACK_BOT, destination="#a", push=Push(text="*x*", markup="markdown")
    )
    assert transport.last_json.payload["mrkdwn"] is True
    SLACK.send_text(
        secret=SLACK_BOT, destination="#a", push=Push(text="x", markup="plain")
    )
    assert transport.last_json.payload["mrkdwn"] is False


def test_slack_bot_error_raises(transport):
    transport.json_reply = HTTPResponse(
        200, {"ok": False, "error": "channel_not_found"}, ""
    )
    with pytest.raises(SendFailedError, match="channel_not_found"):
        SLACK.send_text(secret=SLACK_BOT, destination="#gone", push=Push(text="hi"))


def test_slack_bot_needs_channel(transport):
    with pytest.raises(InvalidPushError, match="channel"):
        SLACK.validate(secret=SLACK_BOT, destination=None, push=Push(text="hi"))


def test_slack_webhook_needs_no_destination(transport):
    SLACK.validate(secret=SLACK_WEBHOOK, destination=None, push=Push(text="hi"))


def test_slack_webhook_cannot_upload_a_file(tmp_path):
    with pytest.raises(MediaUnsupportedError):
        SLACK.validate(
            secret=SLACK_WEBHOOK, destination=None, push=Push(media=_png(tmp_path))
        )


def test_slack_bot_uploads_a_file_in_three_steps(transport, tmp_path):
    transport.multipart_reply_by_url = {
        "getUploadURLExternal": HTTPResponse(
            200,
            {"ok": True, "upload_url": "https://files.slack.com/upload/v1/x",
             "file_id": "F1"},
            "",
        ),
        "completeUploadExternal": HTTPResponse(
            200, {"ok": True, "files": [{"id": "F1"}]}, ""
        ),
    }
    SLACK.send_media(
        secret=SLACK_BOT,
        destination="C123",
        push=Push(media=_png(tmp_path), caption="chart"),
    )
    urls = [call.url for call in transport.multipart_calls]
    assert any("getUploadURLExternal" in url for url in urls)
    assert any("completeUploadExternal" in url for url in urls)
    assert len(transport.bytes_calls) == 1  # the file bytes went up once
    assert transport.bytes_calls[0].url == "https://files.slack.com/upload/v1/x"
    assert transport.bytes_calls[0].content == b"\x89PNG\r\n"
    reserve = next(c for c in transport.multipart_calls if "getUploadURL" in c.url)
    assert reserve.fields["filename"] == "chart.png"
    assert reserve.fields["length"] == str(len(b"\x89PNG\r\n"))
    complete = next(c for c in transport.multipart_calls if "completeUpload" in c.url)
    assert complete.fields["channel_id"] == "C123"
    assert complete.fields["initial_comment"] == "chart"


def test_slack_upload_url_rejection_raises(transport, tmp_path):
    transport.multipart_reply_by_url = {
        "getUploadURLExternal": HTTPResponse(
            200,
            {"ok": True, "upload_url": "https://files.slack.com/upload/v1/x",
             "file_id": "F1"},
            "",
        ),
    }
    transport.bytes_reply = HTTPResponse(413, {}, "")
    with pytest.raises(SendFailedError, match="file upload"):
        SLACK.send_media(
            secret=SLACK_BOT, destination="C1", push=Push(media=_png(tmp_path))
        )


def test_slack_reserve_without_a_target_raises_send_failed(transport, tmp_path):
    # ok:true but no upload_url/file_id must become a SendFailedError, not a
    # KeyError that escapes send()'s documented exception contract.
    transport.multipart_reply_by_url = {
        "getUploadURLExternal": HTTPResponse(200, {"ok": True}, ""),
    }
    with pytest.raises(SendFailedError, match="no target"):
        SLACK.send_media(
            secret=SLACK_BOT, destination="C1", push=Push(media=_png(tmp_path))
        )


def test_telegram_refuses_oversize_photo_before_upload(tmp_path):
    # An image goes via sendPhoto (10 MB cap), not sendDocument (50 MB); an 11 MB
    # PNG must be refused up front, not on the wire.
    big = tmp_path / "big.png"
    with open(big, "wb") as handle:
        handle.truncate(11 * 1024 * 1024)
    with pytest.raises(MediaTooLargeError):
        TELEGRAM.validate(secret="T", destination="1", push=Push(media=big))


def test_read_media_translates_a_read_failure(tmp_path):
    # A file deleted after the push was built raises MediaError, not a bare
    # OSError, so `send`'s `except PushpushError` stays total.
    missing = tmp_path / "gone.png"
    with pytest.raises(MediaError):
        read_media(missing)


def test_discord_non_url_secret_is_refused_without_leaking_it():
    # The secret is the URL Discord posts to; a non-URL value must be refused at
    # validate, and the secret must never appear in the exception message.
    bad_secret = "xoxb-should-not-be-here-999"
    with pytest.raises(InvalidPushError) as caught:
        DISCORD.validate(secret=bad_secret, destination=None, push=Push(text="hi"))
    assert bad_secret not in str(caught.value)


def test_text_only_provider_inherits_the_media_refusal(tmp_path):
    # A provider that declares no media support inherits the base send_media,
    # which refuses media even on a direct, unvalidated call.
    from pushpush.provider import Provider

    class _TextOnly(Provider):
        name = "textonly"
        supports_media = False
        needs_destination = False
        supported_markups = frozenset({"plain"})
        max_media_bytes = None

        def send_text(self, *, secret, destination, push):
            return {}

    with pytest.raises(MediaUnsupportedError):
        _TextOnly().send_media(
            secret="x", destination=None, push=Push(media=_png(tmp_path))
        )


# -- markup capability across providers -------------------------------------


def test_html_is_telegram_only():
    html = Push(text="x", markup="html")
    TELEGRAM.validate(secret="T", destination="1", push=html)
    with pytest.raises(MarkupUnsupportedError):
        SLACK.validate(secret=SLACK_WEBHOOK, destination=None, push=html)
    with pytest.raises(MarkupUnsupportedError):
        DISCORD.validate(secret=WEBHOOK, destination=None, push=html)
