"""Reading routes off disk, and the errors that guard a malformed file."""

from pathlib import Path

import pytest

from pushpush import TELEGRAM, ConfigError, UnknownProviderError, UnknownRouteError
from pushpush.config import config_dir, load_config
from tests.conftest import write_config


def test_single_route_needs_no_default(config_dir):
    write_config(config_dir, '[routes.alerts]\nprovider = "telegram"\n')
    config = load_config()
    assert config.default_route == "alerts"
    assert config.resolve_route().provider is TELEGRAM


def test_destination_is_read(config_dir):
    write_config(
        config_dir,
        '[routes.alerts]\nprovider = "telegram"\ndestination = "123456"\n',
    )
    assert load_config().resolve_route("alerts").destination == "123456"


def test_routes_that_fold_to_one_env_var_are_refused(config_dir):
    # "a-b" and "a_b" both fold to PUSHPUSH_SECRET_A_B, so one route's env secret
    # would answer for the other; load must refuse the ambiguity.
    write_config(
        config_dir,
        'default_route = "a-b"\n'
        '[routes."a-b"]\nprovider = "telegram"\ndestination = "1"\n'
        '[routes."a_b"]\nprovider = "telegram"\ndestination = "2"\n',
    )
    with pytest.raises(ConfigError):
        load_config()


def test_config_path_that_is_a_directory_is_reported(config_dir):
    (config_dir / "config.toml").mkdir()  # read_text -> IsADirectoryError (OSError)
    with pytest.raises(ConfigError, match="cannot read configuration"):
        load_config()


def test_non_utf8_config_is_reported(config_dir):
    (config_dir / "config.toml").write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config()


def test_named_default_route(config_dir):
    write_config(
        config_dir,
        'default_route = "team"\n'
        '[routes.alerts]\nprovider = "telegram"\n'
        '[routes.team]\nprovider = "discord"\n',
    )
    config = load_config()
    assert config.default_route == "team"
    assert config.resolve_route().provider.name == "discord"


def test_several_routes_without_default_is_refused(config_dir):
    write_config(
        config_dir,
        '[routes.alerts]\nprovider = "telegram"\n'
        '[routes.team]\nprovider = "discord"\n',
    )
    with pytest.raises(ConfigError, match="default_route"):
        load_config()


def test_default_route_not_configured_is_refused(config_dir):
    write_config(
        config_dir,
        'default_route = "ghost"\n[routes.alerts]\nprovider = "telegram"\n',
    )
    with pytest.raises(ConfigError, match="ghost"):
        load_config()


def test_missing_file_is_config_error(config_dir):
    with pytest.raises(ConfigError, match="no configuration"):
        load_config()


def test_invalid_toml_is_config_error(config_dir):
    write_config(config_dir, "this is = = not toml")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config()


def test_no_routes_is_config_error(config_dir):
    write_config(config_dir, 'default_route = "x"\n')
    with pytest.raises(ConfigError, match="no routes"):
        load_config()


def test_unknown_provider_is_refused(config_dir):
    write_config(config_dir, '[routes.alerts]\nprovider = "carrier-pigeon"\n')
    with pytest.raises(UnknownProviderError):
        load_config()


def test_missing_provider_key_is_refused(config_dir):
    write_config(config_dir, '[routes.alerts]\ndestination = "123"\n')
    with pytest.raises(ConfigError, match="missing 'provider'"):
        load_config()


def test_non_string_provider_is_refused(config_dir):
    write_config(config_dir, "[routes.alerts]\nprovider = 12345\n")
    with pytest.raises(ConfigError, match="must be a string"):
        load_config()


def test_non_string_destination_is_refused(config_dir):
    write_config(
        config_dir,
        "[routes.alerts]\nprovider = \"telegram\"\ndestination = 123\n",
    )
    with pytest.raises(ConfigError, match="destination must be a string"):
        load_config()


def test_unknown_route_lists_the_known(config_dir):
    write_config(config_dir, '[routes.alerts]\nprovider = "telegram"\n')
    with pytest.raises(UnknownRouteError, match="alerts"):
        load_config().resolve_route("missing")


def test_route_map_is_read_only(config_dir):
    write_config(config_dir, '[routes.alerts]\nprovider = "telegram"\n')
    config = load_config()
    with pytest.raises(TypeError):
        config.route_by_name["x"] = None  # type: ignore[index]


def test_config_dir_falls_back_to_dot_config(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_dir() == Path.home() / ".config" / "pushpush"


def test_config_dir_uses_absolute_xdg_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir() == tmp_path / "pushpush"


def test_config_dir_expands_a_tilde_in_xdg_home(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "~/somewhere")
    assert config_dir() == Path.home() / "somewhere" / "pushpush"


def test_config_dir_ignores_a_relative_xdg_home(monkeypatch):
    # The XDG spec requires a relative value be ignored; using it would resolve
    # against the working directory and split a cron run from an interactive one.
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    assert config_dir() == Path.home() / ".config" / "pushpush"


def test_config_dir_ignores_a_blank_xdg_home(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "   ")
    assert config_dir() == Path.home() / ".config" / "pushpush"
