"""Reading routes off disk, and the errors that guard a malformed file."""

import pytest

from pushpush import TELEGRAM, ConfigError, UnknownProviderError, UnknownRouteError
from pushpush.config import load_config
from tests.conftest import write_config


def test_single_route_needs_no_default(config_home):
    write_config(config_home, '[routes.alerts]\nprovider = "telegram"\n')
    config = load_config()
    assert config.default_route == "alerts"
    assert config.resolve_route().provider is TELEGRAM


def test_destination_is_read(config_home):
    write_config(
        config_home,
        '[routes.alerts]\nprovider = "telegram"\ndestination = "123456"\n',
    )
    assert load_config().resolve_route("alerts").destination == "123456"


def test_routes_that_fold_to_one_env_var_are_refused(config_home):
    # "a-b" and "a_b" both fold to PUSHPUSH_SECRET_A_B, so one route's env secret
    # would answer for the other; load must refuse the ambiguity.
    write_config(
        config_home,
        'default_route = "a-b"\n'
        '[routes."a-b"]\nprovider = "telegram"\ndestination = "1"\n'
        '[routes."a_b"]\nprovider = "telegram"\ndestination = "2"\n',
    )
    with pytest.raises(ConfigError):
        load_config()


def test_config_path_that_is_a_directory_is_reported(config_home):
    (config_home / "config.toml").mkdir()  # read_text -> IsADirectoryError (OSError)
    with pytest.raises(ConfigError, match="cannot read configuration"):
        load_config()


def test_non_utf8_config_is_reported(config_home):
    (config_home / "config.toml").write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config()


def test_named_default_route(config_home):
    write_config(
        config_home,
        'default_route = "team"\n'
        '[routes.alerts]\nprovider = "telegram"\n'
        '[routes.team]\nprovider = "discord"\n',
    )
    config = load_config()
    assert config.default_route == "team"
    assert config.resolve_route().provider.name == "discord"


def test_several_routes_without_default_is_refused(config_home):
    write_config(
        config_home,
        '[routes.alerts]\nprovider = "telegram"\n'
        '[routes.team]\nprovider = "discord"\n',
    )
    with pytest.raises(ConfigError, match="default_route"):
        load_config()


def test_default_route_not_configured_is_refused(config_home):
    write_config(
        config_home,
        'default_route = "ghost"\n[routes.alerts]\nprovider = "telegram"\n',
    )
    with pytest.raises(ConfigError, match="ghost"):
        load_config()


def test_missing_file_is_config_error(config_home):
    with pytest.raises(ConfigError, match="no configuration"):
        load_config()


def test_invalid_toml_is_config_error(config_home):
    write_config(config_home, "this is = = not toml")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config()


def test_no_routes_is_config_error(config_home):
    write_config(config_home, 'default_route = "x"\n')
    with pytest.raises(ConfigError, match="no routes"):
        load_config()


def test_unknown_provider_is_refused(config_home):
    write_config(config_home, '[routes.alerts]\nprovider = "carrier-pigeon"\n')
    with pytest.raises(UnknownProviderError):
        load_config()


def test_missing_provider_key_is_refused(config_home):
    write_config(config_home, '[routes.alerts]\ndestination = "123"\n')
    with pytest.raises(ConfigError, match="missing 'provider'"):
        load_config()


def test_non_string_provider_is_refused(config_home):
    write_config(config_home, "[routes.alerts]\nprovider = 12345\n")
    with pytest.raises(ConfigError, match="must be a string"):
        load_config()


def test_non_string_destination_is_refused(config_home):
    write_config(
        config_home,
        "[routes.alerts]\nprovider = \"telegram\"\ndestination = 123\n",
    )
    with pytest.raises(ConfigError, match="destination must be a string"):
        load_config()


def test_unknown_route_lists_the_known(config_home):
    write_config(config_home, '[routes.alerts]\nprovider = "telegram"\n')
    with pytest.raises(UnknownRouteError, match="alerts"):
        load_config().resolve_route("missing")


def test_route_map_is_read_only(config_home):
    write_config(config_home, '[routes.alerts]\nprovider = "telegram"\n')
    config = load_config()
    with pytest.raises(TypeError):
        config.route_by_name["x"] = None  # type: ignore[index]
