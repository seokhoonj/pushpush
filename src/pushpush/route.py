"""A configured place to send: which service, and where on it.

A route is pushpush's unit of address: both the identity and the destination in
one, because a chat is reached by naming it, not by naming a sender and a
recipient separately. Where the route's secret lives is a separate concern -- see
`credentials`.
"""

from dataclasses import dataclass

from pushpush.provider import Provider

__all__ = ["Route"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Route:
    """A named destination: the service that carries it, and where on that service.

    Attributes
    ----------
    name
        The key this route has in the configuration (`"alerts"`, `"trades"`), and
        the key its secret is stored under.
    provider
        The service that carries it, and how it frames a send.
    destination
        Where on the service the message lands, when the service needs telling: a
        Telegram chat id, a Slack channel. None for a service whose credential
        already names the destination -- a Discord or Slack webhook URL.
    """

    name: str
    provider: Provider
    destination: str | None = None
