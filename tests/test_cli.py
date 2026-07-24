"""The `pushpush` command: argument parsing, stdin, routes, and exit codes."""

import io

import pushpush.cli as cli
from tests.conftest import write_config


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
