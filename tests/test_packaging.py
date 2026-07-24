"""The public surface is importable and complete."""

import importlib

import pushpush


def test_version_is_present():
    assert isinstance(pushpush.__version__, str)
    assert pushpush.__version__


def test_all_names_are_importable():
    for name in pushpush.__all__:
        assert hasattr(pushpush, name), name


def test_send_is_exported():
    assert callable(pushpush.send)


def test_providers_are_exported():
    assert {pushpush.TELEGRAM.name, pushpush.SLACK.name, pushpush.DISCORD.name} == {
        "telegram",
        "slack",
        "discord",
    }


def test_every_error_descends_from_pushpush_error():
    for name in pushpush.__all__:
        exported = getattr(pushpush, name)
        if isinstance(exported, type) and name.endswith("Error"):
            assert issubclass(exported, pushpush.PushpushError), name


def test_submodules_import_clean():
    for module in (
        "config",
        "credentials",
        "errors",
        "http",
        "message",
        "provider",
        "route",
    ):
        importlib.import_module(f"pushpush.{module}")
