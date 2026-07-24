"""The messaging services pushpush sends through, and how each frames a request.

A messaging service is reached its own way: a Telegram bot call, a Slack webhook,
a Discord multipart upload are three different HTTP shapes. So a provider carries
behaviour, not just facts -- it reads a `Push` and frames the request -- unlike a
setting where every service shares one transport and a provider need only hold the
facts that differ. Adding a service -- Kakao, Teams, ntfy -- is one more `Provider`
subclass and one line in `PROVIDER_BY_NAME`, not another send function.

The capability differences are declared, not discovered mid-send: a provider says
up front whether it carries media, which markups it renders, whether its route
needs a destination, and how large a file it takes. `validate` reads those before
anything touches the network, so a message the service could not carry fails at
the call site.
"""

import json
import mimetypes
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from pushpush.errors import (
    InvalidPushError,
    MarkupUnsupportedError,
    MediaError,
    MediaTooLargeError,
    MediaUnsupportedError,
    SendFailedError,
    UnknownProviderError,
)
from pushpush.http import (
    HTTPResponse,
    MultipartFile,
    post_bytes,
    post_json,
    post_multipart,
)
from pushpush.message import Markup, Push

__all__ = [
    "DISCORD",
    "SLACK",
    "TELEGRAM",
    "DiscordProvider",
    "Provider",
    "SendReceipt",
    "SlackProvider",
    "TelegramProvider",
    "read_media",
    "resolve_provider",
]

# Discord's SUPPRESS_NOTIFICATIONS message flag: delivered without pinging the
# recipient. https://discord.com/developers/docs/resources/message#message-object
DISCORD_SILENT_FLAG = 4096


@dataclass(frozen=True, slots=True, kw_only=True)
class SendReceipt:
    """What a completed send yields: where it went, and the service's own reply.

    A send either delivers or raises, so a receipt is proof of delivery, not a
    partial-success report -- that shape belongs to mail, where one message has
    many recipients; here one send has one destination. The service's whole reply is
    kept in `response` rather than picked apart, so nothing the service returned
    is thrown away -- a caller that wants Telegram's edit date or Slack's channel
    id reads it straight from there.

    Attributes
    ----------
    route
        The configured route the message went out on.
    provider
        The service that carried it (`"telegram"`, `"slack"`, `"discord"`).
    message_id
        The service's own id for the delivered message, when it returns one -- a
        Telegram ``message_id``, a Discord message id, a Slack ``ts``. None for a
        Slack incoming webhook (which acknowledges without identifying) and for a
        Slack bot-token file upload, whose completeUpload reply carries no ``ts``.
    response
        The service's full reply, read-only.
    """

    route: str
    provider: str
    message_id: str | None
    response: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "response", MappingProxyType(dict(self.response)))


class Provider(ABC):
    """A messaging service: what it can carry, and how it frames a send.

    Concrete providers are stateless singletons (`TELEGRAM`, `SLACK`, `DISCORD`) --
    they hold no per-message state, only the knowledge of the service's protocol.
    The credential and destination arrive at send time from the route, so one
    provider instance serves every route that uses that service.
    """

    name: ClassVar[str]
    supports_media: ClassVar[bool]
    needs_destination: ClassVar[bool]
    supported_markups: ClassVar[frozenset[Markup]]
    # None where the service publishes no fixed limit small enough to pre-check.
    max_media_bytes: ClassVar[int | None]

    def __repr__(self) -> str:
        return f"<Provider {self.name}>"

    def validate(self, *, secret: str, destination: str | None, push: Push) -> None:
        """Refuse a send this service cannot make, before the network is touched.

        Raises
        ------
        MarkupUnsupportedError
            The service does not render the requested markup.
        MediaUnsupportedError
            The push carries media and the service carries only text.
        MediaTooLargeError
            The media file is over the service's limit.
        InvalidPushError
            The route needs a destination the service was not given.
        """
        if push.markup not in self.supported_markups:
            renders = ", ".join(sorted(self.supported_markups))
            raise MarkupUnsupportedError(
                f"{self.name} does not render {push.markup!r} markup; it renders: "
                f"{renders}"
            )
        if push.media is not None:
            self._check_media(push.media)
        if self.needs_destination and not destination:
            raise InvalidPushError(
                f"the {self.name} route needs a destination and has none; add one "
                f"to the route in the configuration"
            )

    def _check_media(self, media: Path) -> None:
        if not self.supports_media:
            raise MediaUnsupportedError(
                f"{self.name} through pushpush carries text only, not media "
                f"({media.name}); send the file through Telegram or Discord, or "
                f"put a link in the text"
            )
        if self.max_media_bytes is not None:
            try:
                size = media.stat().st_size
            except OSError as err:
                raise MediaError(f"cannot read media at {media}: {err}") from err
            if size > self.max_media_bytes:
                raise MediaTooLargeError(
                    f"{media.name} is {size:,} bytes; {self.name} accepts up to "
                    f"{self.max_media_bytes:,}"
                )

    @abstractmethod
    def send_text(
        self, *, secret: str, destination: str | None, push: Push
    ) -> dict[str, Any]:
        """Send `push`'s text, returning the service's reply body."""

    def send_media(
        self, *, secret: str, destination: str | None, push: Push
    ) -> dict[str, Any]:
        """Send `push`'s media (captioned by its text), returning the reply body.

        A text-only service inherits this refusal; a media-capable one overrides
        it. `validate` already blocks media on a text-only service via
        `supports_media`, so reaching here means a direct, unvalidated call.
        """
        if self.supports_media:
            raise MediaUnsupportedError(
                f"{self.name} declares media support but does not implement "
                f"send_media"
            )
        raise MediaUnsupportedError(
            f"{self.name} through pushpush carries text only, not media"
        )

    def message_id_of(self, response: Mapping[str, Any]) -> str | None:
        """The service's id for a delivered message, if its reply carries one."""
        return None


class TelegramProvider(Provider):
    """Telegram Bot API: a bot token in the URL, a chat id as the destination.

    Text goes through ``sendMessage``; media through ``sendPhoto`` for an image
    and ``sendDocument`` for anything else, so a chart arrives inline and a
    spreadsheet as a file. The bot must have been started by the recipient (or be
    a member of the group/channel) before it can message them -- a bot cannot open
    a conversation, which is Telegram's rule, not this package's.
    """

    name = "telegram"
    supports_media = True
    needs_destination = True
    supported_markups = frozenset({"plain", "markdown", "html"})
    max_media_bytes = 50 * 1024 * 1024  # sendDocument upload ceiling
    # sendPhoto's own cap is lower, so an image is checked against this instead.
    PHOTO_MAX_BYTES: ClassVar[int] = 10 * 1024 * 1024

    API_BASE: ClassVar[str] = "https://api.telegram.org"

    def _check_media(self, media: Path) -> None:
        super()._check_media(media)  # the 50 MB sendDocument ceiling
        # An image goes out via sendPhoto, whose limit is lower; refuse an
        # oversize photo here rather than let it fail mid-upload.
        mime_type = mimetypes.guess_type(media.name)[0] or ""
        if mime_type.startswith("image/"):
            try:
                size = media.stat().st_size
            except OSError as err:
                raise MediaError(f"cannot read media at {media}: {err}") from err
            if size > self.PHOTO_MAX_BYTES:
                raise MediaTooLargeError(
                    f"{media.name} is {size:,} bytes; Telegram sends images up to "
                    f"{self.PHOTO_MAX_BYTES:,}"
                )

    def send_text(
        self, *, secret: str, destination: str | None, push: Push
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": destination,
            "text": push.text,
            "disable_notification": push.silent,
        }
        parse_mode = self._parse_mode(push.markup)
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        response = post_json(f"{self.API_BASE}/bot{secret}/sendMessage", payload)
        return self._result(response)

    def send_media(
        self, *, secret: str, destination: str | None, push: Push
    ) -> dict[str, Any]:
        assert push.media is not None  # media send; guaranteed by the caller
        media = read_media(push.media)
        is_photo = media.mime_type.startswith("image/")
        method = "sendPhoto" if is_photo else "sendDocument"
        part = "photo" if is_photo else "document"
        # Multipart field values are strings, so the JSON booleans of the text
        # path become "true"/"false" here -- Telegram reads either form.
        fields = {
            "chat_id": str(destination),
            "disable_notification": "true" if push.silent else "false",
        }
        if push.caption_or_text is not None:
            fields["caption"] = push.caption_or_text
        parse_mode = self._parse_mode(push.markup)
        if parse_mode is not None:
            fields["parse_mode"] = parse_mode
        response = post_multipart(
            f"{self.API_BASE}/bot{secret}/{method}",
            fields = fields,
            files  = {part: media},
        )
        return self._result(response)

    def message_id_of(self, response: Mapping[str, Any]) -> str | None:
        message_id = response.get("message_id")
        return None if message_id is None else str(message_id)

    def _result(self, response: HTTPResponse) -> dict[str, Any]:
        # Telegram answers {"ok": true, "result": {...}} on success and
        # {"ok": false, "description": ...} on refusal -- the latter with a 200 as
        # often as a 4xx, so the ok flag, not the status, is what decides.
        if not response.body.get("ok"):
            reason = (
                response.body.get("description")
                or response.text
                or f"HTTP {response.status}"
            )
            raise SendFailedError(f"Telegram refused the send: {reason}")
        result = response.body.get("result", {})
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _parse_mode(markup: Markup) -> str | None:
        # Legacy "Markdown" over "MarkdownV2" on purpose: V2 demands escaping a
        # long list of punctuation, so an unescaped ticker like BRK_B or a bare
        # "." would make V2 reject the whole message. Legacy is lenient enough to
        # send a hand-written line as written.
        return {"plain": None, "markdown": "Markdown", "html": "HTML"}[markup]


class DiscordProvider(Provider):
    """Discord incoming webhook: the webhook URL is the whole credential.

    The URL both authenticates and names the channel, so a Discord route needs no
    destination. Text posts as JSON; media as multipart with the file in
    ``files[0]`` and any caption in ``payload_json``. ``?wait=true`` is appended
    so Discord answers with the created message -- and its id -- instead of an
    empty 204.
    """

    name = "discord"
    supports_media = True
    needs_destination = False
    supported_markups = frozenset({"plain", "markdown"})
    # The unboosted webhook upload cap; boosted servers allow more, but pre-checking
    # against the floor keeps an oversize file from failing halfway up the wire.
    max_media_bytes = 8 * 1024 * 1024

    def validate(self, *, secret: str, destination: str | None, push: Push) -> None:
        super().validate(secret=secret, destination=destination, push=push)
        # The secret IS the URL this posts to. A non-URL value (a bot token pasted
        # into a Discord route) would surface as a urllib ValueError carrying the
        # secret in its message -- refuse it here, without echoing the secret.
        if not secret.startswith(("https://", "http://")):
            raise InvalidPushError(
                "the discord route's secret must be the webhook URL "
                "(https://discord.com/api/webhooks/...)"
            )

    def send_text(
        self, *, secret: str, destination: str | None, push: Push
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": push.text}
        if push.silent:
            payload["flags"] = DISCORD_SILENT_FLAG
        response = post_json(_with_wait(secret), payload)
        return self._result(response)

    def send_media(
        self, *, secret: str, destination: str | None, push: Push
    ) -> dict[str, Any]:
        assert push.media is not None  # media send; guaranteed by the caller
        media = read_media(push.media)
        content: dict[str, Any] = {}
        if push.caption_or_text is not None:
            content["content"] = push.caption_or_text
        if push.silent:
            content["flags"] = DISCORD_SILENT_FLAG
        response = post_multipart(
            _with_wait(secret),
            fields = {"payload_json": json.dumps(content)},
            files  = {"files[0]": media},
        )
        return self._result(response)

    def message_id_of(self, response: Mapping[str, Any]) -> str | None:
        message_id = response.get("id")
        return None if message_id is None else str(message_id)

    def _result(self, response: HTTPResponse) -> dict[str, Any]:
        if response.status >= 400:
            reason = (
                response.body.get("message")
                or response.text
                or f"HTTP {response.status}"
            )
            raise SendFailedError(f"Discord refused the send: {reason}")
        return response.body


class SlackProvider(Provider):
    """Slack, in either of its two shapes, told apart by the secret.

    An incoming-webhook URL (``https://hooks.slack.com/...``) posts text to the
    channel baked into the URL and needs no destination. A bot token (``xoxb-...``)
    posts through ``chat.postMessage`` and must be told a channel, which is the
    route's destination.

    A file goes only through a bot token -- an incoming webhook has no upload at
    all -- and takes three calls: reserve an upload URL (``getUploadURLExternal``),
    POST the bytes to it, then share it into the channel (``completeUploadExternal``).
    The bot needs the ``files:write`` scope, and a file's ``destination`` must be a
    channel id (``C…``), which is what ``completeUploadExternal`` accepts. So a
    media push on a webhook route is refused, and one on a bot route is uploaded.

    Slack has no per-message quiet-delivery control, so a push's `silent` flag has
    no effect here (Telegram and Discord honour it).
    """

    name = "slack"
    supports_media = True  # bot token only; a webhook is refused in validate
    needs_destination = False  # webhook mode; bot mode is checked in validate
    supported_markups = frozenset({"plain", "markdown"})
    max_media_bytes = None  # Slack enforces its own workspace limit

    POST_MESSAGE_URL: ClassVar[str] = "https://slack.com/api/chat.postMessage"
    GET_UPLOAD_URL: ClassVar[str] = (
        "https://slack.com/api/files.getUploadURLExternal"
    )
    COMPLETE_UPLOAD_URL: ClassVar[str] = (
        "https://slack.com/api/files.completeUploadExternal"
    )

    def validate(self, *, secret: str, destination: str | None, push: Push) -> None:
        super().validate(secret=secret, destination=destination, push=push)
        # The two mode-dependent rules the base class cannot make -- it does not
        # read the secret. A webhook cannot upload a file; a bot token can, but
        # (like chat.postMessage) must be told which channel.
        if _is_slack_webhook(secret):
            if push.media is not None:
                raise MediaUnsupportedError(
                    "a Slack incoming webhook cannot upload a file; use a "
                    "bot-token route, or send the file via Telegram or Discord"
                )
        elif not destination:
            raise InvalidPushError(
                "a Slack bot-token route needs a destination channel (e.g. "
                'destination = "#alerts"); chat.postMessage must be told where '
                "to post"
            )

    def send_text(
        self, *, secret: str, destination: str | None, push: Push
    ) -> dict[str, Any]:
        if _is_slack_webhook(secret):
            return self._webhook_result(post_json(secret, {"text": push.text}))
        payload = {
            "channel": destination,
            "text": push.text,
            "mrkdwn": push.markup == "markdown",
        }
        response = post_json(
            self.POST_MESSAGE_URL,
            payload,
            headers = {"Authorization": f"Bearer {secret}"},
        )
        return self._api_result(response)

    def send_media(
        self, *, secret: str, destination: str | None, push: Push
    ) -> dict[str, Any]:
        """Upload a file to Slack via the three-call external-upload flow.

        A webhook media send is refused in `validate`, so the secret here is a bot
        token: reserve an upload URL (``files.getUploadURLExternal``), POST the
        bytes to it, then share it into the channel (``files.completeUploadExternal``).

        Raises
        ------
        SendFailedError
            Slack refuses the reservation, names no upload target, answers the byte
            upload with a 4xx/5xx, or rejects the completeUpload.
        """
        assert push.media is not None  # media send; guaranteed by the caller
        media = read_media(push.media)
        auth = {"Authorization": f"Bearer {secret}"}
        # 1. Reserve an upload URL for a file of this name and size.
        reserved = self._api_result(post_multipart(
            self.GET_UPLOAD_URL,
            fields  = {"filename": media.filename, "length": str(len(media.content))},
            files   = {},
            headers = auth,
        ))
        upload_url = reserved.get("upload_url")
        file_id = reserved.get("file_id")
        if not (isinstance(upload_url, str) and isinstance(file_id, str)):
            # ok:true but no target -- translate, rather than let a KeyError escape
            # send()'s documented (PushpushError, urllib.error.URLError) contract.
            raise SendFailedError(
                f"Slack accepted the upload reservation but named no target; "
                f"reply keys: {sorted(reserved)}"
            )
        # 2. POST the bytes to the reserved URL; it authenticates itself, no header.
        upload_response = post_bytes(upload_url, media.content)
        if upload_response.status >= 400:
            raise SendFailedError(
                f"Slack refused the file upload: HTTP {upload_response.status}"
            )
        # 3. Share the file into the channel, with any caption as its comment.
        fields = {
            "files": json.dumps([{"id": file_id, "title": media.filename}]),
            "channel_id": str(destination),
        }
        if push.caption_or_text is not None:
            fields["initial_comment"] = push.caption_or_text
        return self._api_result(post_multipart(
            self.COMPLETE_UPLOAD_URL, fields=fields, files={}, headers=auth
        ))

    def message_id_of(self, response: Mapping[str, Any]) -> str | None:
        timestamp = response.get("ts")
        return None if timestamp is None else str(timestamp)

    def _webhook_result(self, response: HTTPResponse) -> dict[str, Any]:
        # A Slack incoming webhook answers 200 with the literal text "ok" and no
        # JSON, so there is no message id to return -- only success to confirm.
        if response.status >= 400 or response.text.strip().lower() != "ok":
            reason = response.text or f"HTTP {response.status}"
            raise SendFailedError(f"Slack webhook refused the send: {reason}")
        return {}

    def _api_result(self, response: HTTPResponse) -> dict[str, Any]:
        # chat.postMessage answers 200 with {"ok": false, "error": ...} on a
        # logical failure (bad channel, revoked token), so the ok flag decides,
        # not the status.
        if not response.body.get("ok"):
            reason = (
                response.body.get("error")
                or response.text
                or f"HTTP {response.status}"
            )
            raise SendFailedError(f"Slack refused the send: {reason}")
        return response.body


TELEGRAM = TelegramProvider()
SLACK = SlackProvider()
DISCORD = DiscordProvider()

PROVIDER_BY_NAME: dict[str, Provider] = {
    provider.name: provider for provider in (TELEGRAM, SLACK, DISCORD)
}


def resolve_provider(name: str) -> Provider:
    """Look up a provider by its configuration key.

    Raises
    ------
    UnknownProviderError
        `name` is not a provider pushpush knows.
    """
    try:
        return PROVIDER_BY_NAME[name]
    except KeyError as err:
        known = ", ".join(sorted(PROVIDER_BY_NAME))
        raise UnknownProviderError(
            f"unknown provider {name!r}; pushpush knows: {known}"
        ) from err


def read_media(path: Path) -> MultipartFile:
    """Read a media file into the bytes-and-type triple an upload needs.

    The media type is guessed from the suffix, falling back to
    ``application/octet-stream`` -- the type that says "unknown bytes", which is
    what an unrecognised suffix means and what makes a service treat the upload as
    a plain file rather than mis-rendering it.

    Raises
    ------
    MediaError
        The file cannot be read -- deleted since the push was built, or no read
        permission. Translated so `send`'s ``except PushpushError`` stays total.
    """
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        content = path.read_bytes()
    except OSError as err:
        raise MediaError(f"cannot read media at {path}: {err}") from err
    return MultipartFile(
        filename  = path.name,
        content   = content,
        mime_type = mime_type,
    )


def _with_wait(webhook_url: str) -> str:
    """Append ``wait=true`` so Discord returns the created message, not a 204."""
    separator = "&" if "?" in webhook_url else "?"
    return f"{webhook_url}{separator}wait=true"


def _is_slack_webhook(secret: str) -> bool:
    """A Slack secret is a webhook URL when it is a URL; otherwise a bot token."""
    return secret.startswith("https://")
