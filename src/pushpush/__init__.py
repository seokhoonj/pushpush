"""Send a message to Telegram, Slack, or Discord from Python.

    from pushpush import send

    send("chip supply crash -- take a look", to="alerts")
    send(media="chart.png", caption="today", to="alerts")

`to` names a route you configured -- a service and a destination, saved under a
name -- so the call says what to send, not where every message lives. Omit it and
the default route is used. Anything the service could not carry -- media on a
text-only route, a markup it does not render, a file over its size limit -- raises
before the network is touched, so a bad send fails at the call site rather than
arriving as a silent non-delivery.

One service is one `Provider`; adding Kakao or ntfy is a subclass, not a new send
function. Text and a single media file are what a `send` carries; the service's
own reply comes back in the `SendReceipt`.
"""

from pathlib import Path

from pushpush.config import Config, default_config_path, load_config
from pushpush.credentials import (
    SECRET_ENV_VAR,
    default_credentials_path,
    delete_secret,
    resolve_secret,
    store_secret,
)
from pushpush.errors import (
    ConfigError,
    CredentialsError,
    InsecureCredentialsError,
    InvalidPushError,
    MarkupUnsupportedError,
    MediaError,
    MediaTooLargeError,
    MediaUnsupportedError,
    MissingSecretError,
    PushpushError,
    SendFailedError,
    UnknownProviderError,
    UnknownRouteError,
    UnsupportedError,
)
from pushpush.message import Markup, Push
from pushpush.provider import (
    DISCORD,
    SLACK,
    TELEGRAM,
    Provider,
    SendReceipt,
    resolve_provider,
)
from pushpush.route import Route

__all__ = [
    "DISCORD",
    "SECRET_ENV_VAR",
    "SLACK",
    "TELEGRAM",
    "Config",
    "ConfigError",
    "CredentialsError",
    "InsecureCredentialsError",
    "InvalidPushError",
    "Markup",
    "MarkupUnsupportedError",
    "MediaError",
    "MediaTooLargeError",
    "MediaUnsupportedError",
    "MissingSecretError",
    "Provider",
    "Push",
    "PushpushError",
    "Route",
    "SendFailedError",
    "SendReceipt",
    "UnknownProviderError",
    "UnknownRouteError",
    "UnsupportedError",
    "default_config_path",
    "default_credentials_path",
    "delete_secret",
    "load_config",
    "resolve_provider",
    "resolve_secret",
    "send",
    "store_secret",
]

__version__ = "0.1.1"


def send(
    text: str | None = None,
    *,
    to: str | None = None,
    media: Path | str | None = None,
    caption: str | None = None,
    markup: Markup = "plain",
    silent: bool = False,
    config: Config | None = None,
) -> SendReceipt:
    """Send one message on a configured route.

    Parameters
    ----------
    text
        The message body. Optional only when `media` is given -- a media send may
        carry a caption instead. At least one of the two must be present.
    to
        The route to send on. Defaults to the configuration's `default_route`.
    media
        A path to a file to send -- a chart, a report, a screenshot. The service
        chooses how to present it from the file's type (a photo inline, anything
        else as a document).
    caption
        A short line shown with `media`. Meaningless -- and refused -- without it;
        for a text-only send put the words in `text`.
    markup
        How the text is rendered: "plain" (literal), "markdown" (the service's own
        flavour), or "html" (Telegram only). A service that does not render the
        requested markup raises rather than sending it raw.
    silent
        Deliver without a notification sound on the recipient's device.
    config
        Loaded configuration. Read from disk when omitted.

    Returns
    -------
    SendReceipt
        Where the message went and the service's full reply, including its message
        id where the service returns one.

    Raises
    ------
    Everything below descends from `PushpushError` except the last, which is the
    standard library's own and passes through untranslated. A caller who must
    catch every way a send can fail writes
    `except (PushpushError, urllib.error.URLError)`.

    ConfigError, UnknownRouteError, UnknownProviderError
        The configuration is missing, or does not define the route (or its
        provider).
    InvalidPushError
        Nothing to send, a caption without media, or a route that needs a
        destination and has none. Also a `ValueError`.
    MediaError, MediaTooLargeError
        The media path is missing or not a file, or the file is over the service's
        limit.
    MediaUnsupportedError, MarkupUnsupportedError
        The route's service cannot carry the media, or cannot render the markup.
    MissingSecretError, InsecureCredentialsError, CredentialsError
        No secret is stored for the route, the credentials file is world-readable,
        or it is not readable JSON.
    SendFailedError
        The service was reached and refused the send -- a revoked token, a bad
        chat id, a message over a limit only the service enforces. Carries the
        service's own reason.
    urllib.error.URLError
        The network failed -- DNS, a refused connection, a timeout, an untrusted
        certificate. Not a `PushpushError`: it is the standard library's own, and
        wrapping it would say less than it already does.
    """
    config = config if config is not None else load_config()
    route = config.resolve_route(to)
    provider = route.provider
    push = Push(
        text    = text,
        media   = Path(media) if media is not None else None,
        caption = caption,
        markup  = markup,
        silent  = silent,
    )
    secret = resolve_secret(route)
    # Validate before the secret is spent on a request the service would refuse:
    # capability and destination checks raise here, with nothing sent.
    provider.validate(secret=secret, destination=route.destination, push=push)
    if push.media is not None:
        response = provider.send_media(
            secret=secret, destination=route.destination, push=push
        )
    else:
        response = provider.send_text(
            secret=secret, destination=route.destination, push=push
        )
    return SendReceipt(
        route      = route.name,
        provider   = provider.name,
        message_id = provider.message_id_of(response),
        response   = response,
    )
