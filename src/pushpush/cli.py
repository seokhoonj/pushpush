"""The `pushpush` command: a thin shell over `send()`, for scripts and cron.

It exposes only what a shell needs -- send a message, and list the configured
routes. Storing secrets and editing config stay terminal and library tasks on
purpose, so a token never lands in shell history.
"""

import argparse
import contextlib
import sys
import urllib.error
from typing import get_args

from pushpush import (
    InvalidPushError,
    Markup,
    PushpushError,
    __version__,
    load_config,
    send,
)


def main(argv: list[str] | None = None) -> int:
    """Parse `argv`, run a subcommand, and return the process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "routes":
            return _list_routes()
        return _send(args)
    except PushpushError as err:
        print(f"pushpush: {err}", file=sys.stderr)
        return 1
    except urllib.error.URLError as err:
        print(f"pushpush: the network failed: {err}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pushpush",
        description="Send a message to Telegram, Slack, or Discord.",
    )
    parser.add_argument(
        "--version", action="version", version=f"pushpush {__version__}"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    send_parser = subcommands.add_parser("send", help="send a message or a file")
    send_parser.add_argument("text", nargs="?", help="message text (or on stdin)")
    send_parser.add_argument("-t", "--to", help="route (default: the default route)")
    send_parser.add_argument("-m", "--media", help="path to a file to send")
    send_parser.add_argument("-c", "--caption", help="a line shown with the media")
    send_parser.add_argument("--markup", choices=get_args(Markup), default="plain")
    send_parser.add_argument(
        "-s", "--silent", action="store_true", help="deliver without a sound"
    )

    subcommands.add_parser("routes", help="list the configured routes")
    return parser


def _send(args: argparse.Namespace) -> int:
    text: str | None = args.text
    if text is None and not sys.stdin.isatty():
        try:
            text = sys.stdin.read().strip() or None
        except (OSError, UnicodeDecodeError) as err:
            raise InvalidPushError(
                f"cannot read message text from stdin: {err}"
            ) from err
    receipt = send(
        text,
        to      = args.to,
        media   = args.media,
        caption = args.caption,
        markup  = args.markup,
        silent  = args.silent,
    )
    # A reader that closed early (`... | head`) must not fail a delivered send.
    with contextlib.suppress(BrokenPipeError):
        print(receipt.provider, receipt.message_id or "")
    return 0


def _list_routes() -> int:
    config = load_config()
    print(f"default: {config.default_route}")
    for name, route in config.route_by_name.items():
        line = f"  {name}: {route.provider.name} {route.destination or ''}"
        print(line.rstrip())
    return 0
