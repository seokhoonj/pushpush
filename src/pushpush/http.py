"""The one place pushpush touches the network.

The messaging services all speak HTTP with a JSON or a multipart body, so the
whole transport is two functions over the standard library's `urllib`: one posts
JSON, one posts a file as multipart/form-data. Keeping them here, provider-blind,
is what lets a new provider be one file that frames a request rather than another
copy of the connection handling.

Neither function decides whether a send *succeeded*: a service can answer HTTP
200 with ``{"ok": false}`` (Telegram) or the plain word ``ok`` (a Slack webhook),
so what counts as success is the provider's to read off the reply. These return
the reply -- status, parsed JSON, and raw text -- and raise only when the network
itself fails, which is `urllib`'s own `URLError` passed through untranslated.

There is no third-party HTTP client on purpose: a notifier that never breaks on
someone else's release is worth more than the convenience `requests` would add,
and multipart is a dozen lines to frame by hand.
"""

import json
import secrets
import urllib.error
import urllib.request
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
from typing import Any, NamedTuple

__all__ = ["HTTPResponse", "MultipartFile", "post_json", "post_multipart"]

# Sent on every request so a service sees pushpush, not urllib's default
# "Python-urllib/x.y" -- which Discord's Cloudflare front rejects outright
# (error 1010). Read from the installed metadata; a bare name when run from a
# checkout that was never installed.
try:
    USER_AGENT = f"pushpush/{version('pushpush')}"
except PackageNotFoundError:
    USER_AGENT = "pushpush"

# Long enough that a send does not hang a script forever, short enough that a
# dead service surfaces as an error rather than a stall. Media uploads get more,
# because a photo over a slow uplink legitimately takes longer than a text POST.
JSON_TIMEOUT_SECONDS = 15
MULTIPART_TIMEOUT_SECONDS = 60


class HTTPResponse(NamedTuple):
    """A service's reply, as the caller needs to read it.

    Attributes
    ----------
    status
        The HTTP status code. A 2xx for a reply that arrived; the 4xx of a
        rejected send is here too, not raised, so the provider can read the
        reason out of `body`.
    body
        The parsed JSON reply, or an empty mapping when the service answered with
        no body (a Discord webhook returns 204 and nothing). A non-JSON body --
        a Slack webhook's plain ``ok`` -- lands in `text`, not here.
    text
        The raw reply text, always, for the services that do not answer in JSON.
    """

    status: int
    body: dict[str, Any]
    text: str


class MultipartFile(NamedTuple):
    """One file to upload: what to call it, its bytes, and its media type."""

    filename: str
    content: bytes
    mime_type: str


def post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = JSON_TIMEOUT_SECONDS,
) -> HTTPResponse:
    """POST `payload` as a JSON body and read the reply.

    Raises
    ------
    urllib.error.URLError
        The network failed -- DNS, a refused connection, a timeout, or a TLS
        certificate that did not verify. Passed through untranslated: it is the
        standard library's own and says more than a wrapper would.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    _add_headers(request, headers)
    return _read(request, timeout)


def post_multipart(
    url: str,
    *,
    fields: Mapping[str, str],
    files: Mapping[str, MultipartFile],
    headers: Mapping[str, str] | None = None,
    timeout: float = MULTIPART_TIMEOUT_SECONDS,
) -> HTTPResponse:
    """POST text `fields` and `files` as multipart/form-data and read the reply.

    Raises
    ------
    urllib.error.URLError
        As `post_json`.
    """
    boundary = f"----pushpush{secrets.token_hex(16)}"
    body = _multipart_body(boundary, fields=fields, files=files)
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    _add_headers(request, headers)
    return _read(request, timeout)


def _multipart_body(
    boundary: str,
    *,
    fields: Mapping[str, str],
    files: Mapping[str, MultipartFile],
) -> bytes:
    """Frame fields and files into a multipart/form-data body.

    Each part is CRLF-delimited and the whole is closed by the boundary with a
    trailing ``--``; this is the wire format `requests` builds, written out so
    the package carries no dependency to build it. Field values are UTF-8, so a
    caption in Korean rides through unchanged.
    """
    out = BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(value.encode("utf-8"))
        out.write(b"\r\n")
    for name, file in files.items():
        # A quote or newline in the filename would break or split the header it is
        # framed into, so neutralise both before interpolating.
        safe_filename = (
            file.filename.replace('"', "%22").replace("\r", "").replace("\n", "")
        )
        out.write(f"--{boundary}\r\n".encode())
        out.write(
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{safe_filename}"\r\n'.encode()
        )
        out.write(f"Content-Type: {file.mime_type}\r\n\r\n".encode())
        out.write(file.content)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue()


def _add_headers(
    request: urllib.request.Request, headers: Mapping[str, str] | None
) -> None:
    # Identify pushpush first, then let a provider that needs its own User-Agent
    # override it -- add_header normalises the name, so a later same-named header
    # replaces this one.
    request.add_header("User-Agent", USER_AGENT)
    for name, value in (headers or {}).items():
        request.add_header(name, value)


def _read(request: urllib.request.Request, timeout: float) -> HTTPResponse:
    """Send the request and turn the reply into an `HttpResponse`.

    An HTTP error status (4xx/5xx) is a reply, not a transport failure: the
    services answer a rejected send with a 4xx whose body holds the reason, so it
    is read like any other reply and handed back for the provider to interpret. A
    `URLError` that is *not* an `HTTPError` -- the connection never completed --
    propagates, because there is no reply to read.
    """
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _as_response(response.status, response.read())
    except urllib.error.HTTPError as error:
        # An HTTPError is also a readable response object holding a socket; close
        # it once its body is read rather than leaving it to the garbage collector.
        with error:
            return _as_response(error.code, error.read())


def _as_response(status: int, raw: bytes) -> HTTPResponse:
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        # A JSON array or scalar is a valid body but not one any provider here
        # reads by key; keep it reachable as text and leave `body` the empty
        # mapping the providers expect.
        parsed = {}
    return HTTPResponse(status=status, body=parsed, text=text)
