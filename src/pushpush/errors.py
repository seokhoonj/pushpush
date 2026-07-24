"""Exceptions raised by pushpush.

Every error pushpush *defines* descends from `PushpushError`, so one `except`
catches everything this package judges: an unknown route, a missing secret, a
media file the chosen service will not carry, a request the service refused.

It does not catch everything a send can raise, and does not claim to. One kind
passes through untranslated, because it is the standard library's own and
inventing a wrapper for it would say less than it already does:

    urllib.error.URLError     the network did -- DNS did not answer, the
                              connection was refused or timed out, or the TLS
                              certificate did not verify. A subclass of OSError.

A `urllib.error.HTTPError` (also an OSError) is the one transport reply that is
translated, into `SendFailedError`, because the messaging services answer a
rejected send with a 4xx whose body carries the reason -- and that reason is the
thing worth surfacing. A caller who must catch every way a send can fail writes:

    except (PushpushError, urllib.error.URLError)
"""

__all__ = [
    "ConfigError",
    "CredentialsError",
    "InsecureCredentialsError",
    "InvalidPushError",
    "MarkupUnsupportedError",
    "MediaError",
    "MediaTooLargeError",
    "MediaUnsupportedError",
    "MissingSecretError",
    "PushpushError",
    "SendFailedError",
    "UnknownProviderError",
    "UnknownRouteError",
    "UnsupportedError",
]


class PushpushError(Exception):
    """Base class for every error pushpush raises."""


class ConfigError(PushpushError):
    """The configuration file is missing, malformed, or incomplete."""


class UnknownRouteError(ConfigError):
    """A route name was requested that the configuration does not define."""


class UnknownProviderError(ConfigError):
    """A route names a messaging service pushpush does not know how to reach."""


class CredentialsError(PushpushError):
    """Base class for problems with the stored secrets."""


class MissingSecretError(CredentialsError):
    """No secret is stored for the route, so nothing can be sent as it.

    A route's secret is its bot token (Telegram, Slack bot) or its webhook URL
    (Discord, Slack incoming webhook) -- the credential that lets the send speak
    for that destination.
    """


class InsecureCredentialsError(CredentialsError):
    """The credentials file is readable by someone other than its owner."""


class InvalidPushError(PushpushError, ValueError):
    """The message is not sendable as given.

    Raised when there is nothing to send (neither text nor media), when a caption
    is given without media to caption, or when a route needs a destination -- a
    Telegram chat id, a Slack channel -- that it does not have.

    Also a `ValueError`, which is what a bad argument has always been in Python
    and what callers already catch. Inheriting both keeps `except PushpushError`
    total without breaking anyone who reasonably wrote `except ValueError`.
    """


class MediaError(PushpushError):
    """The media file cannot be attached.

    Raised when the path is missing or is not a regular file. It is also the base
    class for the size refusal below, so one `except` covers both.
    """


class MediaTooLargeError(MediaError):
    """The media file is over the limit the chosen service accepts."""


class UnsupportedError(PushpushError):
    """The chosen service cannot do what the message asks of it.

    Base class for a capability the send needs and the provider lacks: media on a
    service that carries only text, or a markup flavour it does not render.
    """


class MediaUnsupportedError(UnsupportedError):
    """The chosen service cannot carry media through the configured route."""


class MarkupUnsupportedError(UnsupportedError):
    """The chosen service does not render the requested markup."""


class SendFailedError(PushpushError):
    """The service accepted the request but refused the send.

    Carries the service's own reason: Telegram's ``description``, Slack's
    ``error``, or the body of an HTTP 4xx. This is a refusal at the application
    layer -- the network reached the service and it said no -- as distinct from a
    `urllib.error.URLError`, where the network itself failed and the service was
    never reached.
    """
