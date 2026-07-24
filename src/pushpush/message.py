"""The message being sent: text, an optional file, and how to render it.

A `Push` is the content core -- what to say and what to attach -- with no idea
which service will carry it. The service (`provider`) reads a `Push` and frames
its own request from it. Validation that does not depend on the service lives
here, on construction, so a caption with nothing to caption or a media path that
does not exist fails at the call site, before any route or secret is looked up.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from pushpush.errors import InvalidPushError, MediaError

__all__ = ["Markup", "Push"]

# How the text is rendered by the service. "plain" is literal; "markdown" is each
# service's own flavour (Telegram legacy Markdown, Slack mrkdwn, Discord
# Markdown); "html" is Telegram's tag subset and no other service here. A provider
# refuses a markup it does not render -- see `Provider.supported_markups`.
Markup: TypeAlias = Literal["plain", "markdown", "html"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Push:
    """One message to send: text, an optional media file, and its rendering.

    At least one of `text` or `media` must be present -- there is no such thing as
    an empty send. `caption` labels media and is meaningless without it, so it is
    refused on a text-only push rather than dropped silently.

    Attributes
    ----------
    text
        The message body, or None when the send is media with only a caption.
    media
        A path to a file to send -- a chart, a report, a screenshot. Which upload
        the service uses (a photo versus a document) is the provider's choice from
        the file's media type.
    caption
        A short line shown with the media. Requires `media`.
    markup
        How the text (or caption) is rendered. See `Markup`.
    silent
        Deliver without a notification sound or vibration on the recipient's
        device, for messages that should arrive without interrupting.

    Raises
    ------
    InvalidPushError
        Neither `text` nor `media` is given, or `caption` is given without
        `media`. Also a `ValueError`.
    MediaError
        `media` is given but the path does not exist or is not a regular file.
    """

    text: str | None = None
    media: Path | None = None
    caption: str | None = None
    markup: Markup = "plain"
    silent: bool = False

    def __post_init__(self) -> None:
        # Whitespace-only text is nothing to say: catch it here so send("") fails
        # at the call site, not on the wire as a service refusal.
        has_words = self.text is not None and self.text.strip() != ""
        if not has_words and self.media is None:
            raise InvalidPushError(
                "a push needs text or media; both are absent, so there is nothing "
                "to send"
            )
        if self.caption is not None and self.media is None:
            raise InvalidPushError(
                "caption labels media, and this push has none; put the words in "
                "text= instead of caption="
            )
        if self.media is not None:
            self._check_media_readable()

    def _check_media_readable(self) -> None:
        """Confirm the media path is a file that exists, before any send begins.

        The provider will read the bytes at send time; catching a missing or
        non-file path here means the failure lands at construction with the path
        in hand, not mid-upload with a connection already open.
        """
        media = self.media
        assert media is not None  # narrowed by the caller; for the type checker
        if not media.exists():
            raise MediaError(f"no file to send at {media}")
        if not media.is_file():
            raise MediaError(f"media must be a regular file, but {media} is not")

    @property
    def caption_or_text(self) -> str | None:
        """The words that ride with the media: the caption, or the text.

        A media send may label the file with either field, and providers should
        not care which the caller used. Text-only sends do not go through here.
        """
        return self.caption if self.caption is not None else self.text
