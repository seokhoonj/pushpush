"""The `pushpush` command: argument parsing, stdin, routes, and exit codes."""

import io
import urllib.error

import pytest

import pushpush
import pushpush.cli as cli
from tests.conftest import write_config


def test_version_flag_prints_the_package_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == f"pushpush {pushpush.__version__}"


def _telegram_route(config_home, monkeypatch):
    write_config(
        config_home,
        '[routes.alerts]\nprovider = "telegram"\ndestination = "1"\n',
    )
    monkeypatch.setenv("PUSHPUSH_SECRET_ALERTS", "bot-token")


def test_send_text_prints_provider_and_exits_zero(
    config_home, transport, monkeypatch, capsys
):
    _telegram_route(config_home, monkeypatch)
    code = cli.main(["send", "hello", "--to", "alerts"])
    assert code == 0
    assert transport.last_json.payload["text"] == "hello"
    assert "telegram" in capsys.readouterr().out


def test_send_reads_text_from_stdin_when_absent(
    config_home, transport, monkeypatch
):
    _telegram_route(config_home, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
    code = cli.main(["send", "--to", "alerts"])
    assert code == 0
    assert transport.last_json.payload["text"] == "from stdin"


def test_send_passes_flags_through(config_home, transport, monkeypatch):
    _telegram_route(config_home, monkeypatch)
    cli.main(["send", "hi", "--to", "alerts", "--silent"])
    assert transport.last_json.payload["disable_notification"] is True


def test_routes_lists_the_configuration(
    config_home, transport, monkeypatch, capsys
):
    _telegram_route(config_home, monkeypatch)
    code = cli.main(["routes"])
    assert code == 0
    out = capsys.readouterr().out
    assert "default: alerts" in out
    assert "alerts: telegram" in out


def test_a_failed_send_reports_and_exits_one(config_home, transport, capsys):
    write_config(
        config_home, '[routes.alerts]\nprovider = "telegram"\ndestination = "1"\n'
    )  # no secret stored
    code = cli.main(["send", "hi", "--to", "alerts"])
    assert code == 1
    assert "pushpush:" in capsys.readouterr().err


def test_a_network_failure_exits_two(config_home, transport, monkeypatch, capsys):
    _telegram_route(config_home, monkeypatch)

    def fail(*args, **kwargs):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr("pushpush.provider.post_json", fail)
    assert cli.main(["send", "hi", "--to", "alerts"]) == 2
    assert "the network failed" in capsys.readouterr().err


def test_send_media_through_the_cli(config_home, transport, monkeypatch, tmp_path):
    _telegram_route(config_home, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # no piped text
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG")
    cli.main(["send", "--to", "alerts", "--media", str(png), "--caption", "cap"])
    assert transport.last_multipart.fields["caption"] == "cap"


def test_an_argument_wins_over_piped_stdin(config_home, transport, monkeypatch):
    _telegram_route(config_home, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO("piped"))
    cli.main(["send", "typed", "--to", "alerts"])
    assert transport.last_json.payload["text"] == "typed"


def test_no_text_and_a_tty_exits_one(config_home, transport, monkeypatch):
    _telegram_route(config_home, monkeypatch)

    class _Tty:
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", _Tty())
    assert cli.main(["send", "--to", "alerts"]) == 1  # InvalidPushError, no hang
